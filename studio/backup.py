"""Verified portable backups; restore never overwrites an existing data tree."""
from __future__ import annotations

import argparse
import contextlib
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import struct
import tempfile
import unicodedata
import zipfile

from . import core

MAX_ENTRIES = 50000
MAX_FILE_BYTES = 1024 * 1024 * 1024
MAX_TOTAL_BYTES = 5 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_DIRECTORY_BYTES = 32 * 1024 * 1024
CHUNK = 1024 * 1024
FORMAT = 'agent-workspace-backup'


class BackupError(ValueError):
    pass


def _link(path):
    return path.is_symlink() or (hasattr(path, 'is_junction') and path.is_junction())


def _safe_relative(value):
    if not isinstance(value, str) or not value or len(value) > 1200 or '\\' in value or value.startswith('/'):
        raise BackupError('备份中存在无效路径')
    parts = value.split('/')
    reserved = re.compile(r'^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)', re.IGNORECASE)
    for part in parts:
        if part in ('', '.', '..') or part.endswith((' ', '.')) or any(ord(char) < 32 or char in '<>:"|?*' for char in part) or reserved.match(part) or unicodedata.normalize('NFC', part) != part:
            raise BackupError('备份中存在不安全或不可移植的路径')
    return PurePosixPath(value)


def _inventory(root):
    files, directories = [], []
    if _link(root) or not root.is_dir():
        raise BackupError('数据目录不存在或是链接')
    for current, names, filenames in os.walk(root, followlinks=False):
        folder = Path(current)
        for name in sorted(names):
            item = folder / name
            if _link(item) or not stat.S_ISDIR(item.lstat().st_mode):
                raise BackupError('数据目录含符号链接、目录联接或特殊文件')
            relative = item.relative_to(root).as_posix()
            _safe_relative(relative)
            directories.append(relative)
        for name in sorted(filenames):
            item = folder / name
            if _link(item) or not stat.S_ISREG(item.lstat().st_mode):
                raise BackupError('数据目录含符号链接或特殊文件')
            relative = item.relative_to(root).as_posix()
            _safe_relative(relative)
            files.append((relative, item))
        if len(files) + len(directories) > MAX_ENTRIES:
            raise BackupError('数据文件数量超过备份限制')
    return sorted(files), sorted(directories)


def readonly_database(path):
    conn = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=20)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    return conn


def check_database(path):
    with contextlib.closing(readonly_database(path)) as conn:
        rows = conn.execute('PRAGMA integrity_check').fetchall()
        if rows != [('ok',)]:
            raise BackupError('数据库完整性检查失败')
        names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if path.name == 'accounts.sqlite3' and not {'users', 'workspaces', 'members', 'sessions', 'api_tokens', 'invitations'}.issubset(names):
            raise BackupError('账户数据库缺少必需的表')
        if path.name == 'studio.sqlite3' and not ({'hub_rooms', 'hub_tasks'}.issubset(names) or 'projects' in names):
            raise BackupError('工作空间数据库缺少必需的表')
        return {'integrity': 'ok', 'schema_readable': True, 'table_count': len(names)}


def _is_database(path):
    if path.suffix.lower() == '.sqlite3':
        return True
    with path.open('rb') as stream:
        return stream.read(16) == b'SQLite format 3\x00'


def _hash_file(path):
    digest, size = hashlib.sha256(), 0
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(CHUNK), b''):
            size += len(chunk)
            if size > MAX_FILE_BYTES:
                raise BackupError('单个文件超过 1 GiB 备份限制')
            digest.update(chunk)
    return size, digest.hexdigest()


def create_backup(data_dir, output, writers_stopped=False):
    if not writers_stopped:
        raise BackupError('请停止服务器、调度器及所有写入客户端后，使用 --writers-stopped 创建一致文件快照')
    supplied_root = Path(data_dir).expanduser().absolute()
    if _link(supplied_root):
        raise BackupError('数据目录不能是链接')
    root = supplied_root.resolve()
    output = Path(output).expanduser().absolute()
    if output.exists() or output.is_symlink():
        raise BackupError('备份目标已存在，不会覆盖')
    if output.resolve().is_relative_to(root):
        raise BackupError('备份文件必须保存在数据目录之外')
    files, directories = _inventory(root)
    databases = {relative for relative, path in files if _is_database(path)}
    files = [(relative, path) for relative, path in files if not any(relative.endswith(suffix) and relative[:-len(suffix)] in databases for suffix in ('-wal', '-shm', '-journal'))]
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.workspace-backup-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        staged = temporary / 'data'
        staged.mkdir()
        entries, total = [], 0
        for directory in directories:
            (staged / directory).mkdir(parents=True, exist_ok=True)
        for relative, source in files:
            target = staged / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative in databases:
                with contextlib.closing(readonly_database(source)) as original, contextlib.closing(sqlite3.connect(target)) as copy:
                    original.backup(copy)
                check_database(target)
            else:
                before = source.stat()
                if before.st_size > MAX_FILE_BYTES:
                    raise BackupError('单个文件超过 1 GiB 备份限制')
                shutil.copyfile(source, target)
                after = source.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise BackupError('备份期间文件发生变化，请停止所有写入后重试')
            size, checksum = _hash_file(target)
            total += size
            if total > MAX_TOTAL_BYTES:
                raise BackupError('备份总大小超过 5 GiB 限制')
            entries.append({'path': relative, 'bytes': size, 'sha256': checksum, 'kind': 'sqlite' if relative in databases else 'file'})
        manifest = {'format': FORMAT, 'version': 1, 'created_at': datetime.now(timezone.utc).isoformat(), 'consistency': 'writers_stopped_declared', 'files': entries, 'directories': directories, 'total_bytes': total}
        archive = temporary / 'backup.zip'
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as zipped:
            zipped.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf-8'))
            for entry in entries:
                zipped.write(staged / entry['path'], 'data/' + entry['path'])
        verified = verify_backup(archive)
        try:
            os.link(archive, output)
        except FileExistsError as exc:
            raise BackupError('备份目标已存在，不会覆盖') from exc
        except OSError:
            with output.open('xb') as destination, archive.open('rb') as source:
                shutil.copyfileobj(source, destination, CHUNK)
        return {'ok': True, 'output': str(output), **verified}


