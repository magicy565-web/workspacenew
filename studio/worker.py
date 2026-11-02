"""Run an agent host command against leased workspace tasks, without a model provider."""
from __future__ import annotations

import argparse
import getpass
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

MAX_OUTPUT = 8 * 1024 * 1024
NETWORK_ERRORS = (OSError, http.client.HTTPException)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


class APIError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class Client:
    def __init__(self, server, token=None):
        parsed = urlsplit(server)
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('server must not contain credentials, query or fragment')
        if parsed.scheme not in ('http', 'https') or not parsed.hostname:
            raise ValueError('server must be an http(s) URL')
        if parsed.scheme == 'http' and parsed.hostname not in ('localhost', '127.0.0.1', '::1'):
            raise ValueError('Remote workspaces require HTTPS')
        self.base = server.rstrip('/') + '/api/hub'
        self.token = token
        self.deadline = None
        handlers = [NoRedirect()]
        if parsed.hostname in ('localhost', '127.0.0.1', '::1'):
            handlers.append(ProxyHandler({}))
        self.opener = build_opener(*handlers)

    def request(self, method, path, data=None):
        if not path.startswith('/') or '?' in path.split('/')[0] or path.startswith('//'):
            raise ValueError('API path must be relative to the workspace')
        headers = {'Content-Type': 'application/json'}
        if self.token:
            headers['Authorization'] = 'Bearer ' + self.token
        payload = json.dumps(data, ensure_ascii=False).encode('utf-8') if data is not None else None
        request = Request(self.base + path, data=payload, headers=headers, method=method)
        try:
            timeout = 15
            if self.deadline is not None:
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('执行器运行超时')
                timeout = min(timeout, remaining)
            with self.opener.open(request, timeout=timeout) as response:
                return json.load(response)
        except HTTPError as exc:
            try:
                message = json.loads(exc.read()).get('error', f'HTTP {exc.code}')
            except (ValueError, AttributeError):
                message = f'HTTP {exc.code}'
            raise APIError(exc.code, message) from exc

    def post(self, path, data):
        return self.request('POST', path, data)

    def get(self, path):
        return self.request('GET', path)


def load_config(path):
    config = json.loads(Path(path).read_text(encoding='utf-8-sig'))
    if not isinstance(config, dict):
        raise ValueError('Executor configuration must be an object')
    command = config.get('command')
    if not isinstance(command, list) or not command or not all(isinstance(arg, str) and arg and '\x00' not in arg for arg in command):
        raise ValueError('command must be a nonempty JSON array of executable and arguments')
    if not isinstance(config.get('worker_id'), str) or not config['worker_id'].strip():
        raise ValueError('worker_id is required')
    if 'cwd' in config and not Path(config['cwd']).is_dir():
        raise ValueError('cwd must be an existing directory')
    timeout = config.get('timeout_seconds', 1800)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 86400:
        raise ValueError('timeout_seconds must be between 1 and 86400')
    config['timeout_seconds'] = timeout
    if config.get('output_protocol', 'json') not in ('json', 'jsonl'):
        raise ValueError('output_protocol must be json or jsonl')
    capabilities = config.get('capabilities', ['command-executor'])
    if not isinstance(capabilities, list) or not all(isinstance(item, str) and 0 < len(item) <= 100 for item in capabilities):
        raise ValueError('capabilities must be a list of nonempty strings')
    if not isinstance(config.get('follow_registered_agents', False), bool):
        raise ValueError('follow_registered_agents must be a boolean')
    return config


def host_identity(config):
    """Bind resumable receipts to this host/config without transmitting local paths."""
    identity = {
        'machine': socket.gethostname(), 'user': getpass.getuser(),
        'cwd': str(Path(config.get('cwd') or Path.cwd()).resolve()),
        'command': config['command'], 'protocol': config.get('output_protocol', 'json'),
        'storage': str(Path(os.environ.get('CODEX_HOME') or Path.home() / '.codex').resolve()),
    }
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()


