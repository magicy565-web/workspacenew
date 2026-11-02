"""Workspace identity, scoped access and durable account administration."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
from http.cookies import SimpleCookie
import json
import os
import re
import secrets
import sqlite3
from uuid import uuid4
from urllib.parse import urlsplit

from . import core
from .hub import HubError

COOKIE = 'workspace_session'
JSON = 'application/json; charset=utf-8'
SESSION_SECONDS = 7 * 24 * 3600


def now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def later(seconds):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(timespec='microseconds')


def digest(token):
    try:
        return hashlib.sha256(token.encode('utf-8')).hexdigest()
    except (AttributeError, UnicodeEncodeError) as exc:
        raise HubError('访问凭据无效', 401) from exc


def _text(value, label, size=120):
    if not isinstance(value, str) or not value.strip() or len(value) > size:
        raise HubError(f'{label}须为 1–{size} 个字符')
    try:
        value.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise HubError(f'{label}包含无效的 Unicode 字符') from exc
    return value.strip()


def email_address(value):
    value = _text(value, '邮箱', 254).lower()
    if not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', value):
        raise HubError('邮箱格式无效')
    return value


def password_hash(password, salt=None):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        raise HubError('密码须为 12–1024 个字符')
    salt = salt or secrets.token_hex(16)
    try:
        encoded = password.encode('utf-8')
    except UnicodeEncodeError as exc:
        raise HubError('密码包含无效的 Unicode 字符') from exc
    value = hashlib.scrypt(encoded, salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=64 * 1024 * 1024, dklen=32).hex()
    return f'scrypt:32768:8:3:{salt}:{value}'


def verify_password(password, stored):
    if not isinstance(password, str) or not 12 <= len(password) <= 1024:
        return False
    valid = isinstance(stored, str) and bool(re.fullmatch(r'scrypt:32768:8:3:[a-f0-9]{32}:[a-f0-9]{64}', stored))
    salt = stored.split(':')[-2] if valid else '0' * 32
    try:
        candidate = password_hash(password, salt)
    except (HubError, ValueError):
        return False
    return valid and secrets.compare_digest(candidate, stored)


def db():
    root = core.base_data_root()
    root.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(root / 'accounts.sqlite3', timeout=20)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (id TEXT PRIMARY KEY,email TEXT UNIQUE NOT NULL,name TEXT NOT NULL,password_hash TEXT NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS workspaces (id TEXT PRIMARY KEY,name TEXT NOT NULL,storage_key TEXT UNIQUE NOT NULL,created_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS members (workspace_id TEXT NOT NULL REFERENCES workspaces(id),user_id TEXT NOT NULL REFERENCES users(id),role TEXT NOT NULL,PRIMARY KEY(workspace_id,user_id));
        CREATE TABLE IF NOT EXISTS sessions (token_hash TEXT PRIMARY KEY,user_id TEXT NOT NULL REFERENCES users(id),workspace_id TEXT REFERENCES workspaces(id),created_at TEXT NOT NULL,expires_at TEXT NOT NULL,last_seen_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS api_tokens (id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,user_id TEXT NOT NULL REFERENCES users(id),workspace_id TEXT NOT NULL REFERENCES workspaces(id),name TEXT NOT NULL,scopes TEXT NOT NULL,created_at TEXT NOT NULL,last_used_at TEXT,revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS invitations (id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,workspace_id TEXT NOT NULL REFERENCES workspaces(id),email TEXT NOT NULL,role TEXT NOT NULL,expires_at TEXT NOT NULL,accepted_at TEXT,revoked_at TEXT);
        CREATE TABLE IF NOT EXISTS login_attempts (key TEXT PRIMARY KEY,count INTEGER NOT NULL,expires_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY AUTOINCREMENT,workspace_id TEXT NOT NULL,actor_id TEXT NOT NULL,actor_name TEXT NOT NULL,method TEXT NOT NULL,path TEXT NOT NULL,status INTEGER NOT NULL,created_at TEXT NOT NULL);
    ''')
    conn.commit()
    return conn


@contextlib.contextmanager
def transaction():
    with contextlib.closing(db()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise


def configured():
    if os.environ.get('STUDIO_AUTH_MODE') == 'required' or os.environ.get('STUDIO_PUBLIC_ORIGIN'):
        return True
    path = core.base_data_root() / 'accounts.sqlite3'
    if not path.exists():
        return False
    return has_users()


def has_users():
    if not (core.base_data_root() / 'accounts.sqlite3').exists():
        return False
    with contextlib.closing(db()) as conn:
        return bool(conn.execute('SELECT 1 FROM users LIMIT 1').fetchone())


def cookie(token='', clear=False):
    if not isinstance(token, str) or (token and not re.fullmatch(r'[A-Za-z0-9_-]{1,200}', token)):
        raise HubError('会话凭据无效', 400)
    value = f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={0 if clear else SESSION_SECONDS}'
    if os.environ.get('STUDIO_PUBLIC_ORIGIN', '').lower().startswith('https://'):
        value += '; Secure'
    return {'Set-Cookie': value}


def _session(conn, user_id, workspace_id):
    token = secrets.token_urlsafe(32)
    stamp = now()
    conn.execute('DELETE FROM sessions WHERE expires_at<=?', (stamp,))
    conn.execute('INSERT INTO sessions VALUES (?,?,?,?,?,?)', (digest(token), user_id, workspace_id, stamp, later(SESSION_SECONDS), stamp))
    return token


def _workspaces(conn, user_id):
    return [dict(row) for row in conn.execute('SELECT w.id,w.name,m.role FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.created_at', (user_id,))]


def _resolve(conn, user_id, workspace_id, session_hash=None, scopes=None, token_id=None):
    user = conn.execute('SELECT id,email,name FROM users WHERE id=?', (user_id,)).fetchone()
    if not user:
        return None
    member = conn.execute('SELECT w.id,w.name,w.storage_key,m.role FROM workspaces w JOIN members m ON m.workspace_id=w.id WHERE w.id=? AND m.user_id=?', (workspace_id, user_id)).fetchone()
    return {'user': dict(user), 'workspace': dict(member) if member else None, 'session_hash': session_hash, 'scopes': scopes, 'token_id': token_id}


def _refresh_principal(conn, principal):
    if not principal:
        raise HubError('请先登录', 401)
    if principal.get('local'):
        if configured():
            raise HubError('账户已经启用，请登录', 401)
        return principal
    if principal.get('token_id'):
        row = conn.execute('SELECT * FROM api_tokens WHERE id=? AND revoked_at IS NULL', (principal['token_id'],)).fetchone()
        if not row:
            raise HubError('访问凭据无效', 401)
        fresh = _resolve(conn, row['user_id'], row['workspace_id'], scopes=json.loads(row['scopes']), token_id=row['id'])
        if not fresh or not fresh['workspace']:
            raise HubError('工作空间访问权限已撤销', 403)
    else:
        row = conn.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?', (principal.get('session_hash'), now())).fetchone()
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec='microseconds')
        if not row or row['last_seen_at'] < cutoff:
            raise HubError('登录已过期，请重新登录', 401)
        fresh = _resolve(conn, row['user_id'], row['workspace_id'], session_hash=row['token_hash'])
        if not fresh:
            raise HubError('登录已过期，请重新登录', 401)
    principal.clear()
    principal.update(fresh)
    return principal


def identify(headers):
    if not configured():
        return {'local': True, 'user': {'id': 'local', 'name': '本地用户', 'email': ''}, 'workspace': {'id': 'local', 'name': '我的工作空间', 'role': 'owner', 'storage_key': '.'}, 'scopes': None}
    authorization = headers.get('Authorization', '')
    with contextlib.closing(db()) as conn:
        if authorization:
            if not authorization.startswith('Bearer ') or len(authorization) > 512:
                raise HubError('访问凭据无效', 401)
            row = conn.execute('SELECT * FROM api_tokens WHERE token_hash=? AND revoked_at IS NULL', (digest(authorization[7:]),)).fetchone()
            if not row:
                raise HubError('访问凭据无效', 401)
            principal = _resolve(conn, row['user_id'], row['workspace_id'], scopes=json.loads(row['scopes']), token_id=row['id'])
            if not principal or not principal['workspace']:
                raise HubError('工作空间访问权限已撤销', 403)
            conn.execute('UPDATE api_tokens SET last_used_at=? WHERE id=?', (now(), row['id']))
            conn.commit()
            return principal
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get('Cookie', ''))
        except Exception:
            return None
        if COOKIE not in cookies:
            return None
        token = cookies[COOKIE].value
        if len(token) > 200:
            return None
        row = conn.execute('SELECT * FROM sessions WHERE token_hash=? AND expires_at>?', (digest(token), now())).fetchone()
        if not row:
            return None
        idle_cutoff = (datetime.now(timezone.utc) - timedelta(hours=12)).isoformat(timespec='microseconds')
        if row['last_seen_at'] < idle_cutoff:
            conn.execute('DELETE FROM sessions WHERE token_hash=?', (row['token_hash'],))
            conn.commit()
            return None
        principal = _resolve(conn, row['user_id'], row['workspace_id'], session_hash=row['token_hash'])
        conn.execute('UPDATE sessions SET last_seen_at=? WHERE token_hash=?', (now(), row['token_hash']))
        conn.commit()
        return principal


def authorize(principal, method, path):
    public = {'/api/account/session', '/api/account/login', '/api/account/setup', '/api/account/invitations/accept'}
    protected = path.startswith(('/api/', '/preview/'))
    if not protected:
        return
    if principal and principal.get('token_id') and path.startswith('/api/account/'):
        raise HubError('API 凭据不能管理账户', 403)
    if path in public:
        return
    if not principal:
        raise HubError('请先登录', 401)
    with contextlib.closing(db()) as conn:
        _refresh_principal(conn, principal)
    if path.startswith('/api/account/'):
        if principal.get('token_id'):
            raise HubError('API 凭据不能管理账户', 403)
        return
    if not principal['workspace']:
        raise HubError('请选择有权限的工作空间', 403)
    role = principal['workspace']['role']
    scopes = principal.get('scopes')
    if method == 'GET':
        if scopes is not None and 'read' not in scopes:
            raise HubError('此凭据没有读取权限', 403)
    else:
        if role == 'viewer':
            raise HubError('当前成员仅可查看', 403)
        execution = path == '/api/hub/tasks/claim' or bool(re.fullmatch(r'/api/hub/tasks/[^/]+/(heartbeat|complete|fail|execution/(events|result|result/read))', path)) or bool(re.fullmatch(r'/api/hub/workers/(register|[^/]+/(ping|disconnect))', path))
        required = 'execute' if execution else 'write'
        if scopes is not None and required not in scopes:
            raise HubError('此凭据缺少所需操作权限', 403)


def data_directory(principal):
    if not principal or not principal.get('workspace'):
        return None
    key = principal['workspace']['storage_key']
    root = core.base_data_root()
    if key == '.':
        return root
    if not isinstance(key, str) or not re.fullmatch(r'[a-f0-9]{32}', key):
        raise HubError('工作空间存储配置无效', 403)
    expected = root / 'workspaces' / key
    directory = expected.resolve()
    if not directory.is_relative_to(root) or directory != expected:
        raise HubError('工作空间存储配置无效', 403)
    return directory


def session_info(principal):
    mode = 'authenticated' if configured() else 'local'
    with contextlib.closing(db()) as conn:
        available = not conn.execute('SELECT 1 FROM users LIMIT 1').fetchone() and not os.environ.get('STUDIO_PUBLIC_ORIGIN')
        if not principal or principal.get('local'):
            return {'mode': mode, 'setup_available': available, 'user': None, 'workspaces': [], 'workspace': None}
        if principal.get('token_id'):
            raise HubError('API 凭据不能管理账户', 403)
        _refresh_principal(conn, principal)
        workspaces = _workspaces(conn, principal['user']['id'])
        current = next((item for item in workspaces if principal['workspace'] and item['id'] == principal['workspace']['id']), None)
        return {'mode': mode, 'setup_available': False, 'user': principal['user'], 'workspaces': workspaces, 'workspace': current}


def _signed_info(token):
    principal = identify({'Cookie': f'{COOKIE}={token}'})
    return session_info(principal), cookie(token)


def _member(principal, owner=False, conn=None):
    if conn is None:
        with contextlib.closing(db()) as owned_conn:
            return _member(principal, owner=owner, conn=owned_conn)
    _refresh_principal(conn, principal)
    if not principal or principal.get('local'):
        raise HubError('请先启用账户并登录工作空间', 401)
    if not principal.get('workspace'):
        raise HubError('没有此工作空间的访问权限', 403)
    if owner and principal['workspace']['role'] != 'owner':
        raise HubError('此操作需要空间所有者权限', 403)
    return principal['user']['id'], principal['workspace']['id']


def _account_user(conn, principal):
    _refresh_principal(conn, principal)
    if principal.get('local') or principal.get('token_id') or not principal.get('session_hash'):
        raise HubError('请使用账户登录后操作', 401)
    return principal['user']['id']


def setup(body, allow_public=False):
    if os.environ.get('STUDIO_PUBLIC_ORIGIN') and not allow_public:
        raise HubError('公开服务请先使用管理命令初始化所有者', 403)
    email = email_address(body.get('email'))
    name = _text(body.get('name'), '姓名')
    password = password_hash(body.get('password'))
    workspace_name = _text(body.get('workspace_name', '我的工作空间'), '空间名称')
    with transaction() as conn:
        if conn.execute('SELECT 1 FROM users LIMIT 1').fetchone():
            raise HubError('账户已经初始化，请登录', 409)
        user_id, workspace_id = uuid4().hex, uuid4().hex
        conn.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user_id, email, name, password, now()))
        conn.execute('INSERT INTO workspaces VALUES (?,?,?,?)', (workspace_id, workspace_name, '.', now()))
        conn.execute('INSERT INTO members VALUES (?,?,?)', (workspace_id, user_id, 'owner'))
        token = _session(conn, user_id, workspace_id)
    return _signed_info(token)


def _check_login_limit(key):
    with transaction() as conn:
        conn.execute('DELETE FROM login_attempts WHERE expires_at<=?', (now(),))
        row = conn.execute('SELECT count FROM login_attempts WHERE key=?', (key,)).fetchone()
        if row and row['count'] >= 8:
            raise HubError('尝试次数过多，请 10 分钟后重试', 429)
        conn.execute('INSERT INTO login_attempts VALUES (?,1,?) ON CONFLICT(key) DO UPDATE SET count=count+1', (key, later(600)))


def login(body):
    email = email_address(body.get('email'))
    key = digest(email)
    _check_login_limit(key)
    with contextlib.closing(db()) as conn:
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
    if not verify_password(body.get('password'), user['password_hash'] if user else None):
        raise HubError('邮箱或密码不正确', 401)
    with transaction() as conn:
        memberships = _workspaces(conn, user['id'])
        token = _session(conn, user['id'], memberships[0]['id'] if memberships else None)
        conn.execute('DELETE FROM login_attempts WHERE key=?', (key,))
    return _signed_info(token)


def accept_invitation(body):
    email = email_address(body.get('email'))
    supplied = _text(body.get('token'), '邀请凭据', 200)
    _check_login_limit(digest('invite:' + email))
    with transaction() as conn:
        invite = conn.execute('SELECT * FROM invitations WHERE token_hash=? AND email=? AND accepted_at IS NULL AND revoked_at IS NULL AND expires_at>?', (digest(supplied), email, now())).fetchone()
        if not invite:
            raise HubError('邀请已失效或邮箱不匹配', 400)
        user = conn.execute('SELECT * FROM users WHERE email=?', (email,)).fetchone()
        if user:
            if not verify_password(body.get('password'), user['password_hash']):
                raise HubError('邮箱或密码不正确', 401)
            user_id = user['id']
        else:
            user_id = uuid4().hex
            name = _text(body.get('name'), '姓名')
            encoded = password_hash(body.get('password'))
            conn.execute('INSERT INTO users VALUES (?,?,?,?,?)', (user_id, email, name, encoded, now()))
        existing = conn.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?', (invite['workspace_id'], user_id)).fetchone()
        if not existing:
            conn.execute('INSERT INTO members VALUES (?,?,?)', (invite['workspace_id'], user_id, invite['role']))
        conn.execute('UPDATE invitations SET accepted_at=? WHERE id=?', (now(), invite['id']))
        token = _session(conn, user_id, invite['workspace_id'])
        conn.execute('DELETE FROM login_attempts WHERE key=?', (digest('invite:' + email),))
    return _signed_info(token)


def audit(principal, method, path, status):
    if method == 'GET' or not principal or principal.get('local') or not principal.get('workspace'):
        return
    static = {'api', 'account', 'hub', 'projects', 'preview', 'session', 'setup', 'login', 'logout', 'workspaces', 'select', 'members', 'invitations', 'accept', 'tokens', 'audit', 'bootstrap', 'connection', 'agents', 'templates', 'workers', 'register', 'ping', 'disconnect', 'rooms', 'messages', 'context', 'events', 'sources', 'tasks', 'claim', 'heartbeat', 'complete', 'fail', 'runs', 'cancel', 'retry', 'artifacts', 'reviews', 'download', 'images', 'brief', 'build', 'skills', 'schedules', 'pause', 'run-now', 'files', 'search', 'shares', 'executors', 'start', 'stop', 'comments', 'modifications', 'apply', 'rebase', 'execution', 'executions', 'result', 'read', 'reconcile'}
    route = '/' + '/'.join(part if part in static else ':id' for part in urlsplit(path).path.split('/') if part)
    with transaction() as conn:
        conn.execute('INSERT INTO audit (workspace_id,actor_id,actor_name,method,path,status,created_at) VALUES (?,?,?,?,?,?,?)', (principal['workspace']['id'], principal['user']['id'], principal['user']['name'], method, route[:1000], status, now()))


def dispatch(method, path, body, principal, query=None):
    if not path.startswith('/api/account/'):
        return None
    if not isinstance(body, dict):
        raise HubError('请求内容须为 JSON 对象')
    if principal and principal.get('token_id'):
        raise HubError('API 凭据不能管理账户', 403)
    route = path.removeprefix('/api/account/').strip('/').split('/')
    headers = {}
    status = 200
    if method == 'GET' and route == ['session']:
        data = session_info(principal)
    elif method == 'POST' and route == ['setup']:
        data, headers = setup(body)
    elif method == 'POST' and route == ['login']:
        data, headers = login(body)
    elif method == 'POST' and route == ['invitations', 'accept']:
        data, headers = accept_invitation(body)
    elif method == 'POST' and route == ['logout']:
        if principal and principal.get('session_hash'):
            with transaction() as conn:
                conn.execute('DELETE FROM sessions WHERE token_hash=?', (principal['session_hash'],))
        data, headers = {'ok': True}, cookie(clear=True)
    else:
        with transaction() as conn:
            user_id = _account_user(conn, principal)
            workspace_id = principal['workspace']['id'] if principal.get('workspace') else None
            workspace_operation = method == 'POST' and (route == ['workspaces'] or (len(route) == 3 and route[0] == 'workspaces' and route[2] == 'select'))
            if not workspace_operation:
                user_id, workspace_id = _member(principal, conn=conn)
            if method == 'POST' and route == ['workspaces']:
                new_id = uuid4().hex
                name = _text(body.get('name'), '空间名称')
                conn.execute('INSERT INTO workspaces VALUES (?,?,?,?)', (new_id, name, new_id, now()))
                conn.execute('INSERT INTO members VALUES (?,?,?)', (new_id, user_id, 'owner'))
                conn.execute('UPDATE sessions SET workspace_id=? WHERE token_hash=?', (new_id, principal['session_hash']))
                data = {'id': new_id, 'name': name, 'role': 'owner'}
                status = 201
            elif method == 'POST' and len(route) == 3 and route[0] == 'workspaces' and route[2] == 'select':
                selected = conn.execute('SELECT 1 FROM members WHERE workspace_id=? AND user_id=?', (route[1], user_id)).fetchone()
                if not selected:
                    raise HubError('没有此工作空间的访问权限', 403)
                conn.execute('UPDATE sessions SET workspace_id=? WHERE token_hash=?', (route[1], principal['session_hash']))
                data = {'ok': True}
            elif method == 'GET' and route == ['members']:
                _member(principal, owner=True, conn=conn)
                data = {'members': [dict(row) for row in conn.execute('SELECT u.id,u.email,u.name,m.role FROM users u JOIN members m ON m.user_id=u.id WHERE m.workspace_id=?', (workspace_id,))], 'invitations': [dict(row) for row in conn.execute('SELECT id,email,role,expires_at,accepted_at,revoked_at FROM invitations WHERE workspace_id=? ORDER BY rowid DESC', (workspace_id,))]}
            elif method == 'POST' and route == ['invitations']:
                _member(principal, owner=True, conn=conn)
                email = email_address(body.get('email'))
                role = body.get('role')
                if role not in ('editor', 'viewer'):
                    raise HubError('成员角色须为 editor 或 viewer')
                invitation_id, raw_token = uuid4().hex, secrets.token_urlsafe(32)
                expires = later(7 * 86400)
                conn.execute('INSERT INTO invitations VALUES (?,?,?,?,?,?,NULL,NULL)', (invitation_id, digest(raw_token), workspace_id, email, role, expires))
                data = {'invitation': {'id': invitation_id, 'email': email, 'role': role, 'expires_at': expires}, 'token': raw_token}
                status = 201
            elif method == 'DELETE' and len(route) == 2 and route[0] == 'invitations':
                _member(principal, owner=True, conn=conn)
                if not conn.execute('UPDATE invitations SET revoked_at=? WHERE id=? AND workspace_id=?', (now(), route[1], workspace_id)).rowcount:
                    raise HubError('邀请不存在', 404)
                data = {'ok': True}
            elif method in ('PUT', 'DELETE') and len(route) == 2 and route[0] == 'members':
                _member(principal, owner=True, conn=conn)
                member = conn.execute('SELECT role FROM members WHERE workspace_id=? AND user_id=?', (workspace_id, route[1])).fetchone()
                if not member:
                    raise HubError('成员不存在', 404)
                if member['role'] == 'owner':
                    raise HubError('不能通过此操作修改或移除空间所有者', 409)
                if method == 'DELETE':
                    conn.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?', (workspace_id, route[1]))
                    data = {'ok': True}
                else:
                    role = body.get('role')
                    if role not in ('editor', 'viewer'):
                        raise HubError('成员角色须为 editor 或 viewer')
                    conn.execute('UPDATE members SET role=? WHERE workspace_id=? AND user_id=?', (role, workspace_id, route[1]))
                    data = {'id': route[1], 'role': role}
            elif method == 'GET' and route == ['tokens']:
                data = {'tokens': []}
                for row in conn.execute('SELECT id,name,scopes,created_at,last_used_at,revoked_at FROM api_tokens WHERE user_id=? AND workspace_id=? ORDER BY created_at DESC', (user_id, workspace_id)):
                    data['tokens'].append(dict(row, scopes=json.loads(row['scopes'])))
            elif method == 'POST' and route == ['tokens']:
                name = _text(body.get('name'), '凭据名称')
                scopes = body.get('scopes')
                if not isinstance(scopes, list) or not scopes or any(scope not in ('read', 'write', 'execute') for scope in scopes):
                    raise HubError('请选择有效的凭据权限')
                if principal['workspace']['role'] == 'viewer' and set(scopes) != {'read'}:
                    raise HubError('只读成员不能创建写入或执行凭据', 403)
                raw_token, token_id = 'aw_' + secrets.token_urlsafe(32), uuid4().hex
                stamp = now()
                conn.execute('INSERT INTO api_tokens (id,token_hash,user_id,workspace_id,name,scopes,created_at,last_used_at,revoked_at) VALUES (?,?,?,?,?,?,?,NULL,NULL)', (token_id, digest(raw_token), user_id, workspace_id, name, json.dumps(sorted(set(scopes))), stamp))
                data = {'token': raw_token, 'record': {'id': token_id, 'name': name, 'scopes': sorted(set(scopes)), 'created_at': stamp}}
                status = 201
            elif method == 'DELETE' and len(route) == 2 and route[0] == 'tokens':
                if not conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=? AND user_id=? AND workspace_id=?', (now(), route[1], user_id, workspace_id)).rowcount:
                    raise HubError('凭据不存在', 404)
                data = {'ok': True}
            elif method == 'GET' and route == ['audit']:
                _member(principal, owner=True, conn=conn)
                raw = (query or {}).get('after', ['0'])
                try:
                    after = int(raw[0] if isinstance(raw, list) else raw)
                    if after < 0:
                        raise ValueError
                except (ValueError, TypeError, IndexError):
                    raise HubError('审计游标无效')
                events = [dict(row) for row in conn.execute('SELECT * FROM audit WHERE workspace_id=? AND id>? ORDER BY id LIMIT 200', (workspace_id, after))]
                data = {'events': events, 'cursor': events[-1]['id'] if events else after}
            else:
                raise HubError('账户接口不存在', 404)
        if method == 'POST' and len(route) == 3 and route[0] == 'workspaces' and route[2] == 'select':
            with contextlib.closing(db()) as conn:
                updated = _resolve(conn, user_id, route[1], session_hash=principal['session_hash'])
            data = session_info(updated)
    return status, data, JSON, headers