def _validated_manifest(zipped):
    members = zipped.infolist()
    if not members or len(members) > MAX_ENTRIES + 1:
        raise BackupError('备份文件数量无效')
    seen, total = set(), 0
    for item in members:
        if item.orig_filename != item.filename or item.is_dir():
            raise BackupError('备份包含非规范路径或目录条目')
        _safe_relative(item.filename)
        folded = item.filename.casefold()
        if folded in seen:
            raise BackupError('备份存在重复或大小写冲突的路径')
        seen.add(folded)
        mode = item.external_attr >> 16
        if stat.S_IFMT(mode) not in (0, stat.S_IFREG) or item.flag_bits & 1:
            raise BackupError('备份包含链接、特殊文件或加密条目')
        limit = MAX_MANIFEST_BYTES if item.filename == 'manifest.json' else MAX_FILE_BYTES
        if item.file_size > limit or item.file_size < 0:
            raise BackupError('备份单文件大小超出限制')
        if item.file_size > max(1, item.compress_size) * MAX_COMPRESSION_RATIO:
            raise BackupError('备份压缩比例超出安全限制')
        if item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise BackupError('备份使用不支持的压缩格式')
        total += item.file_size
        if total > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES:
            raise BackupError('备份解压总大小超出限制')
    if 'manifest.json' not in {item.filename for item in members}:
        raise BackupError('备份缺少 manifest.json')
    try:
        manifest = json.loads(zipped.read('manifest.json').decode('utf-8'))
    except (ValueError, UnicodeDecodeError, RuntimeError) as exc:
        raise BackupError('备份清单无法读取') from exc
    if not isinstance(manifest, dict) or manifest.get('format') != FORMAT or type(manifest.get('version')) is not int or manifest['version'] != 1:
        raise BackupError('备份格式或版本不受支持')
    entries, directories = manifest.get('files'), manifest.get('directories')
    if not isinstance(entries, list) or not isinstance(directories, list) or len(entries) + len(directories) > MAX_ENTRIES:
        raise BackupError('备份清单条目无效')
    file_names, directory_names, logical = set(), set(), set()
    for directory in directories:
        _safe_relative(directory)
        if directory.casefold() in logical:
            raise BackupError('备份清单路径冲突')
        directory_names.add(directory)
        logical.add(directory.casefold())
    expected_size = 0
    for entry in entries:
        if not isinstance(entry, dict):
            raise BackupError('备份文件清单格式无效')
        name = entry.get('path')
        _safe_relative(name)
        size = entry.get('bytes')
        if name.casefold() in logical or isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES or not isinstance(entry.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']) or entry.get('kind') not in ('file', 'sqlite'):
            raise BackupError('备份文件清单格式无效或路径冲突')
        if name.lower().endswith('.sqlite3') and entry['kind'] != 'sqlite':
            raise BackupError('数据库条目必须声明为 SQLite 快照')
        logical.add(name.casefold())
        file_names.add(name)
        expected_size += size
        try:
            saved_size = zipped.getinfo('data/' + name).file_size
        except KeyError as exc:
            raise BackupError('备份清单引用了不存在的文件') from exc
        if saved_size != size:
            raise BackupError('备份文件长度与清单不一致')
    for name in file_names | directory_names:
        for parent in PurePosixPath(name).parents:
            if str(parent) != '.' and str(parent) not in directory_names:
                raise BackupError('备份清单缺少父目录或存在文件目录冲突')
    if {item.filename for item in members} != {'manifest.json'} | {'data/' + name for name in file_names}:
        raise BackupError('备份含有未登记文件')
    if expected_size > MAX_TOTAL_BYTES or type(manifest.get('total_bytes')) is not int or manifest['total_bytes'] != expected_size:
        raise BackupError('备份总大小与清单不一致')
    return manifest


def _verify_streams(zipped, manifest, extract_to=None):
    for entry in manifest['files']:
        digest, size = hashlib.sha256(), 0
        with zipped.open('data/' + entry['path']) as source:
            target = extract_to / entry['path'] if extract_to is not None else None
            if target is not None:
                target.parent.mkdir(parents=True, exist_ok=True)
            with target.open('xb') if target is not None else contextlib.nullcontext() as destination:
                for chunk in iter(lambda: source.read(CHUNK), b''):
                    size += len(chunk)
                    if size > entry['bytes'] or size > MAX_FILE_BYTES:
                        raise BackupError('备份展开长度超出清单')
                    digest.update(chunk)
                    if destination is not None:
                        destination.write(chunk)
        if size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise BackupError('备份 SHA256 或长度校验失败')


def _summary(manifest):
    return {'files': len(manifest['files']), 'databases': sum(entry['kind'] == 'sqlite' for entry in manifest['files']), 'bytes': manifest['total_bytes'], 'sha256_verified': True}


def _preflight_archive(archive):
    with Path(archive).open('rb') as stream:
        size = os.fstat(stream.fileno()).st_size
        if size < 22 or size > MAX_TOTAL_BYTES + MAX_MANIFEST_BYTES + MAX_DIRECTORY_BYTES:
            raise BackupError('ZIP 文件大小超出限制')
        stream.seek(max(0, size - 65557))
        tail = stream.read(65557)
    position = tail.rfind(b'PK\x05\x06')
    if position < 0 or len(tail) - position < 22:
        raise BackupError('ZIP 目录记录无效')
    _, disk, directory_disk, entries_on_disk, entries, directory_bytes, _, comment_bytes = struct.unpack_from('<4s4H2LH', tail, position)
    if disk or directory_disk or entries_on_disk != entries or not 1 <= entries <= MAX_ENTRIES + 1 or directory_bytes > MAX_DIRECTORY_BYTES or position + 22 + comment_bytes != len(tail):
        raise BackupError('ZIP 目录条目或大小超出限制')


def verify_backup(archive):
    try:
        _preflight_archive(archive)
        with zipfile.ZipFile(archive, 'r') as zipped:
            manifest = _validated_manifest(zipped)
            _verify_streams(zipped, manifest)
            return _summary(manifest)
    except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError) as exc:
        raise BackupError('ZIP 完整性检查失败') from exc