def post_with_retry(client, path, payload, pulse=None):
    # Retries preserve the exact event/result and key. HTTP authorization/conflict
    # errors are APIError, not network failures, and must never be retried here.
    for attempt in range(3):
        if pulse:
            pulse()
        try:
            return client.post(path, payload)
        except NETWORK_ERRORS:
            if attempt == 2:
                raise
            time.sleep(0.5 * (attempt + 1))


def execution_history(client, execution, pulse=None):
    """Read the durable tape; pass bounded public receipts, never private traces."""
    cursor, records, total = 0, [], 0
    while True:
        if pulse:
            pulse()
        page = client.get('/executions/' + execution['id'] + '/events?after=' + str(cursor) + '&limit=100')
        events = page.get('events', [])
        total += len(events)
        for event in events:
            item = {key: event[key] for key in ('seq', 'kind', 'attempt', 'payload') if key in event}
            item['payload'] = dict(item.get('payload', {}))
            for key in ('summary', 'message', 'note'):
                value = item['payload'].get(key)
                if isinstance(value, str) and len(value) > 400:
                    item['payload'][key] = value[:400]
                    item['summary_shortened'] = True
            records.append(item)
        next_cursor = page.get('cursor', cursor)
        if not page.get('has_more'):
            break
        if next_cursor <= cursor or total > 2048:
            raise ValueError('执行记录分页无效')
        cursor = next_cursor
    # Full call states and the recovery decision are always in execution.recovery.
    # Older progress text is optional; retain all call and reconciliation receipts.
    ordinary = [index for index, item in enumerate(records) if item['kind'] == 'progress']
    discard = set(ordinary[:-10])
    records = [item for index, item in enumerate(records) if index not in discard]
    if len(json.dumps(records, ensure_ascii=False).encode('utf-8')) > 512 * 1024:
        raise ValueError('执行记录超过宿主上下文上限，请核对任务后再继续')
    return {'events': records, 'total_events': total, 'omitted_progress': len(discard), 'cursor': next_cursor}


class LeasePulse:
    def __init__(self, config, claim, client, heartbeat_seconds):
        self.config, self.claim, self.client = config, claim, client
        self.owner = {'worker_id': config['worker_id'], 'lease_token': claim['lease_token']}
        self.interval, self.next = heartbeat_seconds, 0

    def __call__(self, force=False):
        current = time.monotonic()
        if self.client.deadline is not None and current >= self.client.deadline:
            raise TimeoutError('执行器运行超时')
        if force or current >= self.next:
            if self.config.get('session_token'):
                self.client.post('/workers/' + self.config['worker_id'] + '/ping', {'session_token': self.config['session_token']})
            self.client.post('/tasks/' + self.claim['task']['id'] + '/heartbeat', dict(self.owner, progress='执行器正在处理任务'))
            self.next = time.monotonic() + self.interval


