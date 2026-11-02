"""Small HTTP adapter; request authentication and origin checks remain in server.py."""
from __future__ import annotations

import re
from urllib.parse import quote

from . import files, hub, hub_agent_sessions, hub_collaboration, hub_execution, hub_extensions, hub_teams, search, sharing

JSON = 'application/json; charset=utf-8'


def _body(body):
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise hub.HubError('请求内容须为 JSON 对象')
    return body


def _integer(query, key, default=None):
    value = query.get(key, default)
    if isinstance(value, list):
        if len(value) != 1:
            raise hub.HubError(f'{key} 参数无效')
        value = value[0]
    if value is None:
        return default
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', str(value)):
        raise hub.HubError(f'{key} 参数无效')
    try:
        return int(value)
    except ValueError as exc:
        raise hub.HubError(f'{key} 参数无效') from exc


def dispatch(method, path, body=None, query=None):
    if not (path == '/api/hub' or path.startswith('/api/hub/')):
        return None
    try:
        body = _body(body)
        query = query or {}
        route = path.removeprefix('/api/hub').strip('/').split('/')
        if route == ['teams'] and method in ('GET', 'POST'):
            if method == 'GET':
                data = hub_teams.list_teams()
            else:
                if set(body) - {'name', 'description', 'member_ids', 'routing_mode', 'request_key'}:
                    raise hub.HubError('团队创建参数无效')
                data = hub_teams.create_team(body.get('name'), body.get('member_ids'), body.get('request_key'), body.get('description', ''), body.get('routing_mode', 'auto'))
                return 201, data, JSON, {}
        elif len(route) == 2 and route[0] == 'teams' and method in ('GET', 'PUT', 'DELETE'):
            if method == 'GET':
                data = hub_teams.get_team(route[1])
            elif method == 'DELETE':
                if set(body) - {'base_version'}:
                    raise hub.HubError('团队归档参数无效')
                data = hub_teams.archive_team(route[1], body.get('base_version'))
            else:
                data = hub_teams.update_team(route[1], body.get('base_version'), **{key: value for key, value in body.items() if key != 'base_version'})
        elif len(route) == 3 and route[0] == 'teams' and route[2] == 'history' and method == 'GET':
            data = hub_teams.list_history(route[1], _integer(query, 'after', 0), _integer(query, 'limit', 50))
        elif len(route) == 3 and route[0] == 'teams' and route[2] == 'rooms' and method == 'POST':
            if set(body) - {'text', 'agent_id', 'target_agent_ids', 'request_key', 'base_team_version'}:
                raise hub.HubError('团队会话参数无效')
            data = hub_teams.open_room(route[1], body.get('text'), body.get('agent_id', 'auto'), body.get('target_agent_ids'), body.get('request_key'), body.get('base_team_version'))
        elif method == 'GET' and route == ['search']:
            value = query.get('q', '')
            if isinstance(value, list):
                if len(value) != 1:
                    raise hub.HubError('搜索词无效')
                value = value[0]
            data = search.find(value)
        elif len(route) == 3 and route[0] == 'rooms' and route[2] == 'files' and method in ('GET', 'POST'):
            if method == 'GET':
                data = files.list_files(route[1])
            else:
                data = files.add_file(route[1], body.get('filename'), body.get('data_base64'))
                return 201, data, JSON, {}
        elif len(route) in (2, 3) and route[0] == 'files' and method in ('GET', 'DELETE'):
            if method == 'DELETE' and len(route) == 2:
                data = files.revoke_file(route[1])
            elif method == 'GET' and len(route) == 3 and route[2] == 'download':
                meta, content = files.get_file(route[1], with_bytes=True)
                return 200, content, meta['mime'], {'Content-Disposition': f'attachment; filename="attachment"; filename*=UTF-8\'\'{quote(meta["filename"], safe="")}', 'Content-Security-Policy': "sandbox; default-src 'none'"}
            elif method == 'GET' and len(route) == 2:
                data = files.get_file(route[1])
            else:
                return 404, {'error': '接口不存在'}, JSON, {}
        elif len(route) == 3 and route[0] == 'artifacts' and route[2] == 'shares' and method in ('GET', 'POST'):
            if method == 'GET':
                data = sharing.list_shares(route[1])
            else:
                data = sharing.create_share(route[1], body.get('version'), body.get('expires_days', 7))
                return 201, data, JSON, {}
        elif method == 'DELETE' and len(route) == 4 and route[0] == 'artifacts' and route[2] == 'shares':
            data = sharing.revoke_share(route[1], route[3])
        elif len(route) == 3 and route[0] == 'artifacts' and route[2] == 'comments' and method in ('GET', 'POST'):
            if method == 'GET':
                data = hub_collaboration.list_comments(route[1], _integer(query, 'version'))
            else:
                data = hub_collaboration.create_comment(route[1], body.get('artifact_version'), body.get('body'), body.get('request_key'), body.get('parent_id'), body.get('anchor'))
                return 201, data, JSON, {}
        elif len(route) == 2 and route[0] == 'comments' and method in ('GET', 'PUT'):
            if method == 'PUT' and set(body) - {'base_version', 'body', 'status'}:
                raise hub.HubError('只能修改评论正文或讨论状态；定位和成果版本不可变')
            data = hub_collaboration.get_comment(route[1]) if method == 'GET' else hub_collaboration.update_comment(route[1], body.get('base_version'), body.get('body'), body.get('status'))
        elif len(route) == 3 and route[0] == 'artifacts' and route[2] == 'modifications' and method in ('GET', 'POST'):
            if method == 'GET':
                data = hub_collaboration.list_modifications(route[1])
            else:
                data = hub_collaboration.create_modification(route[1], body.get('base_version'), body.get('comment_ids'), body.get('request_key'), body.get('instructions', ''), body.get('agent_id'))
                return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'modifications':
            data = hub_collaboration.get_modification(route[1])
        elif method == 'POST' and len(route) == 3 and route[0] == 'modifications' and route[2] == 'apply':
            data = hub_collaboration.apply_candidate(route[1], body.get('base_version'), body.get('request_key'))
        elif method == 'GET' and route == ['bootstrap']:
            data = hub.bootstrap()
        elif method == 'GET' and route == ['agents']:
            data = {'agents': hub.list_agents()}
        elif method == 'POST' and route == ['agents']:
            data = hub.create_agent(body.get('name'), body.get('description', ''), body.get('instructions'))
            return 201, data, JSON, {}
        elif method == 'PUT' and len(route) == 2 and route[0] == 'agents':
            data = hub.update_agent(route[1], body.get('name'), body.get('description', ''), body.get('instructions'))
        elif method == 'GET' and len(route) == 3 and route[0] == 'agents' and route[2] == 'skills':
            data = hub_extensions.get_agent_skills(route[1])
        elif method == 'PUT' and len(route) == 3 and route[0] == 'agents' and route[2] == 'skills':
            data = hub_extensions.set_agent_skills(route[1], body.get('base_version'), body.get('skill_ids'))
        elif method == 'GET' and route == ['skills']:
            data = {'skills': hub_extensions.list_skills()}
        elif method == 'POST' and route == ['skills']:
            data = hub_extensions.create_skill(body.get('name'), body.get('description', ''), body.get('content'), body.get('source_url'))
            return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'skills':
            data = hub_extensions.get_skill(route[1], _integer(query, 'version'))
        elif method == 'PUT' and len(route) == 2 and route[0] == 'skills':
            data = hub_extensions.update_skill(route[1], body.get('base_version'), body.get('name'), body.get('description', ''), body.get('content'), body.get('source_url'))
        elif method == 'DELETE' and len(route) == 2 and route[0] == 'skills':
            data = hub_extensions.archive_skill(route[1], body.get('base_version'))
        elif method == 'GET' and route == ['schedules']:
            data = {'schedules': hub_extensions.list_schedules()}
        elif method == 'POST' and route == ['schedules']:
            data = hub_extensions.create_schedule(body.get('name'), body.get('text'), body.get('interval_seconds'), body.get('agent_id', 'auto'), body.get('template_id'), body.get('next_run_at'), body.get('room_id'))
            return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'schedules':
            data = hub_extensions.get_schedule(route[1])
        elif method == 'PUT' and len(route) == 2 and route[0] == 'schedules':
            data = hub_extensions.update_schedule(route[1], body.get('base_version'), body.get('name'), body.get('text'), body.get('interval_seconds'), body.get('agent_id', 'auto'), body.get('template_id'), body.get('next_run_at'), body.get('room_id'))
        elif method == 'DELETE' and len(route) == 2 and route[0] == 'schedules':
            data = hub_extensions.archive_schedule(route[1])
        elif method == 'POST' and len(route) == 3 and route[0] == 'schedules' and route[2] in ('pause', 'run-now'):
            data = hub_extensions.pause_schedule(route[1], body.get('paused', True)) if route[2] == 'pause' else hub_extensions.run_schedule_now(route[1])
        elif method == 'GET' and route == ['templates']:
            data = {'templates': hub.list_templates()}
        elif method == 'POST' and route == ['templates']:
            data = hub.create_template(body.get('name'), body.get('description', ''), body.get('steps'))
            return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'templates':
            data = hub.get_template(route[1])
        elif method == 'PUT' and len(route) == 2 and route[0] == 'templates':
            data = hub.update_template(route[1], body.get('base_version'), body.get('name'), body.get('description', ''), body.get('steps'))
        elif method == 'GET' and route == ['workers']:
            data = {'workers': hub.list_workers()}
        elif method == 'POST' and route == ['workers', 'register']:
            data = hub.register_worker(body.get('worker_id'), body.get('agent_ids'), body.get('name'), body.get('capabilities'))
            return 201, data, JSON, {}
        elif method == 'POST' and len(route) == 3 and route[0] == 'workers' and route[2] in ('ping', 'disconnect'):
            data = hub.ping_worker(route[1], body.get('session_token')) if route[2] == 'ping' else hub.disconnect_worker(route[1], body.get('session_token'))
        elif method == 'POST' and route == ['rooms']:
            data = hub.create_room(body.get('text'), body.get('title'), body.get('agent_id', 'auto'), body.get('template_id'))
            return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'rooms':
            data = hub.get_room(route[1])
        elif method == 'POST' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'messages':
            data = hub.add_message(route[1], body.get('text'), body.get('agent_id', 'auto'), body.get('target_agent_ids'), body.get('request_key'), body.get('base_team_version'))
        elif method == 'PUT' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'context':
            data = hub.update_context(route[1], body.get('text'))
        elif method == 'GET' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'events':
            data = hub.get_events(route[1], _integer(query, 'after', 0))
        elif method == 'GET' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'sources':
            data = hub.list_sources(route[1])
        elif method == 'POST' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'sources':
            data = hub.add_source(route[1], body.get('title'), body.get('kind'), body.get('content'), body.get('source_url'))
            return 201, data, JSON, {}
        elif method == 'GET' and len(route) == 2 and route[0] == 'sources':
            data = hub.get_source(route[1])
        elif method == 'DELETE' and len(route) == 2 and route[0] == 'sources':
            data = hub.revoke_source(route[1])
        elif method == 'POST' and route == ['tasks', 'claim']:
            data = hub.claim_task(body.get('worker_id'), body.get('agent_ids'), body.get('room_id'), body.get('session_token'), body.get('capabilities'), body.get('host_identity'))
        elif method == 'GET' and len(route) == 3 and route[0] == 'rooms' and route[2] == 'agent-sessions':
            data = hub_agent_sessions.list_sessions(route[1])
        elif method == 'GET' and len(route) == 2 and route[0] == 'agent-sessions':
            data = hub_agent_sessions.get_session(route[1], _integer(query, 'after', 0), _integer(query, 'limit', 50))
        elif method == 'GET' and len(route) == 3 and route[0] == 'tasks' and route[2] == 'execution':
            data = hub_execution.get_execution(route[1])
        elif method == 'GET' and len(route) == 3 and route[0] == 'executions' and route[2] == 'events':
            data = hub_execution.list_events(route[1], _integer(query, 'after', 0), _integer(query, 'limit', 50))
        elif method == 'POST' and len(route) == 3 and route[0] == 'executions' and route[2] == 'reconcile':
            data = hub_execution.reconcile(route[1], body.get('request_key'), body.get('expected_seq'), body.get('decision'), body.get('note'), body.get('call_id'))
        elif method == 'POST' and len(route) in (4, 5) and route[0] == 'tasks' and route[2] == 'execution':
            args = (route[1], body.get('worker_id'), body.get('lease_token'))
            if route[3:] == ['events']:
                data = hub_execution.append_event(*args, body.get('event_id'), body.get('kind'), body.get('payload'), body.get('expected_seq'))
                return 201, data, JSON, {}
            elif route[3:] == ['result']:
                data = hub_execution.stage_result(*args, body.get('result'))
            elif route[3:] == ['result', 'read']:
                data = hub_execution.read_result(*args)
            else:
                return 404, {'error': '接口不存在'}, JSON, {}
        elif method == 'POST' and len(route) == 3 and route[0] == 'tasks' and route[2] in ('heartbeat', 'complete', 'fail'):
            args = (route[1], body.get('worker_id'), body.get('lease_token'))
            if route[2] == 'heartbeat':
                data = hub.heartbeat_task(*args, body.get('progress'))
            elif route[2] == 'complete':
                data = hub.complete_task(*args, body.get('result'))
            else:
                data = hub.fail_task(*args, body.get('error'))
        elif method == 'POST' and len(route) == 3 and route[0] == 'runs' and route[2] in ('cancel', 'retry'):
            data = hub.cancel_run(route[1]) if route[2] == 'cancel' else hub.retry_run(route[1])
        elif method == 'PUT' and len(route) == 2 and route[0] == 'artifacts':
            data = hub.edit_artifact(route[1], body.get('base_version'), body.get('title'), body.get('kind'), body.get('content'))
        elif method == 'POST' and len(route) == 3 and route[0] == 'artifacts' and route[2] == 'reviews':
            data = hub.review_artifact(route[1], body.get('version'), body.get('decision'), body.get('comment', ''))
        elif method == 'GET' and len(route) in (2, 3) and route[0] == 'artifacts' and (len(route) == 2 or route[2] in ('preview', 'download')):
            data = hub.get_artifact(route[1], _integer(query, 'version'))
            if len(route) == 3:
                mime = {'html': 'text/html', 'markdown': 'text/markdown', 'text': 'text/plain', 'json': 'application/json'}[data['kind']] + '; charset=utf-8'
                headers = {'Content-Security-Policy': "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'self'", 'X-Content-Type-Options': 'nosniff'}
                if route[2] == 'download':
                    extension = {'html': 'html', 'markdown': 'md', 'text': 'txt', 'json': 'json'}[data['kind']]
                    safe_title = re.sub(r'[\x00-\x1f\x7f<>:"/\\|?*]', '_', data['title']).strip(' .') or 'artifact'
                    filename = f'{safe_title}-v{data["version"]}.{extension}'
                    headers['Content-Disposition'] = f'attachment; filename="artifact-v{data["version"]}.{extension}"; filename*=UTF-8\'\'{quote(filename, safe="")}'
                return 200, data['content'].encode('utf-8'), mime, headers
        else:
            return 404, {'error': '接口不存在'}, JSON, {}
        return 200, data, JSON, {}
    except hub.HubError as exc:
        return exc.status, {'error': str(exc)}, JSON, {}
