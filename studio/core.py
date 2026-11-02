"""Shared, local-first project service used by the website and agent tools."""
from __future__ import annotations

import base64
import contextlib
from contextvars import ContextVar
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from uuid import uuid4
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ENGINE = ROOT / 'engine'
sys.path.insert(0, str(ENGINE / 'scripts'))
import build as renderer  # noqa: E402
import qc  # noqa: E402

MAX_IMAGE = 8 * 1024 * 1024
MAX_BRIEF = 2 * 1024 * 1024
ID_RE = re.compile(r'^[a-f0-9]{32}$')
REQUEST_DATA_ROOT = ContextVar('studio_request_data_root', default=None)
REQUEST_ACTOR = ContextVar('studio_request_actor', default=None)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def base_data_root():
    return Path(os.environ.get('STUDIO_DATA_DIR', ROOT / '.studio-data')).expanduser().resolve()


def data_root():
    return REQUEST_DATA_ROOT.get() or base_data_root()


def db():
    root = data_root()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / 'studio.sqlite3', timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA busy_timeout=20000')
    conn.execute('''CREATE TABLE IF NOT EXISTS projects (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, website TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        brief_json TEXT, revision INTEGER NOT NULL DEFAULT 0,
        build_id TEXT, build_revision INTEGER
    )''')
    conn.execute('''CREATE TABLE IF NOT EXISTS project_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
        text TEXT NOT NULL, created_at TEXT NOT NULL,
        FOREIGN KEY(project_id) REFERENCES projects(id)
    )''')
    conn.commit()
    return conn


def validate_id(project_id):
    if not isinstance(project_id, str) or not ID_RE.fullmatch(project_id):
        raise ValueError('无效的项目 ID')
    return project_id


def project_dir(project_id):
    return data_root() / 'projects' / validate_id(project_id)


def project_record(row, include_brief=False):
    if row is None:
        raise ValueError('项目不存在')
    result = {key: row[key] for key in ('id', 'name', 'website', 'created_at', 'updated_at', 'revision', 'build_revision')}
    result['has_brief'] = bool(row['brief_json'])
    result['has_build'] = bool(row['build_id'])
    result['build_current'] = bool(row['build_id']) and row['build_revision'] == row['revision']
    result['status'] = ('待审核' if result['build_current'] else '有新修改' if result['has_build'] else '待生成' if result['has_brief'] else '待研究')
    if include_brief:
        result['brief'] = json.loads(row['brief_json']) if row['brief_json'] else None
        folder = project_dir(row['id']) / 'images'
        result['images'] = sorted(p.name for p in folder.iterdir() if p.is_file() and not p.is_symlink()) if folder.exists() else []
    return result


def create_project(name, website, request=''):
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise ValueError('项目名称须为 1–120 个字符')
    if not isinstance(website, str) or len(website) > 2048:
        raise ValueError('请输入有效的 HTTPS 企业网址')
    if not isinstance(request, str) or len(request) > 4000:
        raise ValueError('项目要求不能超过 4000 个字符')
    renderer.safe_url(website)
    project_id = uuid4().hex
    stamp = now()
    with contextlib.closing(db()) as conn:
        conn.execute('INSERT INTO projects (id,name,website,created_at,updated_at) VALUES (?,?,?,?,?)',
                     (project_id, name.strip(), website, stamp, stamp))
        if request.strip():
            conn.execute('INSERT INTO project_messages (project_id,text,created_at) VALUES (?,?,?)',
                         (project_id, request.strip(), stamp))
        conn.commit()
    return get_project(project_id)


def list_projects():
    with contextlib.closing(db()) as conn:
        rows = conn.execute('SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC').fetchall()
    return [project_record(row) for row in rows]


def get_project(project_id):
    validate_id(project_id)
    with contextlib.closing(db()) as conn:
        row = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
        result = project_record(row, include_brief=True)
        result['messages'] = [dict(m) for m in conn.execute(
            'SELECT text,created_at FROM project_messages WHERE project_id=? ORDER BY id', (project_id,)).fetchall()]
    return result