class ExecutionStream:
    """Strict opt-in JSONL protocol for durable reported public activity."""
    def __init__(self, reader, config, claim, client, pulse=None):
        self.reader, self.client = reader, client
        self.owner = {'worker_id': config['worker_id'], 'lease_token': claim['lease_token']}
        self.task_id = claim['task']['id']
        self.identity = host_identity(config)
        self.last_seq = claim['execution']['last_seq']
        self.pending = b''
        self.result = None
        self.sent_events = {}
        self.pulse = pulse

    def drain(self, final=False):
        self.pending += self.reader.read(MAX_OUTPUT + 1)
        if len(self.pending) > MAX_OUTPUT:
            raise ValueError('执行器单行输出超过 8 MB 限制')
        while b'\n' in self.pending:
            line, self.pending = self.pending.split(b'\n', 1)
            if line.strip():
                self.record(line)
        if final and self.pending.strip():
            self.record(self.pending)
            self.pending = b''

    def record(self, line):
        if self.pulse:
            self.pulse()
        try:
            record = json.loads(line.decode('utf-8-sig'))
        except (ValueError, UnicodeError) as exc:
            raise ValueError('执行器 JSONL 行不是有效 UTF-8 JSON') from exc
        if not isinstance(record, dict) or self.result is not None:
            raise ValueError('执行器必须在所有事件之后返回唯一结果')
        if record.get('type') == 'result' and set(record) == {'type', 'result'}:
            if not isinstance(record['result'], dict):
                raise ValueError('执行器结果必须是 JSON 对象')
            self.result = record['result']
            return
        if record.get('type') != 'execution_event' or set(record) != {'type', 'event_id', 'kind', 'payload'} or not isinstance(record['payload'], dict):
            raise ValueError('未知执行器 JSONL 事件格式')
        event_id = record['event_id']
        if not isinstance(event_id, str) or not event_id or len(event_id) > 120:
            raise ValueError('执行事件必须提供稳定的字符串 ID')
        fingerprint = hashlib.sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')).hexdigest()
        payload = dict(record['payload'])
        if record['kind'] in ('host_session', 'checkpoint'):
            if payload.get('host_identity', self.identity) != self.identity:
                raise ValueError('宿主检查点与当前执行器身份不匹配')
            payload['host_identity'] = self.identity
        if record['kind'] == 'checkpoint':
            # Child stdout is one-way: the parent supplies its latest committed
            # sequence instead of letting the child guess a database sequence.
            payload['through_seq'] = self.last_seq
        previous = self.sent_events.get(event_id)
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ValueError('同一执行事件 ID 不可用于不同内容')
            sent = previous['payload']
        else:
            sent = dict(self.owner, event_id=event_id, kind=record['kind'], payload=payload, expected_seq=self.last_seq)
            self.sent_events[event_id] = {'fingerprint': fingerprint, 'payload': sent}
        ack = post_with_retry(self.client, '/tasks/' + self.task_id + '/execution/events', sent, self.pulse)
        self.last_seq = ack['execution']['last_seq']
        if ack['execution'].get('recovery', {}).get('decision') == 'review_required':
            raise APIError(409, '工具结果待核对，执行器已停止')


def stop_process(process):
    if process.poll() is not None:
        return
    if os.name == 'nt':
        subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != 'nt':
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)


def execute(config, claim, client, heartbeat_seconds=30, poll_seconds=0.5):
    previous = client.deadline
    client.deadline = time.monotonic() + config.get('timeout_seconds', 1800)
    try:
        return _execute(config, claim, client, heartbeat_seconds, poll_seconds)
    finally:
        client.deadline = previous


