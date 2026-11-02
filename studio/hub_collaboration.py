"""Version-anchored discussions and atomic, model-independent artifact revisions."""
from __future__ import annotations

import contextlib
import hashlib
import json
from uuid import uuid4

from . import hub

MAX_BASELINE = 128 * 1024
MAX_DISCUSSION = 64 * 1024
MAX_ESSENTIAL = 192 * 1024


def ensure_schema(conn):
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS hub_comments (
            id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL REFERENCES hub_artifacts(id),
            artifact_version INTEGER NOT NULL,parent_id TEXT REFERENCES hub_comments(id),
            body TEXT NOT NULL,anchor_json TEXT NOT NULL,status TEXT NOT NULL,
            version INTEGER NOT NULL,author_id TEXT,author_name TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
            FOREIGN KEY(artifact_id,artifact_version) REFERENCES hub_artifact_versions(artifact_id,version)
        );
        CREATE INDEX IF NOT EXISTS hub_comments_artifact ON hub_comments(artifact_id,artifact_version);
        CREATE TABLE IF NOT EXISTS hub_comment_versions (
            comment_id TEXT NOT NULL REFERENCES hub_comments(id),version INTEGER NOT NULL,
            snapshot_json TEXT NOT NULL,PRIMARY KEY(comment_id,version)
        );
        CREATE TABLE IF NOT EXISTS hub_modifications (
            id TEXT PRIMARY KEY,artifact_id TEXT NOT NULL REFERENCES hub_artifacts(id),
            room_id TEXT NOT NULL REFERENCES hub_rooms(id),base_version INTEGER NOT NULL,
            source_task_id TEXT NOT NULL,source_agent_id TEXT NOT NULL,agent_id TEXT NOT NULL,
            run_id TEXT NOT NULL REFERENCES hub_runs(id),task_id TEXT NOT NULL UNIQUE REFERENCES hub_tasks(id),
            snapshot_json TEXT NOT NULL,created_by TEXT,created_by_name TEXT NOT NULL,
            created_at TEXT NOT NULL,updated_at TEXT NOT NULL,candidate_json TEXT,result_version INTEGER,
            applied_by TEXT,applied_by_name TEXT,applied_at TEXT,
            FOREIGN KEY(artifact_id,base_version) REFERENCES hub_artifact_versions(artifact_id,version)
        );
        CREATE INDEX IF NOT EXISTS hub_modifications_artifact ON hub_modifications(artifact_id,created_at);
        CREATE TABLE IF NOT EXISTS hub_collaboration_keys (
            kind TEXT NOT NULL,owner TEXT NOT NULL,request_key TEXT NOT NULL,
            request_hash TEXT NOT NULL,entity_id TEXT NOT NULL,
            PRIMARY KEY(kind,owner,request_key)
        );
    ''')


def _actor():
    actor = hub.REQUEST_ACTOR.get() or {}
    return actor.get('user_id'), actor.get('user_name') or '本地用户'


def _write_allowed():
    actor = hub.REQUEST_ACTOR.get() or {}
    if actor.get('role') == 'viewer':
        raise hub.HubError('当前成员仅可查看', 403)


def _positive(value, label):
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise hub.HubError(f'{label}须为正整数')
    return value


def _identifier(value, label):
    if not isinstance(value, str) or not value or len(value) > 128:
        raise hub.HubError(f'{label}不存在', 404)
    return value


def _bounded(value, label, chars, byte_limit, empty=False):
    value = hub._text(value, label, chars, empty=empty)
    if len(value.encode('utf-8')) > byte_limit:
        raise hub.HubError(f'{label}超过大小限制', 413)
    return value


def _key_input(request_key, payload):
    key = hub._text(request_key, '请求去重键', 120)
    return key, hashlib.sha256(hub._json(payload).encode('utf-8')).hexdigest()


def _existing_key(conn, kind, key, digest):
    owner = _actor()[0] or 'local'
    row = conn.execute('SELECT * FROM hub_collaboration_keys WHERE kind=? AND owner=? AND request_key=?', (kind, owner, key)).fetchone()
    if row and row['request_hash'] != digest:
        raise hub.HubError('同一请求键已用于不同内容，请为新操作生成新键', 409)
    return row['entity_id'] if row else None


def _save_key(conn, kind, key, digest, entity_id):
    conn.execute('INSERT INTO hub_collaboration_keys VALUES (?,?,?,?,?)', (kind, _actor()[0] or 'local', key, digest, entity_id))


def _saved_artifact(conn, artifact_id, version):
    current = hub._find(conn, 'hub_artifacts', artifact_id, '成果')
    _positive(version, '成果版本')
    saved = conn.execute('SELECT * FROM hub_artifact_versions WHERE artifact_id=? AND version=?', (artifact_id, version)).fetchone()
    if saved is None:
        raise hub.HubError('成果版本不存在', 404)
    return current, saved


def _comment(conn, comment_id, history=False):
    row = hub._find(conn, 'hub_comments', comment_id, '评论')
    result = {key: row[key] for key in row.keys() if key != 'anchor_json'}
    result['anchor'] = json.loads(row['anchor_json'])
    if history:
        result['history'] = [json.loads(item['snapshot_json']) for item in conn.execute('SELECT snapshot_json FROM hub_comment_versions WHERE comment_id=? ORDER BY version', (comment_id,))]
    return result


def _record_comment(conn, comment_id):
    saved = _comment(conn, comment_id)
    conn.execute('INSERT INTO hub_comment_versions VALUES (?,?,?)', (comment_id, saved['version'], hub._json(saved)))
    return saved


def _revision(items):
    return hashlib.sha256(hub._json(items).encode('utf-8')).hexdigest()


def list_comments(artifact_id, version=None):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_artifacts', artifact_id, '成果')
        if version is not None:
            _saved_artifact(conn, artifact_id, version)
        rows = conn.execute('SELECT id FROM hub_comments WHERE artifact_id=? AND (? IS NULL OR artifact_version=?) ORDER BY rowid', (artifact_id, version, version))
        comments = [_comment(conn, row['id']) for row in rows]
        return {'comments': comments, 'revision': _revision(comments)}


def get_comment(comment_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return _comment(conn, comment_id, history=True)


def create_comment(artifact_id, artifact_version, body, request_key, parent_id=None, anchor=None):
    _write_allowed()
    _identifier(artifact_id, '成果')
    _positive(artifact_version, '成果版本')
    body = _bounded(body, '评论', 4000, 16 * 1024)
    if parent_id is not None:
        parent_id = hub._text(parent_id, '父评论', 128)
    if anchor is not None:
        if not isinstance(anchor, dict) or set(anchor) != {'quote'}:
            raise hub.HubError('引用定位须为仅含 quote 的对象')
        quote = _bounded(anchor['quote'], '引用文本', 2000, 8 * 1024, empty=True)
        if not quote.strip():
            raise hub.HubError('引用文本不能为空')
        anchor = {'quote': quote}
    key, digest = _key_input(request_key, {'artifact_id': artifact_id, 'artifact_version': artifact_version, 'body': body, 'parent_id': parent_id, 'anchor': anchor})
    with hub._transaction() as conn:
        existing = _existing_key(conn, 'comment', key, digest)
        if existing:
            return _comment(conn, existing)
        current, saved = _saved_artifact(conn, artifact_id, artifact_version)
        if conn.execute('SELECT count(*) FROM hub_comments WHERE artifact_id=?', (artifact_id,)).fetchone()[0] >= 500:
            raise hub.HubError('单个成果最多保留 500 条评论和回复', 413)
        if parent_id is not None:
            parent = _comment(conn, parent_id)
            if parent['parent_id'] is not None or parent['artifact_id'] != artifact_id or parent['artifact_version'] != artifact_version:
                raise hub.HubError('回复必须属于同一成果版本的根讨论', 409)
            if anchor is not None:
                raise hub.HubError('回复继承根讨论定位，不能另设引用')
            located = parent['anchor']
        elif anchor:
            quote = anchor['quote']
            # Count overlapping occurrences as ambiguous too.
            matches = sum(1 for start in range(len(saved['content'])) if saved['content'].startswith(quote, start))
            located = {'type': 'quote', 'quote': quote, 'status': 'matched' if matches == 1 else ('ambiguous' if matches else 'missing'), 'matches': matches}
        else:
            located = {'type': 'document', 'status': 'document', 'matches': 0}
        comment_id, stamp = uuid4().hex, hub.now()
        author_id, author_name = _actor()
        conn.execute('INSERT INTO hub_comments VALUES (?,?,?,?,?,?,?,?,?,?,?,?)', (comment_id, artifact_id, artifact_version, parent_id, body, hub._json(located), 'open', 1, author_id, author_name, stamp, stamp))
        result = _record_comment(conn, comment_id)
        _save_key(conn, 'comment', key, digest, comment_id)
        hub._event(conn, current['room_id'], None, None, 'comment.created', '已添加成果评论')
        return result


def update_comment(comment_id, base_version, body=None, status=None):
    _write_allowed()
    _positive(base_version, '评论版本')
    if body is None and status is None:
        raise hub.HubError('请提供评论正文或讨论状态')
    if body is not None:
        body = _bounded(body, '评论', 4000, 16 * 1024)
    if status is not None and status not in ('open', 'resolved'):
        raise hub.HubError('讨论状态须为 open 或 resolved')
    with hub._transaction() as conn:
        old = _comment(conn, comment_id)
        if old['version'] != base_version:
            raise hub.HubError('评论已更新，请刷新后再编辑', 409)
        if old['parent_id'] is not None and status is not None:
            raise hub.HubError('请在根讨论上解决或重新打开')
        conn.execute('UPDATE hub_comments SET body=?,status=?,version=version+1,updated_at=? WHERE id=?', (body if body is not None else old['body'], status or old['status'], hub.now(), comment_id))
        result = _record_comment(conn, comment_id)
        artifact = hub._find(conn, 'hub_artifacts', old['artifact_id'], '成果')
        hub._event(conn, artifact['room_id'], None, None, 'comment.updated', '已更新成果讨论')
        return result


def _modification(conn, request_id, full=True):
    row = hub._find(conn, 'hub_modifications', request_id, '修改请求')
    result = {key: row[key] for key in row.keys() if key not in ('snapshot_json', 'candidate_json')}
    task = hub._task(hub._find(conn, 'hub_tasks', row['task_id'], '任务'))
    run = hub._find(conn, 'hub_runs', row['run_id'], '运行')
    result['task_status'] = 'cancelled' if run['cancelled'] else task['status']
    result['status'] = 'conflict' if task['status'] == 'completed' and row['candidate_json'] and row['result_version'] is None else result['task_status']
    result['error'] = task['error']
    result['updated_at'] = max(row['updated_at'], task['updated_at'])
    if full:
        result['snapshot'] = json.loads(row['snapshot_json'])
    result['candidate'] = json.loads(row['candidate_json']) if row['candidate_json'] else None
    if result['candidate'] and not full:
        result['candidate'].pop('content', None)
    return result


def list_modifications(artifact_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        hub._find(conn, 'hub_artifacts', artifact_id, '成果')
        items = [_modification(conn, row['id'], full=False) for row in conn.execute('SELECT id FROM hub_modifications WHERE artifact_id=? ORDER BY rowid DESC', (artifact_id,))]
        return {'modifications': items, 'revision': _revision(items)}


def get_modification(request_id):
    with contextlib.closing(hub._db()) as conn:
        conn.execute('BEGIN')
        return _modification(conn, request_id)


def create_modification(artifact_id, base_version, comment_ids, request_key, instructions='', agent_id=None):
    _write_allowed()
    _identifier(artifact_id, '成果')
    _positive(base_version, '成果版本')
    instructions = _bounded(instructions, '补充说明', 8000, 32 * 1024, empty=True)
    if not isinstance(comment_ids, list) or not 1 <= len(comment_ids) <= 20 or any(not isinstance(item, str) or not item or len(item) > 128 for item in comment_ids) or len(set(comment_ids)) != len(comment_ids):
        raise hub.HubError('须选择 1–20 个不同的根讨论')
    if agent_id is not None:
        agent_id = hub._text(agent_id, '执行 Agent', 128)
    key, digest = _key_input(request_key, {'artifact_id': artifact_id, 'base_version': base_version, 'comment_ids': sorted(comment_ids), 'instructions': instructions, 'agent_id': agent_id})
    with hub._transaction() as conn:
        existing = _existing_key(conn, 'modification', key, digest)
        if existing:
            return _modification(conn, existing)
        current, saved = _saved_artifact(conn, artifact_id, base_version)
        if len(saved['content'].encode('utf-8')) > MAX_BASELINE:
            raise hub.HubError('修改基线超过 128 KiB，请拆分成果后再提交；未创建任务', 413)
        comments = []
        for comment_id in comment_ids:
            root = _comment(conn, comment_id)
            if root['parent_id'] is not None or root['artifact_id'] != artifact_id or root['artifact_version'] != base_version:
                raise hub.HubError('请选择目标成果同一基线版本的根讨论', 409)
            comments.append(root)
            comments.extend(_comment(conn, row['id']) for row in conn.execute('SELECT id FROM hub_comments WHERE parent_id=? ORDER BY rowid', (comment_id,)))
        if len(comments) > 100 or len(hub._json(comments).encode('utf-8')) > MAX_DISCUSSION:
            raise hub.HubError('所选讨论及回复超过 100 条或 64 KiB，请缩小选择；未创建任务', 413)
        source_task = hub._find(conn, 'hub_tasks', saved['task_id'], '来源任务')
        source_agent = hub._agent(hub._find(conn, 'hub_agents', source_task['agent_id'], '来源 Agent'))
        agent = hub._agent(hub._find(conn, 'hub_agents', agent_id or source_agent['id'], '执行 Agent'))
        room = hub._find(conn, 'hub_rooms', current['room_id'], '会话')
        if room['team_id'] and agent['id'] == 'coordinator' and source_agent['id'] != 'coordinator':
            raise hub.HubError('团队修改只能选择当前成员；内部统筹只可修订其自身原稿', 409)
        request_id, stamp = uuid4().hex, hub.now()
        actor_id, actor_name = _actor()
        snapshot = {'request_id': request_id, 'artifact_id': artifact_id, 'base_version': base_version,
                    'baseline': {'title': saved['title'], 'kind': saved['kind'], 'content': saved['content'], 'sha256': hashlib.sha256(saved['content'].encode('utf-8')).hexdigest()},
                    'source': {'task_id': saved['task_id'], 'agent_id': source_agent['id'], 'agent_name': source_agent['name'], 'author_type': saved['author_type'], 'author_id': saved['author_id'], 'author_name': saved['author_name']},
                    'agent': agent, 'instructions': instructions, 'comments': comments, 'created_by': actor_id, 'created_by_name': actor_name}
        if len(hub._json({'modification': snapshot, 'agent': agent}).encode('utf-8')) > MAX_ESSENTIAL:
            raise hub.HubError('修改快照及执行指引超过 192 KiB，请缩小输入；未创建任务', 413)
        text = f'请根据选中讨论修改《{saved["title"]}》（v{base_version}）。' + (f'\n{instructions}' if instructions else '')
        run_id = hub._new_run(conn, current['room_id'], text, agent['id'])
        task_id = conn.execute('SELECT id FROM hub_tasks WHERE run_id=?', (run_id,)).fetchone()['id']
        directive = f'Apply only the frozen modification in context.modification. Preserve unrelated content. Return exactly one revised artifact with id={artifact_id} and base_version={base_version}; no new artifacts or plan. Use the complete baseline and frozen discussion, including any missing/ambiguous quote warnings. Report only work actually performed.'
        conn.execute('UPDATE hub_tasks SET title=?,instructions=?,modification_request_id=? WHERE id=?', (f'修改《{saved["title"]}》'[:120], directive, request_id, task_id))
        conn.execute('INSERT INTO hub_modifications (id,artifact_id,room_id,base_version,source_task_id,source_agent_id,agent_id,run_id,task_id,snapshot_json,created_by,created_by_name,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (request_id, artifact_id, current['room_id'], base_version, saved['task_id'], source_agent['id'], agent['id'], run_id, task_id, hub._json(snapshot), actor_id, actor_name, stamp, stamp))
        _save_key(conn, 'modification', key, digest, request_id)
        hub._event(conn, current['room_id'], run_id, task_id, 'modification.created', f'已提交 v{base_version} 的修改请求')
        return _modification(conn, request_id)


def context_for_task(conn, task):
    if not task['modification_request_id']:
        return None
    row = hub._find(conn, 'hub_modifications', task['modification_request_id'], '修改请求')
    if row['task_id'] != task['id']:
        raise hub.HubError('修改任务关联不一致', 409)
    return json.loads(row['snapshot_json'])


def _save_version(conn, request, candidate, version, task_id):
    stamp = hub.now()
    conn.execute('INSERT INTO hub_artifact_versions (artifact_id,version,task_id,title,kind,content,created_at,author_type,modification_request_id,source_version) VALUES (?,?,?,?,?,?,?,?,?,?)', (request['artifact_id'], version, task_id, candidate['title'], candidate['kind'], candidate['content'], stamp, 'agent', request['id'], request['base_version']))
    conn.execute('UPDATE hub_artifacts SET task_id=?,title=?,kind=?,version=?,updated_at=? WHERE id=?', (task_id, candidate['title'], candidate['kind'], version, stamp, request['artifact_id']))
    conn.execute('UPDATE hub_modifications SET result_version=?,updated_at=? WHERE id=?', (version, stamp, request['id']))
    hub._event(conn, request['room_id'], request['run_id'], task_id, 'artifact.saved', f'{candidate["title"]} · v{version}')


def complete_modification(conn, task, run, result):
    """Called inside complete_task's ownership-checked transaction; no writes elsewhere."""
    request = hub._find(conn, 'hub_modifications', task['modification_request_id'], '修改请求')
    if request['task_id'] != task['id'] or request['run_id'] != run['id']:
        raise hub.HubError('修改任务关联不一致', 409)
    if result.get('modification_request_id', request['id']) != request['id']:
        raise hub.HubError('结果关联了其他修改请求', 409)
    if result.get('plan') not in (None, []):
        raise hub.HubError('修改请求不能另建任务计划')
    items = result.get('artifacts')
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
        raise hub.HubError('修改请求必须回传且仅回传目标成果')
    item = items[0]
    if item.get('id') != request['artifact_id']:
        raise hub.HubError('修改请求不能写入其他成果或创建新成果', 409)
    if isinstance(item.get('base_version'), bool) or not isinstance(item.get('base_version'), int) or item.get('base_version') != request['base_version']:
        raise hub.HubError('修改结果必须基于请求的原始版本', 409)
    # Validate content through the existing artifact schema without adopting the latest version.
    validated = hub._artifacts(conn, run, {'artifacts': [{key: value for key, value in item.items() if key not in ('id', 'base_version')}]})[0]
    candidate = {key: validated[key] for key in ('title', 'kind', 'content')}
    candidate.update(sha256=hashlib.sha256(candidate['content'].encode('utf-8')).hexdigest(), created_at=hub.now())
    current = hub._find(conn, 'hub_artifacts', request['artifact_id'], '成果')
    if current['version'] == request['base_version']:
        _save_version(conn, request, candidate, current['version'] + 1, task['id'])
    else:
        conn.execute('UPDATE hub_modifications SET candidate_json=?,updated_at=? WHERE id=?', (hub._json(candidate), hub.now(), request['id']))
        hub._event(conn, run['room_id'], run['id'], task['id'], 'modification.conflict', '已有更新版本；修改结果已保留为候选稿')


def apply_candidate(request_id, base_version, request_key):
    _write_allowed()
    _identifier(request_id, '修改请求')
    _positive(base_version, '当前成果版本')
    key, digest = _key_input(request_key, {'request_id': request_id, 'base_version': base_version})
    with hub._transaction() as conn:
        existing = _existing_key(conn, 'apply', key, digest)
        if existing:
            return _modification(conn, existing)
        request = hub._find(conn, 'hub_modifications', request_id, '修改请求')
        state = _modification(conn, request_id, full=False)
        if state['status'] != 'conflict':
            raise hub.HubError('仅可应用尚未采用的冲突候选稿', 409)
        current = hub._find(conn, 'hub_artifacts', request['artifact_id'], '成果')
        if current['version'] != base_version:
            raise hub.HubError('成果已有新版本，请重新比较后确认应用', 409)
        candidate = json.loads(request['candidate_json'])
        _save_version(conn, request, candidate, base_version + 1, request['task_id'])
        actor_id, actor_name = _actor()
        conn.execute('UPDATE hub_modifications SET applied_by=?,applied_by_name=?,applied_at=? WHERE id=?', (actor_id, actor_name, hub.now(), request_id))
        _save_key(conn, 'apply', key, digest, request_id)
        hub._event(conn, request['room_id'], request['run_id'], request['task_id'], 'modification.applied', '用户已确认应用修改候选稿')
        return _modification(conn, request_id)
