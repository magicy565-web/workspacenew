"""Local operator commands. Passwords are read from the terminal, never argv."""
from __future__ import annotations

import argparse
import contextlib
import getpass
import json
import sqlite3

from . import accounts, backup, core, hub


def _password():
    first = getpass.getpass('新密码（至少 12 个字符）：')
    if first != getpass.getpass('再次输入新密码：'):
        raise hub.HubError('两次输入的密码不一致')
    return first


def initialize(email, name, workspace, password):
    info, _ = accounts.setup({'email': email, 'name': name, 'workspace_name': workspace, 'password': password}, allow_public=True)
    return {'ok': True, 'initialized': True, 'workspace_id': info['workspace']['id']}


def reset_password(email, password):
    email = accounts.email_address(email)
    encoded = accounts.password_hash(password)
    with accounts.transaction() as conn:
        user = conn.execute('SELECT id FROM users WHERE email=?', (email,)).fetchone()
        if not user:
            raise hub.HubError('账户不存在', 404)
        conn.execute('UPDATE users SET password_hash=? WHERE id=?', (encoded, user['id']))
        sessions = conn.execute('DELETE FROM sessions WHERE user_id=?', (user['id'],)).rowcount
        tokens = conn.execute('UPDATE api_tokens SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL', (accounts.now(), user['id'])).rowcount
        conn.execute('DELETE FROM login_attempts WHERE key IN (?,?)', (accounts.digest(email), accounts.digest('invite:' + email)))
    return {'ok': True, 'password_reset': True, 'sessions_revoked': sessions, 'api_tokens_revoked': tokens}


def check_storage():
    root = core.base_data_root()
    if not root.exists():
        return {'ok': True, 'initialized': False, 'databases': [], 'status': 'empty'}
    databases = []
    try:
        files, _ = backup._inventory(root)
    except (OSError, backup.BackupError):
        return {'ok': False, 'status': 'storage_unreadable', 'databases': []}
    for relative, path in files:
        if path.suffix.lower() != '.sqlite3':
            continue
        try:
            result = backup.check_database(path)
            databases.append({'path': relative, **result})
        except (OSError, sqlite3.Error, backup.BackupError):
            databases.append({'path': relative, 'integrity': 'failed', 'schema_readable': False})
    return {'ok': all(item['integrity'] == 'ok' for item in databases), 'initialized': bool(databases), 'databases': databases, 'status': 'checked' if databases else 'empty'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Administer the local Agent Workspace data directory')
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--email', required=True)
    init.add_argument('--name', required=True)
    init.add_argument('--workspace', default='我的工作空间')
    reset = commands.add_parser('reset-password')
    reset.add_argument('--email', required=True)
    commands.add_parser('health')
    commands.add_parser('check')
    args = parser.parse_args(argv)
    try:
        if args.command == 'init':
            result = initialize(args.email, args.name, args.workspace, _password())
        elif args.command == 'reset-password':
            result = reset_password(args.email, _password())
        else:
            result = check_storage()
    except (hub.HubError, OSError, sqlite3.Error, EOFError, KeyboardInterrupt) as exc:
        message = str(exc) if isinstance(exc, hub.HubError) else '管理操作失败或已中止，请检查数据目录与权限'
        print(json.dumps({'ok': False, 'error': message}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
