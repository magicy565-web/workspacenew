"""Persistent room/Agent identity, logical ownership and completed native heads.

Task execution ledgers remain independent. This module does not deliver mailbox
messages, execute tools, or claim physical control of an external model host.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
from uuid import uuid4

from . import hub

MAX_DESCRIPTOR = 4096
COMPONENT_REASONS = (
    ('agent', 'agent_instructions_changed'),
    ('skills', 'skills_changed'),
    ('team', 'team_permissions_changed'),
    ('sources', 'source_visibility_changed'),
    ('background', 'shared_context_changed'),
)
BLOCK_MESSAGES = {
    'agent_session_busy': '此 Agent 正在处理同一会话的上一项任务。',
    'previous_task_recovery': '上一项任务尚未结束，请先恢复或终止该任务。',
    'external_outcome_unknown': '前一项任务的外部调用结果尚未确认，请打开执行记录核对。',
}


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS hub_agent_sessions (
            id TEXT PRIMARY KEY,room_id TEXT NOT NULL REFERENCES hub_rooms(id),
            agent_id TEXT NOT NULL REFERENCES hub_agents(id),generation INTEGER NOT NULL DEFAULT 0,
            current_task_id TEXT REFERENCES hub_tasks(id),last_completed_task_id TEXT REFERENCES hub_tasks(id),
            context_fingerprint TEXT,components_json TEXT,head_json TEXT,invalidated_reason TEXT,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,UNIQUE(room_id,agent_id)
        );
        CREATE TABLE IF NOT EXISTS hub_agent_session_turns (
            task_id TEXT PRIMARY KEY REFERENCES hub_tasks(id),session_id TEXT NOT NULL REFERENCES hub_agent_sessions(id),
            turn_index INTEGER NOT NULL,created_at TEXT NOT NULL,UNIQUE(session_id,turn_index)
        );
        CREATE TABLE IF NOT EXISTS hub_agent_session_attempts (
            task_id TEXT NOT NULL REFERENCES hub_tasks(id),attempt INTEGER NOT NULL,
            session_id TEXT NOT NULL REFERENCES hub_agent_sessions(id),generation INTEGER NOT NULL,
            worker_id TEXT NOT NULL,host_identity TEXT,context_fingerprint TEXT NOT NULL,
            components_json TEXT NOT NULL,continuation_json TEXT NOT NULL,expected_native_json TEXT,created_at TEXT NOT NULL,
            PRIMARY KEY(task_id,attempt)
        );
        CREATE TABLE IF NOT EXISTS hub_agent_delegations (
            id TEXT PRIMARY KEY,parent_task_id TEXT NOT NULL REFERENCES hub_tasks(id),
            child_task_id TEXT NOT NULL UNIQUE REFERENCES hub_tasks(id),
            parent_session_id TEXT NOT NULL REFERENCES hub_agent_sessions(id),
            child_session_id TEXT NOT NULL REFERENCES hub_agent_sessions(id),
            run_id TEXT NOT NULL REFERENCES hub_runs(id),created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS hub_agent_sessions_room ON hub_agent_sessions(room_id);
        CREATE INDEX IF NOT EXISTS hub_agent_attempts_session ON hub_agent_session_attempts(session_id,task_id,attempt);
        CREATE INDEX IF NOT EXISTS hub_agent_delegations_parent ON hub_agent_delegations(parent_task_id);
    ''')
    if 'expected_native_json' not in {row['name'] for row in conn.execute('PRAGMA table_info(hub_agent_session_attempts)')}:
        conn.execute('ALTER TABLE hub_agent_session_attempts ADD COLUMN expected_native_json TEXT')


def bind_task(conn, task_id, stamp):
    old = conn.execute('SELECT * FROM hub_agent_session_turns WHERE task_id=?', (task_id,)).fetchone()
    if old:
        return old
    task = conn.execute('SELECT t.agent_id,r.room_id FROM hub_tasks t JOIN hub_runs r ON r.id=t.run_id WHERE t.id=?', (task_id,)).fetchone()
    if task is None:
        raise hub.HubError('任务不存在', 404)
    conn.execute('INSERT OR IGNORE INTO hub_agent_sessions (id,room_id,agent_id,created_at,updated_at) VALUES (?,?,?,?,?)', ('ags_' + uuid4().hex, task['room_id'], task['agent_id'], stamp, stamp))
    session = conn.execute('SELECT id FROM hub_agent_sessions WHERE room_id=? AND agent_id=?', (task['room_id'], task['agent_id'])).fetchone()
    turn_index = conn.execute('SELECT COALESCE(MAX(turn_index),0)+1 FROM hub_agent_session_turns WHERE session_id=?', (session['id'],)).fetchone()[0]
    conn.execute('INSERT INTO hub_agent_session_turns VALUES (?,?,?,?)', (task_id, session['id'], turn_index, stamp))
    return conn.execute('SELECT * FROM hub_agent_session_turns WHERE task_id=?', (task_id,)).fetchone()


def backfill(conn):
    rows = conn.execute('SELECT t.id,t.created_at FROM hub_tasks t LEFT JOIN hub_agent_session_turns a ON a.task_id=t.id WHERE a.task_id IS NULL ORDER BY t.created_at,t.rowid').fetchall()
    for task in rows:
        bind_task(conn, task['id'], task['created_at'])
    if not rows:
        return
    # Legacy tasks have no captured environment fingerprint. Preserve identity
    # and outstanding ownership, but do not invent an eligible native head.
    conn.execute('''UPDATE hub_agent_sessions SET last_completed_task_id=(
        SELECT t.id FROM hub_tasks t JOIN hub_agent_session_turns a ON a.task_id=t.id
        WHERE a.session_id=hub_agent_sessions.id AND t.status='completed'
        ORDER BY t.updated_at DESC,t.rowid DESC LIMIT 1
    ) WHERE generation=0''')
    conn.execute('''UPDATE hub_agent_sessions SET current_task_id=(
        SELECT t.id FROM hub_tasks t JOIN hub_agent_session_turns a ON a.task_id=t.id JOIN hub_runs r ON r.id=t.run_id
        WHERE a.session_id=hub_agent_sessions.id AND t.attempts>0 AND t.status!='completed' AND r.cancelled=0
        ORDER BY a.turn_index LIMIT 1
    ) WHERE generation=0 AND current_task_id IS NULL''')


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def _components(conn, task, run, context):
    agent = hub._find(conn, 'hub_agents', task['agent_id'], 'Agent')
    bound = conn.execute('SELECT version,skill_ids_json FROM hub_agent_skills WHERE agent_id=?', (task['agent_id'],)).fetchone()
    skills = []
    if bound:
        for skill_id in json.loads(bound['skill_ids_json']):
            skills.append(dict(conn.execute('SELECT id,name,description,source_url,version,sha256,archived_at FROM hub_skills WHERE id=?', (skill_id,)).fetchone()))
    team = None
    if run['team_id']:
        row = hub._find(conn, 'hub_teams', run['team_id'], '团队')
        team = {'id': row['id'], 'members': sorted(json.loads(row['member_ids_json'])), 'frozen_members': sorted(json.loads(run['team_members_json'])), 'archived_at': row['archived_at']}
    sources = [dict(row) for row in conn.execute('SELECT id,title,kind,source_url,version,sha256 FROM hub_sources WHERE room_id=? AND revoked_at IS NULL ORDER BY id', (run['room_id'],))]
    files = [dict(row) for row in conn.execute('SELECT f.id,f.source_id,f.sha256,f.extraction_status FROM hub_files f JOIN hub_sources s ON s.id=f.source_id WHERE f.room_id=? AND s.revoked_at IS NULL ORDER BY f.id', (run['room_id'],))]
    room = hub._find(conn, 'hub_rooms', run['room_id'], '会话')
    return {
        'agent': _digest({'current': dict(agent), 'effective': context['agent']}),
        'skills': _digest({'binding_version': bound['version'] if bound else 0, 'skills': skills}),
        'team': _digest(team), 'sources': _digest({'sources': sources, 'files': files}),
        'background': _digest(room['context']),
    }


def _changed(previous, current):
    if not previous:
        return 'context_snapshot_unavailable'
    for component, reason in COMPONENT_REASONS:
        if previous.get(component) != current[component]:
            return reason
    return None


def termination_reviewed(conn, task_id):
    for event in conn.execute("SELECT payload_json FROM hub_execution_events WHERE session_id=? AND kind='recovery.reviewed'", ('exec_' + task_id,)):
        if json.loads(event['payload_json']).get('decision') == 'terminate':
            return True
    return False


def unresolved(conn, task_id):
    from . import hub_execution
    return bool(hub_execution._unsafe(hub_execution._calls(conn, 'exec_' + task_id))) and not termination_reviewed(conn, task_id)


def _block(conn, session):
    candidates = [session['current_task_id']] if session['current_task_id'] else []
    # Migration can encounter several older cancelled/failed tasks whose tool
    # outcomes remain unknown. Releasing one must not hide another unresolved
    # receipt merely because the old version had no logical-session lock.
    rows = conn.execute('''SELECT DISTINCT t.id,a.turn_index FROM hub_agent_session_turns a
        JOIN hub_tasks t ON t.id=a.task_id JOIN hub_execution_sessions e ON e.task_id=t.id
        JOIN hub_execution_calls c ON c.session_id=e.id
        WHERE a.session_id=? AND t.status!='completed' AND c.retry_safe=0 AND c.state IN ('pending','unknown')
        ORDER BY a.turn_index''', (session['id'],))
    candidates.extend(row['id'] for row in rows if row['id'] not in candidates)
    for task_id in candidates:
        task = hub._find(conn, 'hub_tasks', task_id, '任务')
        run = hub._find(conn, 'hub_runs', task['run_id'], '运行')
        active = not run['cancelled'] and task['status'] == 'running' and task['lease_expires_at'] and task['lease_expires_at'] > hub.now()
        if active:
            reason = 'agent_session_busy'
        elif unresolved(conn, task['id']):
            reason = 'external_outcome_unknown'
        elif not run['cancelled'] and task['status'] in ('running', 'queued') and task['attempts']:
            reason = 'previous_task_recovery'
        else:
            continue
        return {'id': session['id'], 'room_id': session['room_id'], 'agent_id': session['agent_id'], 'blocked_by_task_id': task['id'], 'execution_id': 'exec_' + task['id'], 'reason': reason, 'message': BLOCK_MESSAGES[reason]}
    return None


def claim_blocker(conn, task):
    turn = bind_task(conn, task['id'], task['created_at'])
    session = hub._find(conn, 'hub_agent_sessions', turn['session_id'], 'Agent 会话')
    if session['current_task_id'] == task['id']:
        return None  # Existing task recovery remains the authority for this task.
    blocked = _block(conn, session)
    if blocked:
        if session['current_task_id'] != blocked['blocked_by_task_id']:
            conn.execute('UPDATE hub_agent_sessions SET current_task_id=?,head_json=NULL,invalidated_reason=? WHERE id=?', (blocked['blocked_by_task_id'], 'previous_turn_incomplete', session['id']))
        return blocked
    if session['current_task_id']:
        # A terminal legacy/reconciled task can release ownership. Its partial
        # native context cannot become a continuation source for another task.
        conn.execute('UPDATE hub_agent_sessions SET current_task_id=NULL,head_json=NULL,invalidated_reason=?,updated_at=? WHERE id=?', ('previous_turn_incomplete', hub.now(), session['id']))
    return None


def current_blocker(conn, task_id):
    row = conn.execute('SELECT s.* FROM hub_agent_sessions s JOIN hub_agent_session_turns t ON t.session_id=s.id WHERE t.task_id=?', (task_id,)).fetchone()
    return _block(conn, row) if row else None


def assert_owned(conn, task):
    row = conn.execute('SELECT s.current_task_id,s.generation,a.generation AS attempt_generation FROM hub_agent_session_turns t JOIN hub_agent_sessions s ON s.id=t.session_id LEFT JOIN hub_agent_session_attempts a ON a.task_id=t.task_id AND a.attempt=? WHERE t.task_id=?', (task['attempts'], task['id'])).fetchone()
    if row and (row['current_task_id'] != task['id'] or (row['attempt_generation'] is not None and row['attempt_generation'] != row['generation'])):
        raise hub.HubError('Agent 会话已由另一任务接手，旧执行回执无效', 409)


def prepare_claim(conn, task, run, worker_id, capabilities, host_identity, recovery, context):
    """Called inside the lease transaction; may invalidate native continuation."""
    turn = bind_task(conn, task['id'], task['created_at'])
    session = hub._find(conn, 'hub_agent_sessions', turn['session_id'], 'Agent 会话')
    components = _components(conn, task, run, context)
    fingerprint = _digest(components)
    previous_components = json.loads(session['components_json']) if session['components_json'] else None
    changed = _changed(previous_components, components)
    head = json.loads(session['head_json']) if session['head_json'] else None
    generation = session['generation']
    continuation = {'decision': 'fresh', 'reason': 'first_turn', 'from_task_id': None, 'from_execution_id': None, 'checkpoint': None}
    if task['attempts']:
        prior = conn.execute('SELECT * FROM hub_agent_session_attempts WHERE task_id=? ORDER BY attempt DESC LIMIT 1', (task['id'],)).fetchone()
        continuation.update(decision='task_recovery', reason='task_recovery_priority')
        incompatible = prior is None or prior['generation'] != generation or prior['context_fingerprint'] != fingerprint
        if recovery['decision'] == 'continue' and incompatible:
            recovery.update(decision='restart', reason=changed or 'context_snapshot_unavailable')
        if recovery['decision'] == 'restart' or incompatible:
            generation += 1
            head = None
    else:
        if not generation:
            continuation['reason'] = 'no_completed_checkpoint' if session['last_completed_task_id'] else 'first_turn'
        elif changed:
            continuation['reason'] = changed
        elif session['invalidated_reason']:
            continuation['reason'] = session['invalidated_reason']
        elif head is None or head['task_id'] != session['last_completed_task_id']:
            continuation['reason'] = 'no_completed_checkpoint'
        elif head['worker_id'] != worker_id:
            continuation['reason'] = 'worker_changed'
        elif host_identity is None or head['host_identity'] != host_identity:
            continuation['reason'] = 'host_changed'
        elif 'continuation:' + head['host'] not in capabilities:
            continuation['reason'] = 'continuation_unsupported'
        else:
            point = {key: head[key] for key in ('host', 'session_id', 'host_identity', 'worker_id', 'checkpoint_id')}
            continuation.update(decision='continue', reason='matching_completed_turn', from_task_id=head['task_id'], from_execution_id=head['execution_id'], checkpoint=point)
        if continuation['decision'] != 'continue':
            generation += 1
            head = None
    generation = max(1, generation)
    descriptor = {'id': session['id'], 'room_id': run['room_id'], 'agent_id': task['agent_id'], 'generation': generation, 'turn_index': turn['turn_index'], 'context_fingerprint': fingerprint, 'continuation': continuation}
    if len(hub._json(descriptor).encode('utf-8')) > MAX_DESCRIPTOR:
        raise hub.HubError('Agent 会话续接信息超过 4 KiB；未领取任务', 413)
    stamp = hub.now()
    conn.execute('UPDATE hub_agent_sessions SET generation=?,current_task_id=?,context_fingerprint=?,components_json=?,head_json=?,invalidated_reason=NULL,updated_at=? WHERE id=?', (generation, task['id'], fingerprint, hub._json(components), hub._json(head) if head else None, stamp, session['id']))
    expected = recovery.get('checkpoint') if recovery['decision'] == 'continue' else continuation['checkpoint']
    expected = {key: expected[key] for key in ('host', 'session_id', 'host_identity')} if expected else None
    conn.execute('INSERT INTO hub_agent_session_attempts (task_id,attempt,session_id,generation,worker_id,host_identity,context_fingerprint,components_json,continuation_json,expected_native_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)', (task['id'], task['attempts'] + 1, session['id'], generation, worker_id, host_identity, fingerprint, hub._json(components), hub._json(continuation), hub._json(expected) if expected else None, stamp))
    return descriptor


def assert_host_session(conn, task, payload):
    """A requested continuation must report the exact selected native identity."""
    from . import hub_execution
    saved = conn.execute('SELECT * FROM hub_agent_session_attempts WHERE task_id=? AND attempt=?', (task['id'], task['attempts'])).fetchone()
    if saved is None:
        return
    point = json.loads(saved['expected_native_json']) if saved['expected_native_json'] else None
    if point and any(payload[key] != point[key] for key in ('host', 'session_id', 'host_identity')):
        raise hub.HubError('宿主返回的会话与已选择的续接检查点不一致', 409)
    if point is None:
        rows = conn.execute("SELECT e.payload_json FROM hub_execution_events e JOIN hub_execution_sessions x ON x.id=e.session_id JOIN hub_agent_session_attempts a ON a.task_id=x.task_id AND a.attempt=e.attempt WHERE a.session_id=? AND a.generation!=? AND e.kind='host_session'", (saved['session_id'], saved['generation']))
        for row in rows:
            previous = json.loads(row['payload_json'])
            if all(previous[key] == payload[key] for key in ('host', 'session_id', 'host_identity')):
                raise hub.HubError('上下文已换代，不能重新使用该旧宿主会话', 409)


def record_terminal(conn, task, kind):
    from . import hub_execution
    turn = bind_task(conn, task['id'], task['created_at'])
    session = hub._find(conn, 'hub_agent_sessions', turn['session_id'], 'Agent 会话')
    if kind == 'task.completed':
        calls = hub_execution._calls(conn, 'exec_' + task['id'])
        point = hub_execution._checkpoint(conn, 'exec_' + task['id'], calls)
        head = None
        if point is not None:
            event = conn.execute('SELECT attempt FROM hub_execution_events WHERE session_id=? AND seq=?', ('exec_' + task['id'], point['event_seq'])).fetchone()
            origin = conn.execute('SELECT * FROM hub_agent_session_attempts WHERE task_id=? AND attempt=?', (task['id'], event['attempt'])).fetchone()
            host_attempt = conn.execute('SELECT capabilities_json FROM hub_execution_attempts WHERE session_id=? AND attempt=?', ('exec_' + task['id'], event['attempt'])).fetchone()
            if (origin and origin['generation'] == session['generation'] and origin['context_fingerprint'] == session['context_fingerprint']
                    and origin['host_identity'] is not None and origin['host_identity'] == point['host_identity']
                    and host_attempt and 'continuation:' + point['host'] in json.loads(host_attempt['capabilities_json'])):
                head = {key: point[key] for key in ('host', 'session_id', 'host_identity', 'worker_id', 'checkpoint_id')}
                head.update(task_id=task['id'], execution_id='exec_' + task['id'])
        # Every completed task advances the head, even when it cannot continue.
        # This is what prevents returning later to an older worker/native branch.
        conn.execute('UPDATE hub_agent_sessions SET current_task_id=NULL,last_completed_task_id=?,head_json=?,invalidated_reason=?,updated_at=? WHERE id=?', (task['id'], hub._json(head) if head else None, None if head else 'no_completed_checkpoint', hub.now(), session['id']))
    elif task['attempts'] and session['current_task_id'] == task['id']:
        keep = task['id'] if unresolved(conn, task['id']) else None
        conn.execute('UPDATE hub_agent_sessions SET current_task_id=?,head_json=NULL,invalidated_reason=?,updated_at=? WHERE id=?', (keep, 'previous_turn_incomplete', hub.now(), session['id']))


def reviewed(conn, task_id):
    if unresolved(conn, task_id):
        return
    task = hub._find(conn, 'hub_tasks', task_id, '任务')
    run = hub._find(conn, 'hub_runs', task['run_id'], '运行')
    keep = task_id if not run['cancelled'] and task['status'] in ('running', 'queued') else None
    conn.execute('UPDATE hub_agent_sessions SET current_task_id=?,head_json=NULL,invalidated_reason=?,updated_at=? WHERE current_task_id=?', (keep, 'previous_turn_incomplete', hub.now(), task_id))


def for_task(conn, task_id):
    latest = conn.execute('SELECT a.*,t.turn_index,s.room_id,s.agent_id FROM hub_agent_session_attempts a JOIN hub_agent_session_turns t ON t.task_id=a.task_id JOIN hub_agent_sessions s ON s.id=a.session_id WHERE a.task_id=? ORDER BY a.attempt DESC LIMIT 1', (task_id,)).fetchone()
    if latest is None:
        return None
    return {'id': latest['session_id'], 'room_id': latest['room_id'], 'agent_id': latest['agent_id'], 'generation': latest['generation'], 'turn_index': latest['turn_index'], 'context_fingerprint': latest['context_fingerprint'], 'continuation': json.loads(latest['continuation_json'])}


def delegation(conn, parent_task_id, child_task_id, run_id, stamp):
    parent = bind_task(conn, parent_task_id, stamp)
    child = bind_task(conn, child_task_id, stamp)
    conn.execute('INSERT INTO hub_agent_delegations VALUES (?,?,?,?,?,?,?)', ('dg_' + child_task_id, parent_task_id, child_task_id, parent['session_id'], child['session_id'], run_id, stamp))


def _summary(conn, row):
    blocked = _block(conn, row)
    active = blocked and blocked['reason'] == 'agent_session_busy'
    return {
        'id': row['id'], 'room_id': row['room_id'], 'agent_id': row['agent_id'],
        'agent_name': hub._find(conn, 'hub_agents', row['agent_id'], 'Agent')['name'],
        'generation': row['generation'], 'status': 'running' if active else 'blocked' if blocked else 'idle',
        'current_task_id': blocked['blocked_by_task_id'] if blocked else None,
        'blocked_by_task_id': blocked['blocked_by_task_id'] if blocked and not active else None,
        'blocked_reason': blocked['reason'] if blocked and not active else None,
        'blocked_message': blocked['message'] if blocked and not active else None,
        'last_completed_task_id': row['last_completed_task_id'],
        'native_head': json.loads(row['head_json']) if row['head_json'] else None,
        'turn_count': conn.execute('SELECT COUNT(*) FROM hub_agent_session_turns WHERE session_id=?', (row['id'],)).fetchone()[0],
        'created_at': row['created_at'], 'updated_at': row['updated_at'],
    }


def list_sessions(room_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_rooms', room_id, '会话')
        return {'room_id': room_id, 'sessions': [_summary(conn, row) for row in conn.execute('SELECT * FROM hub_agent_sessions WHERE room_id=? ORDER BY created_at,rowid', (room_id,))]}


def _attempt(row):
    if row is None:
        return None
    return {**{key: row[key] for key in ('attempt', 'generation', 'worker_id', 'host_identity', 'context_fingerprint', 'created_at')}, 'continuation': json.loads(row['continuation_json'])}


def get_session(session_id, after=0, limit=50):
    from . import hub_execution
    hub_execution._integer(after, '会话任务游标')
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise hub.HubError('分页大小须为 1–100')
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        session = hub._find(conn, 'hub_agent_sessions', session_id, 'Agent 会话')
        rows = conn.execute('SELECT a.turn_index,t.*,r.cancelled FROM hub_agent_session_turns a JOIN hub_tasks t ON t.id=a.task_id JOIN hub_runs r ON r.id=t.run_id WHERE a.session_id=? AND a.turn_index>? ORDER BY a.turn_index LIMIT ?', (session_id, after, limit + 1)).fetchall()
        turns, edges = [], {}
        for row in rows[:limit]:
            latest = conn.execute('SELECT * FROM hub_agent_session_attempts WHERE task_id=? ORDER BY attempt DESC LIMIT 1', (row['id'],)).fetchone()
            incident = conn.execute('SELECT * FROM hub_agent_delegations WHERE parent_task_id=? OR child_task_id=? ORDER BY created_at,rowid', (row['id'], row['id'])).fetchall()
            edges.update({edge['id']: dict(edge) for edge in incident})
            parent = next((edge['parent_task_id'] for edge in incident if edge['child_task_id'] == row['id']), None)
            turns.append({
                'turn_index': row['turn_index'], 'task_id': row['id'], 'execution_id': 'exec_' + row['id'],
                'run_id': row['run_id'], 'title': row['title'],
                'status': 'cancelled' if row['cancelled'] and row['status'] != 'completed' else hub._task(row)['status'],
                'attempts': row['attempts'], 'created_at': row['created_at'], 'updated_at': row['updated_at'],
                'latest_attempt': _attempt(latest), 'delegated_from_task_id': parent,
            })
        return {'session': _summary(conn, session), 'turns': turns, 'delegations': list(edges.values()), 'cursor': turns[-1]['turn_index'] if turns else after, 'has_more': len(rows) > limit}