def _execute(config, claim, client, heartbeat_seconds, poll_seconds):
    task = claim['task']
    owner = {'worker_id': config['worker_id'], 'lease_token': claim['lease_token']}
    pulse = LeasePulse(config, claim, client, heartbeat_seconds)
    pulse(force=True)
    execution = claim.get('execution')
    if execution and execution['recovery']['decision'] == 'submit_result':
        staged = post_with_retry(client, '/tasks/' + task['id'] + '/execution/result/read', owner, pulse)
        return post_with_retry(client, '/tasks/' + task['id'] + '/complete', dict(owner, result=staged['result']))
    if execution and execution['recovery']['decision'] == 'review_required':
        raise APIError(409, '工具结果待核对，不能自动重新执行')
    streaming = config.get('output_protocol', 'json') == 'jsonl'
    if streaming and not execution:
        raise ValueError('当前服务不支持持久执行事件，请升级服务或使用 json 输出协议')
    envelope = {
        'protocol': 'agent-workspace/1',
        'task': task,
        'context': claim.get('context', {}),
        'output': 'Return exactly one UTF-8 JSON object: {"message":"...","artifacts":[{"title":"...","kind":"markdown","content":"..."}]}. Coordinator may include plan. No markdown fences. Existing artifacts need id and base_version. Only report work actually performed.',
    }
    if execution:
        envelope['execution'] = dict(execution, host_identity=host_identity(config))
        if execution['attempt'] > 1:
            envelope['execution']['history'] = execution_history(client, execution, pulse)
    if streaming:
        envelope['event_protocol'] = 'agent-workspace/events-v1'
        envelope['output'] = 'Write UTF-8 JSONL: execution_event records followed by exactly one {"type":"result","result":{...}}. Public progress and tool receipts only; no private reasoning or credentials. See docs/executor-protocol.md.'
    env = dict(os.environ, PYTHONUTF8='1')
    env.pop('STUDIO_WORKER_TOKEN', None)
    env.pop('STUDIO_WORKER_SESSION', None)
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {'start_new_session': True}
    process = None
    with tempfile.TemporaryDirectory(prefix='workspace-worker-') as folder, tempfile.TemporaryFile() as incoming, open(Path(folder) / 'stdout.jsonl', 'wb') as output, tempfile.TemporaryFile() as errors, open(Path(folder) / 'stdout.jsonl', 'rb') as reader:
        incoming.write(json.dumps(envelope, ensure_ascii=False).encode('utf-8'))
        incoming.seek(0)
        try:
            pulse(force=True)
            process = subprocess.Popen(config['command'], cwd=config.get('cwd'), stdin=incoming, stdout=output, stderr=errors, env=env, shell=False, **kwargs)
            stream = ExecutionStream(reader, config, claim, client, pulse) if streaming else None
            while process.poll() is None:
                pulse()
                if os.fstat(output.fileno()).st_size > MAX_OUTPUT or os.fstat(errors.fileno()).st_size > MAX_OUTPUT:
                    raise ValueError('执行器输出超过 8 MB 限制')
                if stream:
                    stream.drain()
                time.sleep(poll_seconds)
            if stream:
                stream.drain(final=True)
            if os.fstat(output.fileno()).st_size > MAX_OUTPUT or os.fstat(errors.fileno()).st_size > MAX_OUTPUT:
                raise ValueError('执行器输出超过 8 MB 限制')
            if process.returncode != 0:
                raise ValueError(f'执行器退出，状态码 {process.returncode}；请检查命令及其模型配置')
            if stream:
                result = stream.result
            else:
                reader.seek(0)
                raw = reader.read(MAX_OUTPUT + 1)
                if len(raw) > MAX_OUTPUT:
                    raise ValueError('执行器输出超过 8 MB 限制')
                try:
                    result = json.loads(raw.decode('utf-8-sig'))
                except (ValueError, UnicodeError) as exc:
                    raise ValueError('执行器必须返回一个 UTF-8 JSON 结果对象') from exc
            if not isinstance(result, dict):
                raise ValueError('执行器结果必须是 JSON 对象')
            if execution:
                post_with_retry(client, '/tasks/' + task['id'] + '/execution/result', dict(owner, result=result), pulse)
            pulse(force=True)
            payload = dict(owner, result=result)
            return post_with_retry(client, '/tasks/' + task['id'] + '/complete', payload)
        finally:
            if process is not None:
                stop_process(process)