def add_instruction(project_id, text):
    validate_id(project_id)
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError('请输入不超过 4000 个字符的要求')
    stamp = now()
    with contextlib.closing(db()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('项目不存在')
        conn.execute('INSERT INTO project_messages (project_id,text,created_at) VALUES (?,?,?)',
                     (project_id, text.strip(), stamp))
        conn.execute('UPDATE projects SET revision=revision+1,updated_at=? WHERE id=?', (stamp, project_id))
        conn.commit()
    return get_project(project_id)


def save_brief(project_id, brief):
    validate_id(project_id)
    if not isinstance(brief, dict):
        raise ValueError('编辑简报必须是 JSON 对象')
    raw = json.dumps(brief, ensure_ascii=False)
    if len(raw.encode('utf-8')) > MAX_BRIEF:
        raise ValueError('简报超过 2 MB')
    try:
        renderer.verify_brief(brief)
        # Render too: this checks required fields in the selected chapter layouts.
        renderer.render(brief)
    except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        raise ValueError(f'简报内容不完整或无效：{exc}') from exc
    with contextlib.closing(db()) as conn:
        cursor = conn.execute('UPDATE projects SET brief_json=?,revision=revision+1,updated_at=? WHERE id=?',
                              (raw, now(), project_id))
        if not cursor.rowcount:
            raise ValueError('项目不存在')
        conn.commit()
    return get_project(project_id)


def save_image(project_id, filename, data_base64):
    validate_id(project_id)
    renderer.local_name(filename)
    if not isinstance(data_base64, str) or len(data_base64) > (MAX_IMAGE * 4 // 3 + 8):
        raise ValueError('图片超过 8 MB')
    try:
        data = base64.b64decode(data_base64, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError('图片编码无效') from exc
    if not data or len(data) > MAX_IMAGE:
        raise ValueError('图片为空或超过 8 MB')
    ext = Path(filename).suffix.lower()
    signatures = {
        '.jpg': data.startswith(b'\xff\xd8\xff'), '.jpeg': data.startswith(b'\xff\xd8\xff'),
        '.png': data.startswith(b'\x89PNG\r\n\x1a\n'),
        '.webp': data.startswith(b'RIFF') and data[8:12] == b'WEBP',
        '.avif': len(data) > 12 and data[4:8] == b'ftyp' and b'avif' in data[8:32],
    }
    if not signatures.get(ext, False):
        raise ValueError('图片内容与扩展名不匹配')
    folder = project_dir(project_id) / 'images'
    with contextlib.closing(db()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
            raise ValueError('项目不存在')
        folder.mkdir(parents=True, exist_ok=True)
        temp = folder / ('.upload-' + uuid4().hex)
        try:
            temp.write_bytes(data)
            os.replace(temp, folder / filename)
            conn.execute('UPDATE projects SET revision=revision+1,updated_at=? WHERE id=?', (now(), project_id))
            conn.commit()
        finally:
            temp.unlink(missing_ok=True)
    return {'filename': filename, 'bytes': len(data), 'project': get_project(project_id)}


def build_project(project_id):
    validate_id(project_id)
    project = project_dir(project_id)
    project.mkdir(parents=True, exist_ok=True)
    # Snapshot brief and images while holding the write lock so another agent cannot
    # replace an input halfway through the snapshot.
    with tempfile.TemporaryDirectory(prefix='build-', dir=project) as temp_name:
        temp = Path(temp_name)
        image_snapshot = temp / 'images'
        image_snapshot.mkdir()
        with contextlib.closing(db()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM projects WHERE id=?', (project_id,)).fetchone()
            if row is None:
                raise ValueError('项目不存在')
            if not row['brief_json']:
                raise ValueError('请先提供经过研究的编辑简报')
            brief = json.loads(row['brief_json'])
            revision = row['revision']
            for item in brief['images']:
                name = renderer.local_name(item.get('file'))
                image = project / 'images' / name
                if image.is_symlink() or not image.is_file():
                    raise ValueError(f'缺少图片：{name}')
                shutil.copy2(image, image_snapshot / name)
            conn.commit()
        brief_path = temp / 'editorial.json'
        brief_path.write_text(json.dumps(brief, ensure_ascii=False), encoding='utf-8')
        output = temp / 'output'
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                renderer.main(['--brief', str(brief_path), '--out', str(output), '--assets', str(image_snapshot)])
            problems, counts, _ = qc.structural_checks(output)
        except (ValueError, KeyError, TypeError, OSError) as exc:
            raise ValueError(f'生成失败：{exc}') from exc
        warnings = [p for p in problems if p.startswith('JavaScript syntax check unavailable:')]
        errors = [p for p in problems if p not in warnings]
        if errors:
            raise ValueError('结构检查未通过：' + '; '.join(errors[:5]))
        build_id = uuid4().hex
        builds = project / 'builds'
        builds.mkdir(exist_ok=True)
        final = builds / build_id
        with contextlib.closing(db()) as conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT revision FROM projects WHERE id=?', (project_id,)).fetchone()
            if current is None or current['revision'] != revision:
                raise ValueError('生成期间资料发生变化，请重新生成')
            os.replace(output, final)
            conn.execute('UPDATE projects SET build_id=?,build_revision=?,updated_at=? WHERE id=?',
                         (build_id, revision, now(), project_id))
            conn.commit()
    return {'project': get_project(project_id), 'checks': counts, 'warnings': warnings,
            'preview_path': f'/preview/{project_id}/index.html',
            'download_path': f'/api/projects/{project_id}/download',
            'publication_status': 'private_review_only'}


def current_build(project_id):
    validate_id(project_id)
    with contextlib.closing(db()) as conn:
        row = conn.execute('SELECT build_id FROM projects WHERE id=?', (project_id,)).fetchone()
    if row is None or not row['build_id']:
        raise ValueError('尚无已生成的杂志')
    root = (project_dir(project_id) / 'builds' / validate_id(row['build_id'])).resolve()
    if not root.is_dir():
        raise ValueError('杂志文件不存在')
    return root


def preview_file(project_id, relative):
    root = current_build(project_id)
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise ValueError('文件不存在')
    return target


def download_zip(project_id):
    root = current_build(project_id)
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(root.rglob('*')):
            if item.is_file() and not item.is_symlink():
                archive.write(item, item.relative_to(root).as_posix())
    return out.getvalue()