def restore_backup(archive, destination):
    supplied = Path(destination).expanduser().absolute()
    if _link(supplied) or (supplied.exists() and (not supplied.is_dir() or any(supplied.iterdir()))):
        raise BackupError('恢复目标必须是明确指定的空目录或尚不存在的目录')
    target = supplied.resolve()
    if target == target.parent:
        raise BackupError('不能恢复到文件系统根目录')
    verify_backup(archive)
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.workspace-restore-', dir=target.parent) as temporary:
        staged = Path(temporary) / 'restored'
        staged.mkdir()
        try:
            _preflight_archive(archive)
            with zipfile.ZipFile(archive, 'r') as zipped:
                manifest = _validated_manifest(zipped)
                for directory in manifest['directories']:
                    (staged / directory).mkdir(parents=True, exist_ok=True)
                _verify_streams(zipped, manifest, extract_to=staged)
            for entry in manifest['files']:
                if entry['kind'] == 'sqlite':
                    check_database(staged / entry['path'])
        except (zipfile.BadZipFile, EOFError, RuntimeError, NotImplementedError, sqlite3.Error) as exc:
            raise BackupError('恢复内容的完整性检查失败') from exc
        existed = target.exists()
        if _link(supplied) or supplied.resolve() != target or (existed and (not target.is_dir() or any(target.iterdir()))):
            raise BackupError('恢复目标已发生变化或不再为空')
        if existed:
            target.rmdir()
        try:
            staged.rename(target)
        except OSError:
            if existed and not target.exists():
                target.mkdir()
            raise
    return {'ok': True, 'destination': str(target), **_summary(manifest)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Create, verify or restore an Agent Workspace backup')
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('create')
    create.add_argument('--data-dir', default=str(core.base_data_root()))
    create.add_argument('--output', required=True)
    create.add_argument('--writers-stopped', action='store_true')
    verify = commands.add_parser('verify')
    verify.add_argument('--archive', required=True)
    restore = commands.add_parser('restore')
    restore.add_argument('--archive', required=True)
    restore.add_argument('--destination', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'create':
            result = create_backup(args.data_dir, args.output, args.writers_stopped)
        elif args.command == 'verify':
            result = {'ok': True, **verify_backup(args.archive)}
        else:
            result = restore_backup(args.archive, args.destination)
    except (BackupError, OSError, sqlite3.Error) as exc:
        message = str(exc) if isinstance(exc, BackupError) else '文件或数据库操作失败，请检查目录、权限与磁盘空间'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
