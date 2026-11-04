"""Exact binary attachments and source text for the active workspace."""
from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
from uuid import uuid4

from . import core, hub

MAX_FILE = 8 * 1024 * 1024
MAX_TEXT = 900 * 1024
MAX_JSON_DEPTH = 32
MAX_EXTRACT_OUTPUT = 6 * MAX_TEXT + 4096
_extractors = threading.BoundedSemaphore(2)
MIMES = {'.txt': 'text/plain', '.md': 'text/markdown', '.csv': 'text/csv', '.json': 'application/json', '.pdf': 'application/pdf', '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}
FIELDS = 'f.id,f.room_id,f.filename,f.mime,f.bytes,f.sha256,f.source_id,f.extraction_status,f.warning,f.created_at,s.revoked_at'


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS hub_files (
        id TEXT PRIMARY KEY,room_id TEXT NOT NULL REFERENCES hub_rooms(id),filename TEXT NOT NULL,
        mime TEXT NOT NULL,bytes INTEGER NOT NULL,sha256 TEXT NOT NULL,source_id TEXT NOT NULL REFERENCES hub_sources(id),
        extraction_status TEXT NOT NULL,warning TEXT NOT NULL,created_at TEXT NOT NULL,content BLOB NOT NULL
    )''')


def _metadata(row):
    result = {key: row[key] for key in ('id', 'room_id', 'filename', 'mime', 'bytes', 'sha256', 'source_id', 'extraction_status', 'warning', 'created_at', 'revoked_at')}
    result['download_url'] = f'/api/hub/files/{row["id"]}/download'
    return result


def for_room(conn, room_id):
    return [_metadata(row) for row in conn.execute(f'SELECT {FIELDS} FROM hub_files f JOIN hub_sources s ON s.id=f.source_id WHERE f.room_id=? ORDER BY f.created_at', (room_id,))]


def list_files(room_id):
    with contextlib.closing(hub._db()) as conn:
        hub._find(conn, 'hub_rooms', room_id, '会话')
        return {'files': for_room(conn, room_id)}


def get_file(file_id, with_bytes=False):
    if not isinstance(file_id, str) or not file_id or len(file_id) > 128:
        raise hub.HubError('附件不存在', 404)
    with contextlib.closing(hub._db()) as conn:
        selected = FIELDS + (',f.content' if with_bytes else '')
        row = conn.execute(f'SELECT {selected} FROM hub_files f JOIN hub_sources s ON s.id=f.source_id WHERE f.id=?', (file_id,)).fetchone()
        if not row:
            raise hub.HubError('附件不存在', 404)
        metadata = _metadata(row)
        if with_bytes:
            if row['revoked_at']:
                raise hub.HubError('附件已撤销', 410)
            return metadata, bytes(row['content'])
        return metadata


def _failed_extraction():
    return {'text': '', 'status': 'failed', 'warning': '文字提取失败、繁忙或超出处理限制；原件完整保留'}


def _validated_extraction(value):
    if not isinstance(value, dict) or value.get('status') not in ('extracted', 'partial', 'no_text', 'encrypted', 'failed'):
        raise ValueError('invalid extraction response')
    text, warning = value.get('text'), value.get('warning', '')
    if not isinstance(text, str) or not isinstance(warning, str) or len(warning) > 1000 or len(text.encode('utf-8')) > MAX_TEXT:
        raise ValueError('invalid extraction size')
    return {'text': text, 'status': value['status'], 'warning': warning}


def _extract(raw, extension):
    if extension in ('.png', '.jpg', '.jpeg', '.webp'):
        valid = raw.startswith(b'\x89PNG\r\n\x1a\n') if extension == '.png' else raw.startswith(b'\xff\xd8\xff') if extension in ('.jpg', '.jpeg') else raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'
        if not valid:
            raise hub.HubError('图片格式与文件扩展名不匹配')
        return {'text': '', 'status': 'image_only', 'warning': '图片原件已保存，未执行 OCR'}
    if extension == '.pdf' and not raw.startswith(b'%PDF-'):
        raise hub.HubError('PDF 文件格式无效')
    if extension == '.docx' and not raw.startswith(b'PK'):
        raise hub.HubError('DOCX 文件格式无效')
    if extension in ('.txt', '.md', '.csv', '.json'):
        try:
            text = raw.decode('utf-8-sig')
            if '\x00' in text:
                raise ValueError
            if extension == '.json':
                value = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
                # Decoder recursion limits vary across Python versions. Enforce
                # our own document bound without recursively walking user data.
                pending = [(iter((value,)), 0)]
                while pending:
                    children, depth = pending[-1]
                    try:
                        item = next(children)
                    except StopIteration:
                        pending.pop()
                        continue
                    if isinstance(item, (dict, list)):
                        if depth >= MAX_JSON_DEPTH:
                            raise ValueError('JSON nesting exceeds document limit')
                        children = item.values() if isinstance(item, dict) else item
                        pending.append((iter(children), depth + 1))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise hub.HubError('请上传有效的 UTF-8 文本文件') from exc
        if not text.strip():
            return {'text': '', 'status': 'no_text', 'warning': '文本文件中没有可用正文'}
        limit = MAX_TEXT
        encoded = text.encode('utf-8')
        return {'text': encoded[:limit].decode('utf-8', errors='ignore'), 'status': 'partial' if len(encoded) > limit else 'extracted', 'warning': '文字达到提取上限，原件完整保留' if len(encoded) > limit else ''}
    if not _extractors.acquire(blocking=False):
        raise hub.HubError('文档提取正在处理其他文件，请稍后重试', 503)
    try:
        with tempfile.TemporaryDirectory(prefix='workspace-document-') as temp:
            path = Path(temp) / ('source' + extension)
            output = Path(temp) / 'extracted.json'
            path.write_bytes(raw)
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with output.open('wb') as stdout:
                result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'studio.file_extract', str(path), extension], cwd=core.ROOT, stdin=subprocess.DEVNULL, stdout=stdout, stderr=subprocess.DEVNULL, timeout=20, **flags)
            if result.returncode or output.stat().st_size > MAX_EXTRACT_OUTPUT:
                raise ValueError('extraction failed')
            return _validated_extraction(json.loads(output.read_bytes()))
    except (ValueError, UnicodeError, RecursionError, OSError, MemoryError, subprocess.TimeoutExpired):
        return _failed_extraction()
    finally:
        _extractors.release()


def add_file(room_id, filename, data_base64):
    filename = hub._text(filename, '文件名', 160)
    if re.search(r'[\x00-\x1f\x7f/\\:]', filename):
        raise hub.HubError('文件名不能包含路径或控制字符')
    extension = Path(filename).suffix.lower()
    if extension not in MIMES:
        raise hub.HubError('支持 TXT、MD、CSV、JSON、PDF、DOCX 和 PNG/JPG/WebP')
    if not isinstance(data_base64, str) or len(data_base64) > (MAX_FILE * 4 // 3) + 8:
        raise hub.HubError('附件不能超过 8 MB')
    try:
        raw = base64.b64decode(data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise hub.HubError('附件编码无效') from exc
    if not raw or len(raw) > MAX_FILE:
        raise hub.HubError('附件为空或超过 8 MB')
    hub.get_room(room_id)
    extracted = _extract(raw, extension)
    file_id, source_id = uuid4().hex, uuid4().hex
    sha = hashlib.sha256(raw).hexdigest()
    content = f'附件：{filename}\nSHA256：{sha}\n原件：/api/hub/files/{file_id}/download\n提取状态：{extracted["status"]}\n'
    if extracted.get('warning'):
        content += extracted['warning'] + '\n'
    content += '\n' + extracted['text']
    stamp = hub.now()
    actor = core.REQUEST_ACTOR.get()
    with hub._transaction() as conn:
        hub._find(conn, 'hub_rooms', room_id, '会话')
        if conn.execute('SELECT COUNT(*) AS n FROM hub_sources WHERE room_id=? AND revoked_at IS NULL', (room_id,)).fetchone()['n'] >= 100:
            raise hub.HubError('每个会话最多保留 100 份活动资料')
        conn.execute('INSERT INTO hub_sources (id,room_id,title,kind,content,sha256,created_by,origin,retrieval_status,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)', (source_id, room_id, filename, 'text', content, hashlib.sha256(content.encode()).hexdigest(), actor['user_id'] if actor else 'local_user', 'uploaded_file', 'provided', stamp))
        conn.execute('INSERT INTO hub_files VALUES (?,?,?,?,?,?,?,?,?,?,?)', (file_id, room_id, filename, MIMES[extension], len(raw), sha, source_id, extracted['status'], extracted.get('warning', ''), stamp, raw))
        hub._event(conn, room_id, None, None, 'file.added', f'已添加附件：{filename}')
    return get_file(file_id)


def revoke_file(file_id):
    file = get_file(file_id)
    hub.revoke_source(file['source_id'])
    return get_file(file_id)
