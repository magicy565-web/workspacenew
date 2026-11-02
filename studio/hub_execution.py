"""Durable, lease-fenced public execution receipts; no model-private reasoning."""
from __future__ import annotations

import contextlib
import hashlib
import json
import re

from . import core, hub

MAX_EVENT_BYTES = 16 * 1024
MAX_WORKER_EVENTS = 1024
MAX_EVENTS = 2048
MAX_CALLS = 64
MAX_ATTEMPTS = 100
KINDS = {'progress', 'host_session', 'checkpoint', 'tool_call', 'tool_result'}


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS hub_execution_sessions (
            id TEXT PRIMARY KEY,task_id TEXT NOT NULL UNIQUE REFERENCES hub_tasks(id),created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hub_execution_attempts (
            session_id TEXT NOT NULL REFERENCES hub_execution_sessions(id),attempt INTEGER NOT NULL,
            worker_id TEXT NOT NULL,host_identity TEXT,capabilities_json TEXT NOT NULL,
            decision TEXT NOT NULL,reason TEXT NOT NULL,started_at TEXT NOT NULL,
            PRIMARY KEY(session_id,attempt)
        );
        CREATE TABLE IF NOT EXISTS hub_execution_events (
            session_id TEXT NOT NULL REFERENCES hub_execution_sessions(id),seq INTEGER NOT NULL,
            event_id TEXT NOT NULL,kind TEXT NOT NULL,payload_json TEXT NOT NULL,fingerprint TEXT NOT NULL,
            attempt INTEGER NOT NULL,worker_id TEXT,author_id TEXT,author_name TEXT,
            created_at TEXT NOT NULL,worker_event INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY(session_id,seq),UNIQUE(session_id,event_id)
        );
        CREATE TABLE IF NOT EXISTS hub_execution_calls (
            session_id TEXT NOT NULL REFERENCES hub_execution_sessions(id),call_id TEXT NOT NULL,
            tool TEXT NOT NULL,retry_safe INTEGER NOT NULL,call_seq INTEGER NOT NULL,
            state TEXT NOT NULL,result_seq INTEGER,review_seq INTEGER,
            PRIMARY KEY(session_id,call_id)
        );
        CREATE TABLE IF NOT EXISTS hub_execution_results (
            session_id TEXT PRIMARY KEY REFERENCES hub_execution_sessions(id),result_json TEXT NOT NULL,
            sha256 TEXT NOT NULL,bytes INTEGER NOT NULL,event_seq INTEGER NOT NULL,created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hub_execution_reviews (
            session_id TEXT NOT NULL REFERENCES hub_execution_sessions(id),owner TEXT NOT NULL,
            request_key TEXT NOT NULL,fingerprint TEXT NOT NULL,event_seq INTEGER NOT NULL,
            PRIMARY KEY(session_id,owner,request_key)
        );
    ''')


def backfill(conn):
    conn.execute("INSERT OR IGNORE INTO hub_execution_sessions(id,task_id,created_at) SELECT 'exec_' || id,id,created_at FROM hub_tasks")


def session_id(task_id):
    return 'exec_' + task_id


def create_session(conn, task_id, stamp):
    conn.execute('INSERT INTO hub_execution_sessions VALUES (?,?,?)', (session_id(task_id), task_id, stamp))


def _actor(write=False):
    actor = core.REQUEST_ACTOR.get() or {}
    if actor.get('role') == 'viewer':
        raise hub.HubError('当前成员仅可查看', 403)
    if actor.get('user_id') and actor.get('workspace_id'):
        # Server middleware also refreshes session/token validity on every HTTP request.
        from . import accounts
        with contextlib.closing(accounts.db()) as conn:
            member = conn.execute('SELECT role FROM members WHERE user_id=? AND workspace_id=?', (actor['user_id'], actor['workspace_id'])).fetchone()
        if member is None or member['role'] not in ('owner', 'editor'):
            raise hub.HubError('当前成员已无执行或写入权限', 403)
    scopes = actor.get('scopes')
    if scopes is not None and ('write' if write else 'execute') not in scopes:
        raise hub.HubError('此凭据缺少所需操作权限', 403)
    return actor


def _integer(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= 9223372036854775807:
        raise hub.HubError(f'{label}须为 {minimum}–9223372036854775807 范围内的整数')
    return value


def _identifier(value, label, maximum=120):
    value = hub._text(value, label, maximum)
    if not re.fullmatch(r'[A-Za-z0-9_.:/-]+', value):
        raise hub.HubError(f'{label}须为不含空格的 ASCII 标识')
    return value


def _opaque(value, label):
    value = hub._text(value, label, 200)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise hub.HubError(f'{label}包含控制字符')
    return value


def normalize_capabilities(capabilities):
    if capabilities is None:
        return []
    if not isinstance(capabilities, list) or len(capabilities) > 32:
        raise hub.HubError('capabilities 最多包含 32 项')
    return list(dict.fromkeys(hub._text(value, '执行器能力', 120) for value in capabilities))


def normalize_host_identity(identity):
    return _opaque(identity, '宿主标识') if identity is not None else None


def _event(row):
    result = {key: row[key] for key in ('seq', 'event_id', 'kind', 'attempt', 'worker_id', 'author_id', 'author_name', 'created_at')}
    result['payload'] = json.loads(row['payload_json'])
    return result


def _last_seq(conn, sid):
    return conn.execute('SELECT COALESCE(MAX(seq),0) FROM hub_execution_events WHERE session_id=?', (sid,)).fetchone()[0]


def _append(conn, task, event_id, kind, payload, worker=False, expected_seq=None):
    sid = session_id(task['id'])
    fingerprint = hashlib.sha256(hub._json({'kind': kind, 'payload': payload}).encode('utf-8')).hexdigest()
    old = conn.execute('SELECT * FROM hub_execution_events WHERE session_id=? AND event_id=?', (sid, event_id)).fetchone()
    if old:
        if old['fingerprint'] != fingerprint:
            raise hub.HubError('同一事件 ID 已用于不同内容', 409)
        return _event(old), False
    seq = _last_seq(conn, sid)
    if expected_seq is not None and expected_seq != seq:
        raise hub.HubError('执行记录已更新，请读取最新序号后重试', 409)
    if seq >= MAX_EVENTS or (worker and conn.execute('SELECT count(*) FROM hub_execution_events WHERE session_id=? AND worker_event=1', (sid,)).fetchone()[0] >= MAX_WORKER_EVENTS):
        raise hub.HubError('执行事件数量达到上限', 413)
    actor = core.REQUEST_ACTOR.get() or {}
    conn.execute('INSERT INTO hub_execution_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (sid, seq + 1, event_id, kind, hub._json(payload), fingerprint, task['attempts'], task['worker_id'], actor.get('user_id'), actor.get('user_name'), hub.now(), int(worker)))
    return _event(conn.execute('SELECT * FROM hub_execution_events WHERE session_id=? AND seq=?', (sid, seq + 1)).fetchone()), True


def _calls(conn, sid):
    result = []
    for row in conn.execute('SELECT call_id,tool,state,retry_safe,call_seq,result_seq,review_seq FROM hub_execution_calls WHERE session_id=? ORDER BY call_seq', (sid,)):
        call = dict(row)
        call['retry_safe'] = bool(call['retry_safe'])
        result.append(call)
    return result


def _active(task, run):
    return not run['cancelled'] and task['status'] == 'running' and bool(task['lease_expires_at']) and task['lease_expires_at'] > hub.now()


def _unsafe(calls, active=False):
    return [call for call in calls if not call['retry_safe'] and (call['state'] == 'unknown' or (call['state'] == 'pending' and not active))]


def _checkpoint(conn, sid, calls):
    row = conn.execute("SELECT * FROM hub_execution_events WHERE session_id=? AND kind='checkpoint' ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
    if not row:
        return None
    point = json.loads(row['payload_json'])
    host = conn.execute("SELECT * FROM hub_execution_events WHERE session_id=? AND kind='host_session' ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
    if not host:
        return None
    source = json.loads(host['payload_json'])
    if any(source[key] != point[key] for key in ('host', 'session_id', 'host_identity')):
        return None
    through = max([host['seq']] + [max(call['call_seq'], call['result_seq'] or 0, call['review_seq'] or 0) for call in calls])
    if point['through_seq'] < through:
        return None
    return dict(point, event_seq=row['seq'], worker_id=row['worker_id'])


def recovery(conn, task, run, worker_id=None, capabilities=None, host_identity=None, claiming=False):
    sid = session_id(task['id'])
    calls = _calls(conn, sid)
    last_attempt = conn.execute('SELECT * FROM hub_execution_attempts WHERE session_id=? ORDER BY attempt DESC LIMIT 1', (sid,)).fetchone()
    if worker_id is None and last_attempt:
        worker_id, capabilities, host_identity = last_attempt['worker_id'], json.loads(last_attempt['capabilities_json']), last_attempt['host_identity']
    capabilities = capabilities or []
    staged = conn.execute('SELECT sha256 FROM hub_execution_results WHERE session_id=?', (sid,)).fetchone()
    point = _checkpoint(conn, sid, calls)
    unsafe = _unsafe(calls, active=_active(task, run) and not claiming)
    if task['status'] == 'completed':
        decision, reason = 'completed', 'completion_committed'
    elif run['cancelled'] or task['status'] == 'cancelled':
        decision, reason = 'cancelled', 'run_cancelled'
    elif unsafe:
        decision, reason = 'review_required', 'external_outcome_unknown'
    elif staged:
        decision, reason = 'submit_result', 'final_result_durably_staged'
    elif _active(task, run) and not claiming and last_attempt:
        decision, reason = last_attempt['decision'], last_attempt['reason']
    elif task['attempts'] == 0:
        decision, reason = 'start', 'first_attempt'
    elif point and point['worker_id'] == worker_id and host_identity is not None and point['host_identity'] == host_identity and 'continuation:' + point['host'] in capabilities:
        decision, reason = 'continue', 'matching_host_checkpoint'
    else:
        decision, reason = 'restart', 'checkpoint_unavailable_or_host_mismatch'
    return {'decision': decision, 'reason': reason, 'checkpoint': point, 'calls': calls,
            'result_available': staged is not None, 'result_sha256': staged['sha256'] if staged else None,
            'events_url': f'/api/hub/executions/{sid}/events', 'result_url': f'/api/hub/tasks/{task["id"]}/execution/result/read'}


def _detail(conn, sid):
    session = hub._find(conn, 'hub_execution_sessions', sid, '执行会话')
    task = hub._find(conn, 'hub_tasks', session['task_id'], '任务')
    run = hub._find(conn, 'hub_runs', task['run_id'], '运行')
    active = _active(task, run)
    status = 'completed' if task['status'] == 'completed' else ('cancelled' if run['cancelled'] else hub._task(task)['status'])
    rec = recovery(conn, task, run)
    actor = core.REQUEST_ACTOR.get() or {}
    editable = actor.get('role') != 'viewer' and (actor.get('scopes') is None or 'write' in actor['scopes'])
    newer = conn.execute('SELECT 1 FROM hub_runs r JOIN hub_tasks t ON t.run_id=r.id WHERE r.room_id=? AND r.created_at>? AND t.attempts>0 LIMIT 1', (run['room_id'], run['created_at'])).fetchone()
    attempts = [dict(row) for row in conn.execute('SELECT attempt,worker_id,host_identity,decision,reason,started_at FROM hub_execution_attempts WHERE session_id=? ORDER BY attempt', (sid,))]
    return {'id': sid, 'task_id': task['id'], 'run_id': run['id'], 'task_status': status, 'active': active,
            'attempts': attempts, 'last_seq': _last_seq(conn, sid), 'recovery': rec,
            'can_reconcile': editable and not active and rec['decision'] == 'review_required',
            'can_retry': editable and status == 'failed' and rec['decision'] != 'review_required' and not newer and task['attempts'] < MAX_ATTEMPTS and hub._run(conn, run)['status'] == 'failed',
            'can_cancel': editable and hub._run(conn, run)['status'] not in ('cancelled', 'completed')}


def get_execution(task_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_tasks', task_id, '任务')
        return _detail(conn, session_id(task_id))


def list_events(sid, after=0, limit=50):
    _integer(after, '事件游标')
    _integer(limit, '每页数量', 1)
    if limit > 100:
        raise hub.HubError('每页最多 100 条事件')
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_execution_sessions', sid, '执行会话')
        rows = conn.execute('SELECT * FROM hub_execution_events WHERE session_id=? AND seq>? ORDER BY seq LIMIT ?', (sid, after, limit + 1)).fetchall()
        events = [_event(row) for row in rows[:limit]]
        return {'events': events, 'cursor': events[-1]['seq'] if events else after, 'has_more': len(rows) > limit, 'last_seq': _last_seq(conn, sid)}


def _block(conn, task, reason, calls=None):
    payload = {'reason': reason, 'call_ids': [call['call_id'] for call in calls or []]}
    key = hashlib.sha256(hub._json(payload).encode()).hexdigest()[:32]
    _append(conn, task, 'system:review:' + key, 'recovery.required', payload)
    message = '执行次数达到上限，请新建任务' if reason == 'attempt_limit_reached' else '外部调用结果尚未确认，请先查看执行记录并核对'
    conn.execute('UPDATE hub_tasks SET status=?,worker_id=NULL,lease_token=NULL,lease_expires_at=NULL,progress=NULL,error=?,updated_at=? WHERE id=?', ('failed', message, hub.now(), task['id']))
    run = hub._find(conn, 'hub_runs', task['run_id'], '运行')
    hub._event(conn, run['room_id'], run['id'], task['id'], 'execution.review_required', '外部调用结果未确认，已暂停自动执行')


def prepare_claim(conn, task, run, worker_id, capabilities, host_identity):
    rec = recovery(conn, task, run, worker_id, capabilities, host_identity, claiming=True)
    if rec['decision'] == 'review_required':
        _block(conn, task, 'external_outcome_unknown', _unsafe(rec['calls']))
        return None
    if task['attempts'] >= MAX_ATTEMPTS:
        _block(conn, task, 'attempt_limit_reached')
        return None
    return rec


def record_claim(conn, task, capabilities, host_identity, rec):
    sid, stamp = session_id(task['id']), hub.now()
    conn.execute('INSERT INTO hub_execution_attempts VALUES (?,?,?,?,?,?,?,?)', (sid, task['attempts'], task['worker_id'], host_identity, hub._json(capabilities), rec['decision'], rec['reason'], stamp))
    event, _ = _append(conn, task, f'system:attempt:{task["attempts"]}', 'attempt.started', {'decision': rec['decision'], 'reason': rec['reason'], 'host_identity': host_identity})
    return {'id': sid, 'task_id': task['id'], 'run_id': task['run_id'], 'attempt': task['attempts'], 'last_seq': event['seq'], 'recovery': rec}


def _payload(kind, payload):
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(payload, dict):
        raise hub.HubError('不支持的公开执行事件类型或内容')
    schemas = {'progress': ({'message'}, set()), 'host_session': ({'host', 'session_id', 'host_identity'}, set()),
               'checkpoint': ({'host', 'session_id', 'host_identity', 'checkpoint_id', 'through_seq'}, set()),
               'tool_call': ({'call_id', 'tool', 'retry_safe'}, {'summary'}), 'tool_result': ({'call_id', 'outcome'}, {'summary'})}
    required, optional = schemas[kind]
    if not required.issubset(payload) or set(payload) - required - optional:
        raise hub.HubError('执行事件字段不完整或含不允许的字段')
    result = dict(payload)
    if kind == 'progress':
        result['message'] = hub._text(payload['message'], '公开进度', 2000)
    if 'host' in result:
        result['host'] = _identifier(result['host'], '宿主类型', 80)
        for key in ('session_id', 'host_identity'):
            result[key] = _opaque(result[key], key)
    if kind == 'checkpoint':
        result['checkpoint_id'] = _opaque(result['checkpoint_id'], 'checkpoint_id')
        _integer(result['through_seq'], '检查点覆盖序号')
    if kind in ('tool_call', 'tool_result'):
        result['call_id'] = _identifier(result['call_id'], '调用标识')
        if 'summary' in result:
            result['summary'] = hub._text(result['summary'], '公开摘要', 1000, empty=True)
    if kind == 'tool_call':
        result['tool'] = _identifier(result['tool'], '工具名称', 80)
        if not isinstance(result['retry_safe'], bool):
            raise hub.HubError('retry_safe 必须明确为 true 或 false')
    if kind == 'tool_result' and result['outcome'] not in ('succeeded', 'not_executed', 'unknown'):
        raise hub.HubError('工具结果须为 succeeded、not_executed 或 unknown')
    if len(hub._json(result).encode('utf-8')) > MAX_EVENT_BYTES:
        raise hub.HubError('执行事件超过 16 KiB', 413)
    return result


def append_event(task_id, worker_id, lease_token, event_id, kind, payload, expected_seq=None):
    _actor()
    event_id = _identifier(event_id, '事件标识')
    if event_id.startswith('system:'):
        raise hub.HubError('system: 前缀仅供服务端事件使用')
    payload = _payload(kind, payload)
    if expected_seq is not None:
        _integer(expected_seq, '预期事件序号')
    with hub._transaction() as conn:
        _actor()
        task, run = hub._owned(conn, task_id, worker_id, lease_token)
        sid = session_id(task_id)
        if lease_token in hub._json(payload):
            raise hub.HubError('公开事件不得包含任务凭据')
        old = conn.execute('SELECT 1 FROM hub_execution_events WHERE session_id=? AND event_id=?', (sid, event_id)).fetchone()
        if old:
            event, _ = _append(conn, task, event_id, kind, payload, worker=True, expected_seq=expected_seq)
            return {'event': event, 'execution': _detail(conn, sid)}
        attempt = conn.execute('SELECT * FROM hub_execution_attempts WHERE session_id=? AND attempt=?', (sid, task['attempts'])).fetchone()
        if 'host_identity' in payload and attempt and attempt['host_identity'] is not None and payload['host_identity'] != attempt['host_identity']:
            raise hub.HubError('宿主标识与本次领取声明不一致', 409)
        if kind == 'checkpoint':
            host = conn.execute("SELECT * FROM hub_execution_events WHERE session_id=? AND kind='host_session' ORDER BY seq DESC LIMIT 1", (sid,)).fetchone()
            if not host or any(json.loads(host['payload_json'])[key] != payload[key] for key in ('host', 'session_id', 'host_identity')) or host['worker_id'] != worker_id:
                raise hub.HubError('检查点必须匹配本执行器已记录的宿主会话', 409)
            if not host['seq'] <= payload['through_seq'] <= _last_seq(conn, sid):
                raise hub.HubError('检查点覆盖序号无效', 409)
        call = None
        if kind in ('tool_call', 'tool_result'):
            call = conn.execute('SELECT * FROM hub_execution_calls WHERE session_id=? AND call_id=?', (sid, payload['call_id'])).fetchone()
        if kind == 'tool_call':
            if call:
                raise hub.HubError('调用标识已存在；重试原事件应复用其 event_id', 409)
            if conn.execute('SELECT count(*) FROM hub_execution_calls WHERE session_id=?', (sid,)).fetchone()[0] >= MAX_CALLS:
                raise hub.HubError('单次执行会话最多记录 64 个工具调用', 413)
        if kind == 'tool_result' and (call is None or call['state'] in ('succeeded', 'not_executed')):
            raise hub.HubError('调用不存在或已记录不可覆盖的确定结果', 409)
        event, _ = _append(conn, task, event_id, kind, payload, worker=True, expected_seq=expected_seq)
        if kind == 'tool_call':
            conn.execute('INSERT INTO hub_execution_calls VALUES (?,?,?,?,?,?,NULL,NULL)', (sid, payload['call_id'], payload['tool'], int(payload['retry_safe']), event['seq'], 'pending'))
        elif kind == 'tool_result':
            conn.execute('UPDATE hub_execution_calls SET state=?,result_seq=? WHERE session_id=? AND call_id=?', (payload['outcome'], event['seq'], sid, payload['call_id']))
            if payload['outcome'] == 'unknown' and not call['retry_safe']:
                _block(conn, task, 'external_outcome_unknown', _unsafe(_calls(conn, sid)))
        return {'event': event, 'execution': _detail(conn, sid)}


def assert_can_complete(conn, task, digest):
    if _unsafe(_calls(conn, session_id(task['id']))):
        raise hub.HubError('外部调用结果未确认，请先人工核对，不能直接完成任务', 409)
    saved = conn.execute('SELECT sha256 FROM hub_execution_results WHERE session_id=?', (session_id(task['id']),)).fetchone()
    if saved and saved['sha256'] != digest:
        raise hub.HubError('完成结果与已冻结的最终检查点不同', 409)


def assert_can_retry(conn, run_id):
    for task in conn.execute('SELECT * FROM hub_tasks WHERE run_id=?', (run_id,)):
        if task['status'] == 'completed':
            continue
        if _unsafe(_calls(conn, session_id(task['id']))):
            raise hub.HubError('此运行存在未核对的外部调用，请先处理执行记录', 409)
        if task['attempts'] >= MAX_ATTEMPTS:
            raise hub.HubError('执行次数达到上限，请新建任务', 413)


def record_terminal(conn, task, kind, payload):
    _actor(write=kind == 'task.cancelled')
    _append(conn, task, f'system:{kind}:{task["attempts"]}', kind, payload)


def _validate_result(conn, task, run, result):
    if not isinstance(result, dict):
        raise hub.HubError('最终结果须为 JSON 对象')
    hub._text(result.get('message'), '结果消息')
    try:
        raw = hub._json(result)
        encoded = raw.encode('utf-8')
    except (TypeError, ValueError, UnicodeError) as exc:
        raise hub.HubError('最终结果不是有效 JSON') from exc
    if len(encoded) > 7 * hub.MAX_CONTENT:
        raise hub.HubError('最终结果超过 7 MiB', 413)
    if task['modification_request_id']:
        # Validate exactly the same constraints in a rollback-only savepoint.
        from . import hub_collaboration
        conn.execute('SAVEPOINT execution_validate_result')
        try:
            hub_collaboration.complete_modification(conn, task, run, result)
        finally:
            conn.execute('ROLLBACK TO execution_validate_result')
            conn.execute('RELEASE execution_validate_result')
    else:
        hub._plan(conn, task, result)
        hub._artifacts(conn, run, result)
    return raw, encoded, hashlib.sha256(encoded).hexdigest()


def stage_result(task_id, worker_id, lease_token, result):
    _actor()
    with hub._transaction() as conn:
        _actor()
        task, run = hub._owned(conn, task_id, worker_id, lease_token)
        # Identical staged retries remain idempotent even if an artifact subsequently changed.
        try:
            candidate_raw = hub._json(result)
            candidate_digest = hashlib.sha256(candidate_raw.encode('utf-8')).hexdigest()
        except (TypeError, ValueError, UnicodeError) as exc:
            raise hub.HubError('最终结果不是有效 JSON') from exc
        sid = session_id(task_id)
        saved = conn.execute('SELECT * FROM hub_execution_results WHERE session_id=?', (sid,)).fetchone()
        if saved:
            if saved['sha256'] != candidate_digest:
                raise hub.HubError('最终结果检查点已冻结，不能覆盖', 409)
            return {'sha256': saved['sha256'], 'bytes': saved['bytes'], 'event_seq': saved['event_seq'], 'execution': _detail(conn, sid)}
        raw, encoded, digest = _validate_result(conn, task, run, result)
        event, _ = _append(conn, task, 'system:result:ready', 'result.ready', {'sha256': digest, 'bytes': len(encoded)})
        conn.execute('INSERT INTO hub_execution_results VALUES (?,?,?,?,?,?)', (sid, raw, digest, len(encoded), event['seq'], hub.now()))
        return {'sha256': digest, 'bytes': len(encoded), 'event_seq': event['seq'], 'execution': _detail(conn, sid)}


def read_result(task_id, worker_id, lease_token):
    _actor()
    with hub._transaction() as conn:
        _actor()
        hub._owned(conn, task_id, worker_id, lease_token)
        saved = conn.execute('SELECT result_json,sha256 FROM hub_execution_results WHERE session_id=?', (session_id(task_id),)).fetchone()
        if not saved:
            raise hub.HubError('没有最终结果检查点', 404)
        return {'result': json.loads(saved['result_json']), 'sha256': saved['sha256']}


def reconcile(sid, request_key, expected_seq, decision, note, call_id=None):
    actor = _actor(write=True)
    request_key = hub._text(request_key, '核对请求键', 120)
    _integer(expected_seq, '预期事件序号')
    note = hub._text(note, '核对依据', 2000)
    if decision not in ('confirmed_succeeded', 'confirmed_not_executed', 'terminate'):
        raise hub.HubError('不支持的核对决定')
    if call_id is not None:
        call_id = _identifier(call_id, '调用标识')
    payload = {'decision': decision, 'note': note, 'call_id': call_id}
    fingerprint = hashlib.sha256(hub._json(dict(payload, expected_seq=expected_seq)).encode('utf-8')).hexdigest()
    owner = actor.get('user_id') or 'local'
    with hub._transaction() as conn:
        _actor(write=True)
        detail = _detail(conn, sid)
        old = conn.execute('SELECT * FROM hub_execution_reviews WHERE session_id=? AND owner=? AND request_key=?', (sid, owner, request_key)).fetchone()
        if old:
            if old['fingerprint'] != fingerprint:
                raise hub.HubError('同一核对请求键已用于不同内容', 409)
            return detail
        if detail['active'] or detail['task_status'] in ('completed', 'cancelled'):
            raise hub.HubError('请先停止或等待当前租约到期；已结束的任务不可核对', 409)
        if detail['last_seq'] != expected_seq:
            raise hub.HubError('执行记录已更新，请重新核对最新状态', 409)
        task = hub._find(conn, 'hub_tasks', detail['task_id'], '任务')
        if decision != 'terminate':
            selected = next((call for call in _unsafe(detail['recovery']['calls']) if call['call_id'] == call_id), None)
            if selected is None:
                raise hub.HubError('请选择结果未确认且不可自动重试的调用', 409)
        event_id = 'system:reviewed:' + hashlib.sha256((owner + '\0' + request_key).encode()).hexdigest()[:32]
        event, _ = _append(conn, task, event_id, 'recovery.reviewed', payload)
        if decision == 'terminate':
            hub._cancel_run(conn, detail['run_id'])
        else:
            state = 'succeeded' if decision == 'confirmed_succeeded' else 'not_executed'
            conn.execute('UPDATE hub_execution_calls SET state=?,review_seq=? WHERE session_id=? AND call_id=?', (state, event['seq'], sid, call_id))
        conn.execute('INSERT INTO hub_execution_reviews VALUES (?,?,?,?,?)', (sid, owner, request_key, fingerprint, event['seq']))
        return _detail(conn, sid)
