"""Persistent Agent rosters and explicit, bounded conversation routing.

Team membership constrains assignment. It is not a host tool permission grant.
The implementation is independent of any unobserved third-party router.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import re
from uuid import uuid4

from . import hub
from .core import REQUEST_ACTOR


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS hub_teams (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL,
            version INTEGER NOT NULL, member_ids_json TEXT NOT NULL,
            routing_mode TEXT NOT NULL, primary_room_id TEXT NOT NULL UNIQUE REFERENCES hub_rooms(id),
            archived_at TEXT, created_by TEXT, created_by_name TEXT,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hub_team_revisions (
            team_id TEXT NOT NULL REFERENCES hub_teams(id), version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL, added_json TEXT NOT NULL, removed_json TEXT NOT NULL,
            cancelled_json TEXT NOT NULL, actor_id TEXT, actor_name TEXT,
            created_at TEXT NOT NULL, PRIMARY KEY(team_id,version)
        );
        CREATE TABLE IF NOT EXISTS hub_team_requests (
            scope TEXT NOT NULL, actor_key TEXT NOT NULL, request_key TEXT NOT NULL,
            fingerprint TEXT NOT NULL, resource_id TEXT NOT NULL, created_at TEXT NOT NULL,
            PRIMARY KEY(scope,actor_key,request_key)
        );
    ''')


def _write_allowed():
    from . import hub_execution
    return hub_execution._actor(write=True)


def _key(request_key, payload):
    request_key = hub._text(request_key, '请求标识', 120)
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]+', request_key):
        raise hub.HubError('请求标识须为不含空格的 ASCII 标识')
    try:
        fingerprint = hashlib.sha256(hub._json(payload).encode('utf-8')).hexdigest()
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise hub.HubError('请求内容不是有效 JSON') from exc
    return request_key, fingerprint


def _actor_key():
    return (REQUEST_ACTOR.get() or {}).get('user_id') or 'local'


def _existing(conn, scope, request_key, fingerprint):
    row = conn.execute('SELECT * FROM hub_team_requests WHERE scope=? AND actor_key=? AND request_key=?', (scope, _actor_key(), request_key)).fetchone()
    if row and row['fingerprint'] != fingerprint:
        raise hub.HubError('同一请求标识已用于不同内容', 409)
    return row['resource_id'] if row else None


def _save_key(conn, scope, request_key, fingerprint, resource_id):
    conn.execute('INSERT INTO hub_team_requests VALUES (?,?,?,?,?,?)', (scope, _actor_key(), request_key, fingerprint, resource_id, hub.now()))


def _members(conn, member_ids):
    if not isinstance(member_ids, list) or not 1 <= len(member_ids) <= 24 or any(not isinstance(item, str) for item in member_ids) or len(set(member_ids)) != len(member_ids):
        raise hub.HubError('团队须包含 1–24 个不同的 Agent')
    for agent_id in member_ids:
        hub._find(conn, 'hub_agents', agent_id, '团队成员')
        if agent_id == 'coordinator':
            raise hub.HubError('任务统筹是内部路由角色，请选择实际协作者作为团队成员')
    return sorted(member_ids)


def _mode(value):
    if value not in ('auto', 'mention'):
        raise hub.HubError('routing_mode 须为 auto 或 mention')
    return value


def _definition(row):
    item = {key: row[key] for key in ('id', 'name', 'description', 'version', 'routing_mode', 'primary_room_id', 'archived_at', 'created_by', 'created_by_name', 'created_at', 'updated_at')}
    item['member_ids'] = json.loads(row['member_ids_json'])
    return item


def _unfinished(conn, team_id):
    # Avoid parsing possibly large completed result bodies just to list teams.
    return conn.execute('''SELECT r.* FROM hub_runs r WHERE r.team_id=? AND r.cancelled=0
        AND (NOT EXISTS (SELECT 1 FROM hub_tasks t WHERE t.run_id=r.id)
             OR EXISTS (SELECT 1 FROM hub_tasks t WHERE t.run_id=r.id AND t.status!='completed'))
        ORDER BY r.created_at,r.rowid''', (team_id,)).fetchall()


def team(conn, team_id):
    row = hub._find(conn, 'hub_teams', team_id, '团队')
    item = _definition(row)
    item['members'] = []
    for agent_id in item['member_ids']:
        agent = hub._find(conn, 'hub_agents', agent_id, '团队成员')
        item['members'].append({'agent_id': agent_id, 'name': agent['name'], 'description': agent['description']})
    item.update(room_ids=[row['primary_room_id']], active_run_ids=[run['id'] for run in _unfinished(conn, team_id)], routing_implementation='bounded_user_message_v1')
    return item


def teams(conn):
    return [team(conn, row['id']) for row in conn.execute('SELECT id FROM hub_teams ORDER BY updated_at DESC,rowid DESC')]


def list_teams():
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return {'teams': teams(conn)}


def get_team(team_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return team(conn, team_id)


def _revision(conn, team_id, added, removed, cancelled):
    row = hub._find(conn, 'hub_teams', team_id, '团队')
    actor = REQUEST_ACTOR.get() or {}
    conn.execute('INSERT INTO hub_team_revisions VALUES (?,?,?,?,?,?,?,?,?)', (team_id, row['version'], hub._json(_definition(row)), hub._json(added), hub._json(removed), hub._json(cancelled), actor.get('user_id'), actor.get('user_name', '本地用户'), hub.now()))


def create_team(name, member_ids, request_key, description='', routing_mode='auto'):
    _write_allowed()
    name, description = hub._text(name, '团队名称', 120), hub._text(description, '团队说明', 4000, empty=True)
    routing_mode = _mode(routing_mode)
    with hub._transaction() as conn:
        member_ids = _members(conn, member_ids)
        key, fingerprint = _key(request_key, {'name': name, 'description': description, 'member_ids': member_ids, 'routing_mode': routing_mode})
        old = _existing(conn, 'team.create', key, fingerprint)
        if old:
            return team(conn, old)
        team_id, room_id, stamp = uuid4().hex, uuid4().hex, hub.now()
        actor = REQUEST_ACTOR.get() or {}
        conn.execute('INSERT INTO hub_rooms (id,title,created_at,updated_at,team_id) VALUES (?,?,?,?,?)', (room_id, name, stamp, stamp, team_id))
        conn.execute('INSERT INTO hub_teams VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (team_id, name, description, 1, hub._json(member_ids), routing_mode, room_id, None, actor.get('user_id'), actor.get('user_name', '本地用户'), stamp, stamp))
        _revision(conn, team_id, member_ids, [], [])
        _save_key(conn, 'team.create', key, fingerprint, team_id)
        hub._event(conn, room_id, None, None, 'team.created', '已创建团队会话')
        return team(conn, team_id)


def _cancel_affected(conn, team_id, removed=None):
    cancelled = []
    for run in _unfinished(conn, team_id):
        if removed is not None and not set(json.loads(run['team_members_json'])).intersection(removed):
            continue
        hub._cancel_run(conn, run['id'], 'team_archived' if removed is None else 'team_members_changed')
        cancelled.append(run['id'])
    return cancelled


def update_team(team_id, base_version, **changes):
    _write_allowed()
    if set(changes) - {'name', 'description', 'member_ids', 'routing_mode'}:
        raise hub.HubError('团队只支持修改名称、说明、成员及自动分工模式')
    with hub._transaction() as conn:
        row = hub._find(conn, 'hub_teams', team_id, '团队')
        hub._version(base_version, row['version'], '团队')
        if row['archived_at']:
            raise hub.HubError('团队已归档，无法修改', 409)
        old_members = json.loads(row['member_ids_json'])
        name = hub._text(changes.get('name', row['name']), '团队名称', 120)
        description = hub._text(changes.get('description', row['description']), '团队说明', 4000, empty=True)
        member_ids = _members(conn, changes.get('member_ids', old_members))
        routing_mode = _mode(changes.get('routing_mode', row['routing_mode']))
        if (name, description, member_ids, routing_mode) == (row['name'], row['description'], old_members, row['routing_mode']):
            return dict(team(conn, team_id), cancelled_run_ids=[])
        removed, added = sorted(set(old_members) - set(member_ids)), sorted(set(member_ids) - set(old_members))
        cancelled = _cancel_affected(conn, team_id, removed) if removed else []
        stamp = hub.now()
        conn.execute('UPDATE hub_teams SET name=?,description=?,version=version+1,member_ids_json=?,routing_mode=?,updated_at=? WHERE id=?', (name, description, hub._json(member_ids), routing_mode, stamp, team_id))
        conn.execute('UPDATE hub_rooms SET title=? WHERE id=?', (name, row['primary_room_id']))
        _revision(conn, team_id, added, removed, cancelled)
        hub._event(conn, row['primary_room_id'], None, None, 'team.updated', f'已更新团队；停止 {len(cancelled)} 次受影响的协作')
        return dict(team(conn, team_id), cancelled_run_ids=cancelled)


def archive_team(team_id, base_version):
    _write_allowed()
    with hub._transaction() as conn:
        row = hub._find(conn, 'hub_teams', team_id, '团队')
        hub._version(base_version, row['version'], '团队')
        if row['archived_at']:
            return dict(team(conn, team_id), cancelled_run_ids=[])
        cancelled = _cancel_affected(conn, team_id)
        stamp = hub.now()
        conn.execute('UPDATE hub_teams SET version=version+1,archived_at=?,updated_at=? WHERE id=?', (stamp, stamp, team_id))
        _revision(conn, team_id, [], [], cancelled)
        hub._event(conn, row['primary_room_id'], None, None, 'team.archived', '团队已归档，历史成果已保留')
        return dict(team(conn, team_id), cancelled_run_ids=cancelled)


def list_history(team_id, after=0, limit=50):
    if isinstance(after, bool) or not isinstance(after, int) or not 0 <= after <= 9223372036854775807:
        raise hub.HubError('历史游标无效')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise hub.HubError('历史分页大小须为 1–100')
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_teams', team_id, '团队')
        rows = conn.execute('SELECT * FROM hub_team_revisions WHERE team_id=? AND version>? ORDER BY version LIMIT ?', (team_id, after, limit + 1)).fetchall()
        items = []
        for row in rows[:limit]:
            item = json.loads(row['snapshot_json'])
            item.update(added_member_ids=json.loads(row['added_json']), removed_member_ids=json.loads(row['removed_json']), cancelled_run_ids=json.loads(row['cancelled_json']), actor_id=row['actor_id'], actor_name=row['actor_name'], created_at=row['created_at'])
            items.append(item)
        return {'history': items, 'cursor': items[-1]['version'] if items else after, 'has_more': len(rows) > limit}


def for_room(conn, room_id, active=False):
    room = hub._find(conn, 'hub_rooms', room_id, '会话')
    if not room['team_id']:
        return None
    row = hub._find(conn, 'hub_teams', room['team_id'], '团队')
    if row['primary_room_id'] != room_id:
        raise hub.HubError('团队会话关联无效', 409)
    if active and row['archived_at']:
        raise hub.HubError('团队已归档，无法开始新的协作', 409)
    return row


def _targets(row, agent_id, target_agent_ids):
    if target_agent_ids is not None:
        if agent_id not in (None, 'auto'):
            raise hub.HubError('请只使用 agent_id 或 target_agent_ids 其中一种点名方式')
        if not isinstance(target_agent_ids, list) or len(target_agent_ids) > 12 or any(not isinstance(item, str) for item in target_agent_ids) or len(set(target_agent_ids)) != len(target_agent_ids):
            raise hub.HubError('点名目标须为最多 12 个不同的团队成员')
        targets = sorted(target_agent_ids)
    else:
        targets = [] if agent_id in (None, 'auto') else [agent_id]
    if any(agent not in json.loads(row['member_ids_json']) for agent in targets):
        raise hub.HubError('只能点名当前团队成员', 409)
    return targets


def run_snapshot(conn, room_id, agent_id, template=None, target_agent_ids=None):
    """Called inside the run transaction, including schedules/modifications."""
    row = for_room(conn, room_id, active=True)
    if row is None:
        if target_agent_ids is not None:
            raise hub.HubError('target_agent_ids 只适用于团队会话')
        return None, None, None
    members = json.loads(row['member_ids_json'])
    if template is not None:
        targets = sorted({step['agent_id'] for step in template['steps']})
        if not set(targets).issubset(members):
            raise hub.HubError('工作流包含不在当前团队中的 Agent', 409)
        mode = 'workflow'
    else:
        targets = _targets(row, agent_id, target_agent_ids)
        mode = 'direct' if targets else 'auto'
    routing = {'mode': mode, 'target_agent_ids': targets, 'team_id': row['id'], 'team_version': row['version']}
    return row, routing, targets


def assert_assignment(conn, run, agent_id):
    if not run['team_id']:
        return
    row = hub._find(conn, 'hub_teams', run['team_id'], '团队')
    if run['cancelled'] or row['archived_at']:
        raise hub.HubError('团队协作已停止', 409)
    routing = json.loads(run['routing_json'])
    if agent_id == 'coordinator' and routing['mode'] == 'auto':
        return
    allowed = set(json.loads(run['team_members_json'])) & set(json.loads(row['member_ids_json']))
    if agent_id not in allowed:
        raise hub.HubError('Agent 不在此次协作允许的团队成员范围内', 409)


def context_for_run(conn, run):
    if not run['team_id']:
        return None
    row = hub._find(conn, 'hub_teams', run['team_id'], '团队')
    members = json.loads(row['member_ids_json'])
    allowed = sorted(set(members) & set(json.loads(run['team_members_json'])))
    return {'id': row['id'], 'name': row['name'], 'version': row['version'], 'run_version': run['team_version'], 'member_ids': members, 'allowed_agent_ids': allowed, 'routing_mode': row['routing_mode'], 'routing_policy': 'Independent bounded routing after an explicit user request. Delegate only to allowed_agent_ids. Agent output does not trigger another conversation turn. Membership is not authorization for host tools or external actions.'}


def _send(conn, room_id, text, agent_id, target_agent_ids, request_key, base_team_version):
    _write_allowed()
    text = hub._text(text, '消息')
    key, fingerprint = _key(request_key, {'text': text, 'agent_id': agent_id, 'target_agent_ids': target_agent_ids, 'base_team_version': base_team_version})
    scope = 'team.message:' + room_id
    if _existing(conn, scope, key, fingerprint):
        return hub._room(conn, room_id)
    row = for_room(conn, room_id, active=True)
    if row is None:
        raise hub.HubError('此会话不属于团队', 409)
    if base_team_version is not None:
        hub._version(base_team_version, row['version'], '团队')
    targets = _targets(row, agent_id, target_agent_ids)
    if targets or row['routing_mode'] == 'auto':
        hub._new_run(conn, room_id, text, agent_id, target_agent_ids=target_agent_ids)
    else:
        actor = REQUEST_ACTOR.get() or {}
        routing = {'mode': 'note', 'target_agent_ids': [], 'team_id': row['id'], 'team_version': row['version']}
        conn.execute('INSERT INTO hub_messages (id,room_id,role,text,created_at,author_id,author_name,routing_json) VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, room_id, 'user', text, hub.now(), actor.get('user_id'), actor.get('user_name', '本地用户'), hub._json(routing)))
        hub._event(conn, room_id, None, None, 'message.saved', '已保存消息；未点名成员，没有创建任务')
    _save_key(conn, scope, key, fingerprint, room_id)
    return hub._room(conn, room_id)


def open_room(team_id, text=None, agent_id='auto', target_agent_ids=None, request_key=None, base_team_version=None):
    with hub._transaction() as conn:
        row = hub._find(conn, 'hub_teams', team_id, '团队')
        if text is None:
            return hub._room(conn, row['primary_room_id'])
        return _send(conn, row['primary_room_id'], text, agent_id, target_agent_ids, request_key, base_team_version)
