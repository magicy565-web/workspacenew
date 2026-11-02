"""Versioned host guidance and durable recurring work in the selected workspace."""
from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import json
from uuid import uuid4

from . import core, hub

MAX_SKILL = 256 * 1024


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS hub_skills (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,description TEXT NOT NULL,content TEXT NOT NULL,
            source_url TEXT,version INTEGER NOT NULL,sha256 TEXT NOT NULL,created_by TEXT,workspace_id TEXT,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT
        );
        CREATE TABLE IF NOT EXISTS hub_skill_versions (
            skill_id TEXT NOT NULL REFERENCES hub_skills(id),version INTEGER NOT NULL,name TEXT NOT NULL,
            description TEXT NOT NULL,content TEXT NOT NULL,source_url TEXT,sha256 TEXT NOT NULL,
            created_by TEXT,created_at TEXT NOT NULL,PRIMARY KEY(skill_id,version)
        );
        CREATE TABLE IF NOT EXISTS hub_agent_skills (
            agent_id TEXT PRIMARY KEY REFERENCES hub_agents(id),version INTEGER NOT NULL,
            skill_ids_json TEXT NOT NULL,updated_at TEXT NOT NULL,updated_by TEXT
        );
        CREATE TABLE IF NOT EXISTS hub_schedules (
            id TEXT PRIMARY KEY,name TEXT NOT NULL,text TEXT NOT NULL,agent_id TEXT NOT NULL,
            template_id TEXT,interval_seconds INTEGER NOT NULL,next_run_at TEXT NOT NULL,
            room_id TEXT REFERENCES hub_rooms(id),owner_user_id TEXT,owner_workspace_id TEXT,
            version INTEGER NOT NULL DEFAULT 1,enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,archived_at TEXT,
            last_run_id TEXT REFERENCES hub_runs(id),last_fired_at TEXT,last_error TEXT,pause_reason TEXT
        );
        CREATE INDEX IF NOT EXISTS hub_schedules_due ON hub_schedules(enabled,archived_at,next_run_at);
        CREATE TABLE IF NOT EXISTS hub_schedule_fires (
            id TEXT PRIMARY KEY,schedule_id TEXT NOT NULL REFERENCES hub_schedules(id),scheduled_for TEXT NOT NULL,
            trigger TEXT NOT NULL,status TEXT NOT NULL,run_id TEXT REFERENCES hub_runs(id),error TEXT,created_at TEXT NOT NULL,
            UNIQUE(schedule_id,scheduled_for,trigger)
        );
    ''')


def _actor():
    variable = getattr(core, 'REQUEST_ACTOR', None)
    return variable.get() if variable is not None else None


def _identity():
    actor = _actor()
    return (actor.get('user_id'), actor.get('workspace_id')) if actor else (None, None)


def _skill_input(name, description, content, source_url):
    name = hub._text(name, '技能名称', 80)
    description = hub._text(description, '技能简介', 500, empty=True)
    content = hub._text(content, '技能内容', MAX_SKILL, empty=True)
    if not content.strip() or len(content.encode('utf-8')) > MAX_SKILL:
        raise hub.HubError('技能内容不能为空或超过 256 KiB')
    source_url = hub._source_url(source_url) if source_url is not None and source_url != '' else None
    return name, description, content, source_url, hashlib.sha256(content.encode('utf-8')).hexdigest()


def _skill(conn, skill_id, version=None, full=True):
    row = hub._find(conn, 'hub_skills', skill_id, '技能')
    result = {key: row[key] for key in row.keys() if key != 'content'}
    if full:
        target = row['version'] if version is None else version
        if isinstance(target, bool) or not isinstance(target, int) or target < 1:
            raise hub.HubError('技能版本无效')
        saved = conn.execute('SELECT * FROM hub_skill_versions WHERE skill_id=? AND version=?', (skill_id, target)).fetchone()
        if saved is None:
            raise hub.HubError('技能版本不存在', 404)
        result.update({key: saved[key] for key in ('version', 'name', 'description', 'content', 'source_url', 'sha256')})
        result['versions'] = [dict(item) for item in conn.execute('SELECT version,sha256,created_at,created_by FROM hub_skill_versions WHERE skill_id=? ORDER BY version DESC', (skill_id,))]
    return result


def list_skills():
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return [_skill(conn, row['id'], full=False) for row in conn.execute('SELECT id FROM hub_skills ORDER BY updated_at DESC,rowid DESC')]


def get_skill(skill_id, version=None):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return _skill(conn, skill_id, version)


def _save_skill_version(conn, skill_id, version, values, actor, stamp):
    name, description, content, source_url, digest = values
    conn.execute('INSERT INTO hub_skill_versions (skill_id,version,name,description,content,source_url,sha256,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)', (skill_id, version, name, description, content, source_url, digest, actor, stamp))


def create_skill(name, description, content, source_url=None):
    values = _skill_input(name, description, content, source_url)
    actor, workspace = _identity()
    with hub._transaction() as conn:
        skill_id, stamp = uuid4().hex, hub.now()
        conn.execute('INSERT INTO hub_skills (id,name,description,content,source_url,version,sha256,created_by,workspace_id,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?,?,?,?)', (skill_id, *values[:4], values[4], actor, workspace, stamp, stamp))
        _save_skill_version(conn, skill_id, 1, values, actor, stamp)
        return _skill(conn, skill_id)


def update_skill(skill_id, base_version, name, description, content, source_url=None):
    values = _skill_input(name, description, content, source_url)
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_skills', skill_id, '技能')
        hub._version(base_version, current['version'], '技能')
        if current['archived_at']:
            raise hub.HubError('技能已归档，请创建新的技能', 409)
        version, stamp = current['version'] + 1, hub.now()
        conn.execute('UPDATE hub_skills SET name=?,description=?,content=?,source_url=?,sha256=?,version=?,updated_at=? WHERE id=?', (*values, version, stamp, skill_id))
        _save_skill_version(conn, skill_id, version, values, _identity()[0], stamp)
        return _skill(conn, skill_id)


def archive_skill(skill_id, base_version):
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_skills', skill_id, '技能')
        hub._version(base_version, current['version'], '技能')
        if not current['archived_at']:
            stamp = hub.now()
            conn.execute('UPDATE hub_skills SET archived_at=?,updated_at=? WHERE id=?', (stamp, stamp, skill_id))
        return _skill(conn, skill_id)


def bound_skills(conn, agent_id, full=False):
    hub._find(conn, 'hub_agents', agent_id, 'Agent')
    row = conn.execute('SELECT * FROM hub_agent_skills WHERE agent_id=?', (agent_id,)).fetchone()
    return {'agent_id': agent_id, 'version': row['version'] if row else 0, 'skills': [_skill(conn, skill_id, full=full) for skill_id in json.loads(row['skill_ids_json'])] if row else []}


def get_agent_skills(agent_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return bound_skills(conn, agent_id)


def set_agent_skills(agent_id, base_version, skill_ids):
    if not isinstance(skill_ids, list) or len(skill_ids) > 12 or any(not isinstance(item, str) for item in skill_ids) or len(skill_ids) != len(set(skill_ids)):
        raise hub.HubError('请选择最多 12 个不重复的技能')
    with hub._transaction() as conn:
        current = bound_skills(conn, agent_id)
        hub._version(base_version, current['version'], '技能绑定')
        for skill_id in skill_ids:
            if hub._find(conn, 'hub_skills', skill_id, '技能')['archived_at']:
                raise hub.HubError('不能绑定已归档的技能', 409)
        conn.execute('INSERT INTO hub_agent_skills (agent_id,version,skill_ids_json,updated_at,updated_by) VALUES (?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET version=excluded.version,skill_ids_json=excluded.skill_ids_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by', (agent_id, current['version'] + 1, hub._json(skill_ids), hub.now(), _identity()[0]))
        return bound_skills(conn, agent_id)


def _timestamp(value, label='下次执行时间'):
    if not isinstance(value, str) or len(value) > 80:
        raise hub.HubError(f'{label}须为包含时区的 ISO 8601 时间')
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError('missing timezone')
        return stamp.astimezone(timezone.utc).isoformat(timespec='microseconds')
    except (ValueError, OverflowError) as exc:
        raise hub.HubError(f'{label}须为包含时区的 ISO 8601 时间') from exc


def _schedule_input(conn, name, text, interval_seconds, agent_id, template_id, next_run_at, room_id):
    name = hub._text(name, '计划名称', 120)
    text = hub._text(text, '计划要求')
    if isinstance(interval_seconds, bool) or not isinstance(interval_seconds, int) or not 60 <= interval_seconds <= 31536000:
        raise hub.HubError('执行间隔须为 60–31536000 秒的整数')
    if agent_id != 'auto':
        hub._find(conn, 'hub_agents', agent_id, 'Agent')
    if template_id is not None:
        hub._find(conn, 'hub_templates', template_id, '团队')
    if room_id is not None:
        hub._find(conn, 'hub_rooms', room_id, '会话')
    next_run_at = _timestamp(next_run_at) if next_run_at is not None else (datetime.now(timezone.utc) + timedelta(seconds=interval_seconds)).isoformat(timespec='microseconds')
    return name, text, interval_seconds, agent_id, template_id, next_run_at, room_id


def _owner_permission(user_id, workspace_id):
    from . import accounts
    if user_id is None and workspace_id is None:
        return None if not accounts.configured() else '此计划创建于本地模式，请登录后重新创建'
    if not user_id or not workspace_id:
        return '计划创建者身份不完整'
    with contextlib.closing(accounts.db()) as conn:
        member = conn.execute('SELECT m.role,w.storage_key FROM members m JOIN workspaces w ON w.id=m.workspace_id WHERE m.workspace_id=? AND m.user_id=?', (workspace_id, user_id)).fetchone()
    if not member or member['role'] not in ('owner', 'editor'):
        return '计划创建者已失去执行权限'
    try:
        expected = accounts.data_directory({'workspace': {'storage_key': member['storage_key']}})
    except hub.HubError:
        return '工作空间存储配置无效'
    if expected.resolve() != core.data_root().resolve():
        return '计划创建者不属于当前工作空间'
    return None


def _schedule(conn, row, history=False):
    result = dict(row)
    result['enabled'] = bool(row['enabled'])
    result['last_run_status'] = None
    if row['last_run_id']:
        run = conn.execute('SELECT * FROM hub_runs WHERE id=?', (row['last_run_id'],)).fetchone()
        if run:
            state = hub._run(conn, run)
            result['last_run_status'] = state['status']
            if not result['last_error'] and state['status'] == 'failed':
                result['last_error'] = next((task['error'] for task in state['tasks'] if task['error']), '最近一次执行失败')
    result['run_count'] = conn.execute("SELECT COUNT(*) AS n FROM hub_schedule_fires WHERE schedule_id=? AND status='created'", (row['id'],)).fetchone()['n']
    if history:
        result['history'] = [dict(item) for item in conn.execute('SELECT id,scheduled_for,trigger,status,run_id,error,created_at FROM hub_schedule_fires WHERE schedule_id=? ORDER BY rowid DESC LIMIT 30', (row['id'],))]
    return result


def list_schedules():
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return [_schedule(conn, row) for row in conn.execute('SELECT * FROM hub_schedules ORDER BY created_at DESC,rowid DESC')]


def get_schedule(schedule_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'), history=True)


def create_schedule(name, text, interval_seconds, agent_id='auto', template_id=None, next_run_at=None, room_id=None):
    actor, workspace = _identity()
    denied = _owner_permission(actor, workspace)
    if denied:
        raise hub.HubError(denied, 403)
    with hub._transaction() as conn:
        values = _schedule_input(conn, name, text, interval_seconds, agent_id, template_id, next_run_at, room_id)
        schedule_id, stamp = uuid4().hex, hub.now()
        conn.execute('INSERT INTO hub_schedules (id,name,text,interval_seconds,agent_id,template_id,next_run_at,room_id,owner_user_id,owner_workspace_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (schedule_id, *values, actor, workspace, stamp, stamp))
        return _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'))


def update_schedule(schedule_id, base_version, name, text, interval_seconds, agent_id='auto', template_id=None, next_run_at=None, room_id=None):
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_schedules', schedule_id, '计划')
        hub._version(base_version, current['version'], '计划')
        if current['archived_at']:
            raise hub.HubError('计划已归档', 409)
        if current['last_run_id'] and room_id is not None and room_id != current['room_id']:
            raise hub.HubError('已经执行的计划不能更换会话', 409)
        values = _schedule_input(conn, name, text, interval_seconds, agent_id, template_id, next_run_at if next_run_at is not None else current['next_run_at'], room_id if room_id is not None else current['room_id'])
        conn.execute('UPDATE hub_schedules SET name=?,text=?,interval_seconds=?,agent_id=?,template_id=?,next_run_at=?,room_id=?,version=version+1,updated_at=? WHERE id=?', (*values, hub.now(), schedule_id))
        return _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'))


def pause_schedule(schedule_id, paused=True):
    if not isinstance(paused, bool):
        raise hub.HubError('paused 须为布尔值')
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_schedules', schedule_id, '计划')
        if current['archived_at']:
            raise hub.HubError('计划已归档', 409)
        if not paused:
            denied = _owner_permission(current['owner_user_id'], current['owner_workspace_id'])
            if denied:
                raise hub.HubError(denied, 403)
        conn.execute('UPDATE hub_schedules SET enabled=?,pause_reason=?,version=version+1,updated_at=? WHERE id=?', (0 if paused else 1, '已手动暂停' if paused else None, hub.now(), schedule_id))
        return _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'))


def archive_schedule(schedule_id):
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_schedules', schedule_id, '计划')
        if not current['archived_at']:
            stamp = hub.now()
            conn.execute('UPDATE hub_schedules SET enabled=0,archived_at=?,pause_reason=?,version=version+1,updated_at=? WHERE id=?', (stamp, '已归档', stamp, schedule_id))
        return _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'))


def _fire_record(conn, schedule_id, scheduled_for, trigger, status, run_id=None, error=None):
    conn.execute('INSERT INTO hub_schedule_fires (id,schedule_id,scheduled_for,trigger,status,run_id,error,created_at) VALUES (?,?,?,?,?,?,?,?)', (uuid4().hex, schedule_id, scheduled_for, trigger, status, run_id, error, hub.now()))


def _fire(conn, row, scheduled_for, trigger):
    if conn.execute('SELECT 1 FROM hub_schedule_fires WHERE schedule_id=? AND scheduled_for=? AND trigger=?', (row['id'], scheduled_for, trigger)).fetchone():
        return None, 'already_recorded'
    denied = _owner_permission(row['owner_user_id'], row['owner_workspace_id'])
    if denied:
        conn.execute('UPDATE hub_schedules SET enabled=0,pause_reason=?,last_error=?,version=version+1,updated_at=? WHERE id=?', (denied, denied, hub.now(), row['id']))
        _fire_record(conn, row['id'], scheduled_for, trigger, 'failed', error=denied)
        return None, 'creator_permission_revoked'
    state = _schedule(conn, row)
    if state['last_run_status'] in ('queued', 'running'):
        _fire_record(conn, row['id'], scheduled_for, trigger, 'skipped', error='previous_run_active')
        return None, 'previous_run_active'
    conn.execute('SAVEPOINT schedule_start')
    try:
        room_id = row['room_id']
        stamp = hub.now()
        if room_id is None:
            room_id = uuid4().hex
            conn.execute('INSERT INTO hub_rooms (id,title,created_at,updated_at) VALUES (?,?,?,?)', (room_id, row['name'], stamp, stamp))
        actor_binding = None
        if trigger == 'scheduled':
            actor = {'user_id': row['owner_user_id'], 'workspace_id': row['owner_workspace_id'], 'user_name': '本地用户'}
            if row['owner_user_id']:
                from . import accounts
                with contextlib.closing(accounts.db()) as registry:
                    owner = registry.execute('SELECT name FROM users WHERE id=?', (row['owner_user_id'],)).fetchone()
                actor['user_name'] = owner['name'] if owner else '计划创建者'
            actor_binding = core.REQUEST_ACTOR.set(actor)
        try:
            run_id = hub._new_run(conn, room_id, row['text'], row['agent_id'], row['template_id'])
        finally:
            if actor_binding is not None:
                core.REQUEST_ACTOR.reset(actor_binding)
        conn.execute('UPDATE hub_schedules SET room_id=?,last_run_id=?,last_fired_at=?,last_error=NULL,updated_at=? WHERE id=?', (room_id, run_id, stamp, stamp, row['id']))
        _fire_record(conn, row['id'], scheduled_for, trigger, 'created', run_id)
        hub._event(conn, room_id, run_id, None, 'schedule.started', f'周期计划：{row["name"]}')
        conn.execute('RELEASE schedule_start')
        return run_id, None
    except hub.HubError as exc:
        conn.execute('ROLLBACK TO schedule_start')
        conn.execute('RELEASE schedule_start')
        error = str(exc)
        conn.execute('UPDATE hub_schedules SET last_error=?,updated_at=? WHERE id=?', (error, hub.now(), row['id']))
        _fire_record(conn, row['id'], scheduled_for, trigger, 'failed', error=error)
        return None, 'configuration_error'


def run_schedule_now(schedule_id):
    with hub._transaction() as conn:
        current = hub._find(conn, 'hub_schedules', schedule_id, '计划')
        if current['archived_at']:
            raise hub.HubError('计划已归档', 409)
        run_id, reason = _fire(conn, current, hub.now(), 'manual')
        schedule = _schedule(conn, hub._find(conn, 'hub_schedules', schedule_id, '计划'), history=True)
        return {'schedule': schedule, 'run_id': run_id, 'room_id': schedule['room_id'], 'skipped_reason': reason}


def tick_due(now_iso=None):
    stamp = _timestamp(now_iso, '调度时间') if now_iso is not None else hub.now()
    current_time = datetime.fromisoformat(stamp)
    output = {'created': [], 'skipped': [], 'paused': []}
    with hub._transaction() as conn:
        rows = conn.execute('SELECT * FROM hub_schedules WHERE enabled=1 AND archived_at IS NULL AND next_run_at<=? ORDER BY next_run_at,rowid LIMIT 100', (stamp,)).fetchall()
        for row in rows:
            due = datetime.fromisoformat(row['next_run_at'])
            skipped_intervals = int((current_time - due).total_seconds() // row['interval_seconds']) + 1
            try:
                next_stamp = (due + timedelta(seconds=skipped_intervals * row['interval_seconds'])).isoformat(timespec='microseconds')
            except OverflowError:
                conn.execute('UPDATE hub_schedules SET enabled=0,pause_reason=?,last_error=?,updated_at=? WHERE id=?', ('调度时间超出范围', '调度时间超出范围', hub.now(), row['id']))
                output['paused'].append({'schedule_id': row['id'], 'reason': 'invalid_next_run'})
                continue
            run_id, reason = _fire(conn, row, row['next_run_at'], 'scheduled')
            conn.execute('UPDATE hub_schedules SET next_run_at=?,updated_at=? WHERE id=?', (next_stamp, hub.now(), row['id']))
            fresh = hub._find(conn, 'hub_schedules', row['id'], '计划')
            if run_id:
                output['created'].append({'schedule_id': row['id'], 'run_id': run_id, 'room_id': fresh['room_id']})
            elif not fresh['enabled']:
                output['paused'].append({'schedule_id': row['id'], 'reason': reason})
            else:
                output['skipped'].append({'schedule_id': row['id'], 'reason': reason})
    return output
