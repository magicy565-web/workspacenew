"""Revocable public snapshots of an explicitly selected artifact version."""
from __future__ import annotations

import contextlib
import hashlib
import html
import re
import secrets
from uuid import uuid4

from . import accounts, core, hub


def _db():
    conn = accounts.db()
    conn.execute('''CREATE TABLE IF NOT EXISTS artifact_shares (
        id TEXT PRIMARY KEY,token_hash TEXT UNIQUE NOT NULL,workspace_key TEXT NOT NULL,
        artifact_id TEXT NOT NULL,version INTEGER NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,
        content TEXT NOT NULL,sha256 TEXT NOT NULL,created_at TEXT NOT NULL,expires_at TEXT NOT NULL,revoked_at TEXT
    )''')
    conn.commit()
    return conn


def _workspace_key():
    try:
        return core.data_root().relative_to(core.base_data_root()).as_posix()
    except ValueError as exc:
        raise hub.HubError('工作空间目录无效', 403) from exc


def _metadata(row):
    return {key: row[key] for key in ('id', 'artifact_id', 'version', 'title', 'kind', 'created_at', 'expires_at', 'revoked_at')}


def create_share(artifact_id, version, expires_days=7):
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise hub.HubError('分享必须指定成果版本')
    if isinstance(expires_days, bool) or not isinstance(expires_days, int) or not 1 <= expires_days <= 30:
        raise hub.HubError('分享期限须为 1–30 天')
    artifact = hub.get_artifact(artifact_id, version)
    token, share_id = secrets.token_urlsafe(32), uuid4().hex
    with contextlib.closing(_db()) as conn:
        conn.execute('INSERT INTO artifact_shares VALUES (?,?,?,?,?,?,?,?,?,?,?,NULL)', (share_id, accounts.digest(token), _workspace_key(), artifact_id, version, artifact['title'], artifact['kind'], artifact['content'], hashlib.sha256(artifact['content'].encode()).hexdigest(), accounts.now(), accounts.later(expires_days * 86400)))
        conn.commit()
        row = conn.execute('SELECT * FROM artifact_shares WHERE id=?', (share_id,)).fetchone()
    return {'share': _metadata(row), 'token': token, 'url': '/share/' + token}


def list_shares(artifact_id):
    hub.get_artifact(artifact_id)
    with contextlib.closing(_db()) as conn:
        return {'shares': [_metadata(row) for row in conn.execute('SELECT * FROM artifact_shares WHERE artifact_id=? AND workspace_key=? ORDER BY created_at DESC', (artifact_id, _workspace_key()))]}


def revoke_share(artifact_id, share_id):
    hub.get_artifact(artifact_id)
    with contextlib.closing(_db()) as conn:
        updated = conn.execute('UPDATE artifact_shares SET revoked_at=COALESCE(revoked_at,?) WHERE id=? AND artifact_id=? AND workspace_key=?', (accounts.now(), share_id, artifact_id, _workspace_key())).rowcount
        if not updated:
            raise hub.HubError('分享不存在', 404)
        conn.commit()
    return {'ok': True}


def public_content(token):
    if not isinstance(token, str) or not re.fullmatch(r'[A-Za-z0-9_-]{43}', token):
        raise hub.HubError('分享不存在或已失效', 404)
    with contextlib.closing(_db()) as conn:
        row = conn.execute('SELECT * FROM artifact_shares WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?', (accounts.digest(token), accounts.now())).fetchone()
    if not row:
        raise hub.HubError('分享不存在或已失效', 404)
    if not secrets.compare_digest(hashlib.sha256(row['content'].encode('utf-8')).hexdigest(), row['sha256']):
        raise hub.HubError('分享不存在或已失效', 404)
    if row['kind'] == 'html':
        content = row['content']
    else:
        title = html.escape(row['title'])
        body = html.escape(row['content'])
        content = f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title><style>body{{font:16px/1.8 system-ui,sans-serif;margin:0;padding:40px 24px;color:#1f2937;background:#f8faf9}}main{{max-width:900px;margin:auto;background:white;padding:32px;border:1px solid #e5e7eb;border-radius:12px}}h1{{font-size:24px;margin:0}}small{{color:#64748b}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}}@media(max-width:600px){{body{{padding:12px}}main{{padding:20px}}}}</style><main><h1>{title}</h1><small>版本 {row["version"]}</small><pre>{body}</pre></main></html>'
    return content.encode('utf-8')