def run_once(config, client, heartbeat_seconds=30):
    request = {'worker_id': config['worker_id'], 'host_identity': host_identity(config)}
    if 'capabilities' in config:
        request['capabilities'] = config['capabilities']
    for field in ('agent_ids', 'room_id', 'session_token'):
        if field == 'agent_ids' and config.get('follow_registered_agents'):
            # This only selects the server-owned registration allowlist. It
            # cannot add roles to a fixed/external worker's registration.
            continue
        if config.get(field) is not None:
            request[field] = config[field]
    if config.get('follow_registered_agents') and not config.get('session_token'):
        raise ValueError('follow_registered_agents requires an existing registration session')
    claim = client.post('/tasks/claim', request)
    if claim.get('task') is None:
        return False
    task_id = claim['task']['id']
    print(json.dumps({'event': 'claimed', 'task_id': task_id}), flush=True)
    try:
        execute(config, claim, client, heartbeat_seconds=heartbeat_seconds)
        print(json.dumps({'event': 'completed', 'task_id': task_id}), flush=True)
    except APIError as exc:
        # /fail rechecks ownership: a stale/cancelled/review-blocked lease cannot
        # change state, while an ordinary result conflict must not stay running.
        report_failure(client, config, claim, str(exc))
        print(json.dumps({'event': 'rejected', 'task_id': task_id, 'status': exc.status}, ensure_ascii=False), flush=True)
    except (ValueError, OSError, http.client.HTTPException) as exc:
        report_failure(client, config, claim, str(exc))
        print(json.dumps({'event': 'failed', 'task_id': task_id, 'error': str(exc)}, ensure_ascii=False), flush=True)
    except KeyboardInterrupt:
        report_failure(client, config, claim, '执行器已停止')
        raise
    return True


def report_failure(client, config, claim, message):
    try:
        client.post('/tasks/' + claim['task']['id'] + '/fail', {'worker_id': config['worker_id'], 'lease_token': claim['lease_token'], 'error': message[:2000]})
    except (APIError, OSError, http.client.HTTPException):
        pass


def main():
    def terminate(_signal, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, terminate)
    parser = argparse.ArgumentParser(description='Connect a model-owning agent command to Agent Workspace')
    parser.add_argument('--config', required=True, help='Executor JSON configuration')
    parser.add_argument('--server', default='http://127.0.0.1:8765')
    parser.add_argument('--once', action='store_true', help='Claim at most one ready task and exit')
    parser.add_argument('--poll-seconds', type=float, default=3)
    args = parser.parse_args()
    config = None
    client = None
    try:
        config = load_config(args.config)
        client = Client(args.server, os.environ.get('STUDIO_WORKER_TOKEN'))
        if not 1 <= args.poll_seconds <= 300:
            parser.error('poll-seconds must be between 1 and 300')
        if config.get('follow_registered_agents') and not os.environ.get('STUDIO_WORKER_SESSION'):
            raise ValueError('follow_registered_agents requires STUDIO_WORKER_SESSION; automatic registration is disabled')
        if not config.get('agent_ids') and not config.get('follow_registered_agents'):
            config['agent_ids'] = [agent['id'] for agent in client.get('/agents')['agents']]
        if os.environ.get('STUDIO_WORKER_SESSION'):
            config['session_token'] = os.environ['STUDIO_WORKER_SESSION']
        else:
            registration = client.post('/workers/register', {'worker_id': config['worker_id'], 'name': config.get('name', config['worker_id']), 'agent_ids': config['agent_ids'], 'capabilities': config.get('capabilities', ['command-executor'])})
            config['session_token'] = registration['session_token']
        next_ping = time.monotonic()
        while True:
            try:
                if time.monotonic() >= next_ping:
                    client.post('/workers/' + config['worker_id'] + '/ping', {'session_token': config['session_token']})
                    next_ping = time.monotonic() + 30
                worked = run_once(config, client)
            except (APIError, OSError, http.client.HTTPException) as exc:
                print(json.dumps({'event': 'connection_error', 'error': str(exc)}, ensure_ascii=False), file=sys.stderr, flush=True)
                if isinstance(exc, APIError) and exc.status in (401, 403, 409):
                    return 1
                if args.once:
                    return 1
                worked = False
            if args.once:
                return 0
            if not worked:
                time.sleep(args.poll_seconds)
    except KeyboardInterrupt:
        return 0
    except (ValueError, OSError, APIError, http.client.HTTPException) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    finally:
        if config and client and config.get('session_token'):
            try:
                client.post('/workers/' + config['worker_id'] + '/disconnect', {'session_token': config['session_token']})
            except (APIError, OSError, http.client.HTTPException):
                pass


if __name__ == '__main__':
    raise SystemExit(main())
