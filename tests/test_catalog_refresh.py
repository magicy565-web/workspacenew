"""Managed catalog tests; the subprocess fixture is not a model response."""
import contextlib
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from studio import accounts, core, executors, hub, worker
from studio.server import Handler


ROOT = Path(__file__).resolve().parents[1]


class QuietHandler(Handler):
    def log_message(self, *_args):
        pass


class CatalogRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.environment = patch.dict(os.environ, {
            'STUDIO_DATA_DIR': str(self.root), 'STUDIO_AUTH_MODE': '',
            'STUDIO_PUBLIC_ORIGIN': '', 'STUDIO_WORKER_TOKEN': '',
            'STUDIO_WORKER_SESSION': '',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.addCleanup(executors.shutdown)

    def bind(self, directory):
        @contextlib.contextmanager
        def binding():
            token = core.REQUEST_DATA_ROOT.set(directory)
            try:
                yield
            finally:
                core.REQUEST_DATA_ROOT.reset(token)
        return binding()

    def register_record(self, worker_id='managed', principal=None):
        principal = principal or accounts.identify({})
        data_root = accounts.data_directory(principal)
        issued = None
        if not principal.get('local'):
            issued = accounts.dispatch('POST', '/api/account/tokens', {'name': 'catalog fixture', 'scopes': ['read', 'write', 'execute']}, principal)[1]
        with self.bind(data_root):
            registered = hub.register_worker(worker_id, [a['id'] for a in hub.list_agents()], capabilities=['test-command'])
        return {
            'worker_id': worker_id, 'session_token': registered['session_token'],
            'data_root': data_root, 'api_token_id': issued['record']['id'] if issued else None,
            'owner_id': principal['user']['id'], 'workspace_id': principal['workspace']['id'],
            'refresh_agent_catalog': True,
        }, issued

    def worker_row(self, record):
        with self.bind(record['data_root']), contextlib.closing(hub._db()) as conn:
            return dict(conn.execute('SELECT * FROM hub_workers WHERE id=?', (record['worker_id'],)).fetchone())

    def new_agent(self, name='Created after registration'):
        return hub.create_agent(name, 'Protocol fixture role', 'Echo only the supplied fixture input.')

    def assert_hub_error(self, status, callback, *args):
        with self.assertRaises(hub.HubError) as caught:
            callback(*args)
        self.assertEqual(caught.exception.status, status)

    def setup_owner(self):
        _, headers = accounts.setup({'email': 'owner@example.test', 'name': 'Owner', 'password': 'correct-fixture-password-731'})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        return accounts.identify({'Cookie': cookie}), cookie

    def serve(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
        self.addCleanup(close)
        return f'http://127.0.0.1:{server.server_port}'

    def until(self, predicate, timeout=12):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.05)
        self.fail('Condition was not met before its finite deadline')

    def test_add_remove_changes_future_claims_without_touching_current_lease(self):
        original = self.new_agent('Original role')
        unused = self.new_agent('Unused role removed from directory')
        record, _ = self.register_record()
        room = hub.create_room('Already held task', agent_id=original['id'])
        orphan = hub.create_room('Queued before directory removal', agent_id=original['id'])
        claim = hub.claim_task(record['worker_id'], room_id=room['id'], session_token=record['session_token'])
        before = self.worker_row(record)
        with contextlib.closing(hub._db()) as conn:
            lease = dict(conn.execute('SELECT * FROM hub_tasks WHERE id=?', (claim['task']['id'],)).fetchone())
        added = self.new_agent()
        # The public product has no delete-Agent API, and referenced Agents have
        # FK protection. Physically remove an unused role; simulate the catalog
        # temporarily excluding the held role without damaging its stored rows.
        with hub._transaction() as conn:
            conn.execute('DELETE FROM hub_agents WHERE id=?', (unused['id'],))
        actual_catalog = executors._catalog_agent_ids
        with patch.object(executors, '_catalog_agent_ids', side_effect=lambda conn: [agent_id for agent_id in actual_catalog(conn) if agent_id != original['id']]):
            self.assertTrue(executors._refresh_catalog(record))
        after = self.worker_row(record)
        for key in before:
            if key != 'agent_ids_json':
                self.assertEqual(before[key], after[key], key)
        self.assertIn(added['id'], json.loads(after['agent_ids_json']))
        self.assertNotIn(original['id'], json.loads(after['agent_ids_json']))
        self.assertNotIn(unused['id'], json.loads(after['agent_ids_json']))
        with contextlib.closing(hub._db()) as conn:
            self.assertEqual(lease, dict(conn.execute('SELECT * FROM hub_tasks WHERE id=?', (claim['task']['id'],)).fetchone()))
        hub.heartbeat_task(claim['task']['id'], record['worker_id'], claim['lease_token'])
        completed = hub.complete_task(claim['task']['id'], record['worker_id'], claim['lease_token'], {'message': 'Held fixture task completed'})
        self.assertEqual(completed['status'], 'completed')
        self.assertIsNone(hub.claim_task(record['worker_id'], room_id=orphan['id'], session_token=record['session_token'])['task'])
        new_room = hub.create_room('New role now eligible', agent_id=added['id'])
        self.assertEqual(hub.claim_task(record['worker_id'], room_id=new_room['id'], session_token=record['session_token'])['task']['agent_id'], added['id'])

    def test_disconnected_expired_and_replaced_sessions_never_refresh_or_reregister(self):
        for state in ('disconnected', 'expired', 'replaced'):
            with self.subTest(state=state):
                record, _ = self.register_record('managed-' + state)
                if state in ('disconnected', 'replaced'):
                    hub.disconnect_worker(record['worker_id'], record['session_token'])
                if state == 'expired':
                    with hub._transaction() as conn:
                        conn.execute("UPDATE hub_workers SET expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (record['worker_id'],))
                if state == 'replaced':
                    hub.register_worker(record['worker_id'], ['writer'])
                before = self.worker_row(record)
                self.new_agent(state)
                actor = core.REQUEST_ACTOR.set({'sentinel': state})
                binding = core.REQUEST_DATA_ROOT.set(self.root / 'unrelated-request')
                try:
                    self.assert_hub_error(409, executors._refresh_catalog, record)
                    self.assertEqual(core.REQUEST_ACTOR.get(), {'sentinel': state})
                    self.assertEqual(core.REQUEST_DATA_ROOT.get(), self.root / 'unrelated-request')
                finally:
                    core.REQUEST_ACTOR.reset(actor)
                    core.REQUEST_DATA_ROOT.reset(binding)
                self.assertEqual(before, self.worker_row(record))

    def test_follow_flag_cannot_expand_fixed_external_registration(self):
        registered = hub.register_worker('external-fixed', ['writer'])
        record = {'refresh_agent_catalog': False}
        self.assertFalse(executors._refresh_catalog(record))
        added = self.new_agent()
        room = hub.create_room('Must stay queued', agent_id=added['id'])
        client = worker.Client(self.serve())
        config = {
            'worker_id': 'external-fixed', 'session_token': registered['session_token'],
            'follow_registered_agents': True, 'agent_ids': [added['id']],
            'command': [sys.executable, '-c', 'raise SystemExit("must not execute")'],
            'room_id': room['id'],
        }
        self.assertFalse(worker.run_once(config, client))
        self.assertEqual(hub.list_workers()[0]['agent_ids'], ['writer'])
        self.assert_hub_error(409, hub.claim_task, 'external-fixed', [added['id']], room['id'], registered['session_token'])
        self.assertEqual(hub.get_room(room['id'])['status'], 'queued')
        del config['session_token']
        with self.assertRaisesRegex(ValueError, 'registration session'):
            worker.run_once(config, client)

    def test_fixed_worker_request_keeps_explicit_filter_and_follow_requires_existing_session(self):
        requests = []
        class NoTaskClient:
            def post(self, path, body):
                requests.append((path, body))
                return {'task': None}
        config = {'worker_id': 'fixed', 'agent_ids': ['writer'], 'command': [sys.executable]}
        self.assertFalse(worker.run_once(config, NoTaskClient()))
        self.assertEqual(requests[0][1]['agent_ids'], ['writer'])
        config_path = self.root / 'follow.json'
        config_path.write_text(json.dumps(dict(config, follow_registered_agents='true')), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'boolean'):
            worker.load_config(config_path)
        config_path.write_text(json.dumps(dict(config, follow_registered_agents=True)), encoding='utf-8')
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'studio.worker', '--config', str(config_path), '--server', self.serve(), '--once'], cwd=ROOT, capture_output=True, encoding='utf-8', timeout=12)
        self.assertEqual(result.returncode, 1)
        self.assertIn('automatic registration is disabled', result.stderr)
        self.assertEqual(hub.list_workers(), [])

    def test_refresh_is_pinned_to_issued_token_workspace_and_restores_context(self):
        principal, cookie = self.setup_owner()
        record_a, token_a = self.register_record(principal=principal)
        agent_a = self.new_agent('Workspace A only')
        accounts.dispatch('POST', '/api/account/workspaces', {'name': 'Workspace B'}, principal)
        principal_b = accounts.identify({'Cookie': cookie})
        root_b = accounts.data_directory(principal_b)
        record_b, _ = self.register_record(principal=principal_b)
        with self.bind(root_b):
            agent_b = self.new_agent('Workspace B only')
            room_b = hub.create_room('Private B task', agent_id=agent_b['id'])
            unchanged_b = self.worker_row(record_b)
            marker = core.REQUEST_ACTOR.set({'workspace_id': principal_b['workspace']['id']})
            try:
                self.assertTrue(executors._refresh_catalog(record_a))
                self.assertEqual(core.data_root(), root_b)
                self.assertEqual(core.REQUEST_ACTOR.get()['workspace_id'], principal_b['workspace']['id'])
            finally:
                core.REQUEST_ACTOR.reset(marker)
        ids = json.loads(self.worker_row(record_a)['agent_ids_json'])
        self.assertIn(agent_a['id'], ids)
        self.assertNotIn(agent_b['id'], ids)
        self.assertEqual(unchanged_b, self.worker_row(record_b))
        client_a = worker.Client(self.serve(), token_a['token'])
        with self.assertRaises(worker.APIError) as rejected:
            client_a.post('/tasks/claim', {'worker_id': record_a['worker_id'], 'session_token': record_a['session_token'], 'room_id': room_b['id']})
        self.assertEqual(rejected.exception.status, 404)
        forged = dict(record_a, data_root=root_b)
        self.assert_hub_error(403, executors._refresh_catalog, forged)
        self.assertEqual(unchanged_b, self.worker_row(record_b))

    def test_revoked_token_membership_and_local_mode_transition_stop_expansion(self):
        local_record, _ = self.register_record('local-before-setup')
        principal, _ = self.setup_owner()
        self.assert_hub_error(403, executors._refresh_catalog, local_record)
        for change in ('token', 'viewer', 'removed', 'execute-scope'):
            with self.subTest(change=change):
                with accounts.transaction() as conn:
                    conn.execute('INSERT OR REPLACE INTO members VALUES (?,?,?)', (principal['workspace']['id'], principal['user']['id'], 'owner'))
                record, issued = self.register_record('managed-' + change, principal=principal)
                before = self.worker_row(record)
                self.new_agent(change)
                with accounts.transaction() as conn:
                    if change == 'token':
                        conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?', (accounts.now(), issued['record']['id']))
                    elif change == 'viewer':
                        conn.execute("UPDATE members SET role='viewer' WHERE workspace_id=? AND user_id=?", (record['workspace_id'], record['owner_id']))
                    elif change == 'removed':
                        conn.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?', (record['workspace_id'], record['owner_id']))
                    else:
                        conn.execute('UPDATE api_tokens SET scopes=? WHERE id=?', (json.dumps(['read']), issued['record']['id']))
                self.assert_hub_error(401 if change == 'token' else 403, executors._refresh_catalog, record)
                self.assertEqual(before, self.worker_row(record))

    def test_refresh_never_restores_a_cancelled_lease(self):
        record, _ = self.register_record()
        room = hub.create_room('Cancellation fixture', agent_id='writer')
        claim = hub.claim_task(record['worker_id'], session_token=record['session_token'])
        hub.cancel_run(room['runs'][0]['id'])
        self.new_agent()
        self.assertTrue(executors._refresh_catalog(record))
        self.assert_hub_error(409, hub.heartbeat_task, claim['task']['id'], record['worker_id'], claim['lease_token'])
        self.assert_hub_error(409, hub.complete_task, claim['task']['id'], record['worker_id'], claim['lease_token'], {'message': 'Must be rejected'})
        self.assertIsNone(hub.claim_task(record['worker_id'], session_token=record['session_token'])['task'])

    def test_managed_process_claims_new_role_without_restart_and_child_has_no_credentials(self):
        principal, _ = self.setup_owner()
        url = self.serve()
        evidence = self.root / 'child-invocations.jsonl'
        fixture = self.root / 'protocol_fixture.py'
        fixture.write_text('''import json, os, sys
from pathlib import Path
def process_ancestors():
    if os.name != "nt":
        return [os.getppid()]
    import ctypes
    from ctypes import wintypes
    class Entry(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD), ("ppid", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD), ("exe", wintypes.WCHAR * 260)]
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = api.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise OSError("Cannot inspect fixture process ancestry")
    parents = {}
    try:
        row = Entry()
        row.size = ctypes.sizeof(row)
        more = api.Process32FirstW(snapshot, ctypes.byref(row))
        while more:
            parents[row.pid] = row.ppid
            more = api.Process32NextW(snapshot, ctypes.byref(row))
    finally:
        api.CloseHandle(snapshot)
    chain, pid = [], os.getpid()
    while parents.get(pid) and parents[pid] not in chain and len(chain) < 20:
        pid = parents[pid]
        chain.append(pid)
    return chain
envelope = json.load(sys.stdin)
record = {"pid": os.getpid(), "parent_pid": os.getppid(), "ancestor_pids": process_ancestors(), "agent_id": envelope["task"]["agent_id"], "task_id": envelope["task"]["id"], "token_env_present": any(key in os.environ for key in ("STUDIO_WORKER_TOKEN", "STUDIO_WORKER_SESSION")), "private_envelope_fields": [key for key in ("session_token", "lease_token") if key in envelope]}
with Path(sys.argv[1]).open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(record) + "\\n")
print(json.dumps({"message": "Protocol fixture consumed the real assigned input", "artifacts": [{"title": "Protocol fixture input", "kind": "text", "content": envelope["task"]["instructions"]}]}))
''', encoding='utf-8')
        actual_popen = subprocess.Popen
        def launch(command, **options):
            config_path = Path(command[command.index('--config') + 1])
            config = json.loads(config_path.read_text(encoding='utf-8'))
            self.assertTrue(config['follow_registered_agents'])
            self.assertNotIn('agent_ids', config)
            self.assertNotIn('session_token', config)
            self.assertNotIn('STUDIO_WORKER_TOKEN', config_path.read_text(encoding='utf-8'))
            config.update(command=[sys.executable, '-X', 'utf8', str(fixture), str(evidence)], output_protocol='json', timeout_seconds=15)
            config_path.write_text(json.dumps(config), encoding='utf-8')
            return actual_popen(command, **options)
        with patch.object(executors.shutil, 'which', return_value=sys.executable), patch.object(executors.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)), patch.object(executors.subprocess, 'Popen', side_effect=launch):
            self.assertEqual(executors.start(principal, url)['state'], 'running')
        managed = executors._managed[str(self.root)]
        self.addCleanup(lambda: managed['catalog_thread'].join(timeout=3))
        original_pid = managed['process'].pid
        # The real worker has pinged over HTTP before the role exists. No new
        # registration or process restart is used to make the new task eligible.
        self.until(lambda: self.worker_row(managed)['last_seen_at'] != self.worker_row(managed)['registered_at'])
        before = self.worker_row(managed)
        new_agent = self.new_agent('Created while managed worker is connected')
        text = 'Exact fixture input, not a generated model answer.'
        room = hub.create_room(text, agent_id=new_agent['id'])
        elapsed_start = time.monotonic()
        self.until(lambda: hub.get_room(room['id'])['status'] == 'completed', timeout=14)
        elapsed = time.monotonic() - elapsed_start
        after = self.worker_row(managed)
        self.assertEqual(managed['process'].pid, original_pid)
        self.assertIsNone(managed['process'].poll())
        self.assertEqual(before['session_hash'], after['session_hash'])
        self.assertEqual(before['registered_at'], after['registered_at'])
        records = [json.loads(line) for line in evidence.read_text(encoding='utf-8').splitlines()]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['agent_id'], new_agent['id'])
        self.assertIn(original_pid, records[0]['ancestor_pids'])
        self.assertFalse(records[0]['token_env_present'])
        self.assertEqual(records[0]['private_envelope_fields'], [])
        artifact = hub.get_room(room['id'])['artifacts'][0]
        self.assertEqual(hub.get_artifact(artifact['id'])['content'], text)
        self.assertLess(elapsed, 14)
        # Revoking the actual token stops this process; the catalog loop may not
        # create another registration, launch a replacement, or start new work.
        with accounts.transaction() as conn:
            conn.execute('UPDATE api_tokens SET revoked_at=? WHERE id=?', (accounts.now(), managed['api_token_id']))
        future = self.new_agent('Created after token revocation')
        pending = hub.create_room('Must remain queued after revocation', agent_id=future['id'])
        self.until(lambda: managed['process'].poll() is not None, timeout=9)
        managed['catalog_stop'].set()
        managed['catalog_thread'].join(timeout=3)
        self.assertEqual(hub.get_room(pending['id'])['status'], 'queued')
        self.assertNotIn(future['id'], json.loads(self.worker_row(managed)['agent_ids_json']))
        self.assertEqual(len(evidence.read_text(encoding='utf-8').splitlines()), 1)
        self.assertEqual(self.worker_row(managed)['session_hash'], before['session_hash'])
        report_path = os.environ.get('CATALOG_TEST_REPORT')
        if report_path:
            Path(report_path).write_text(json.dumps({
                'kind': 'real HTTP + managed worker + actual protocol-fixture child; no model response claimed',
                'worker_pid_unchanged': original_pid, 'registration_unchanged': True,
                'catalog_refresh_seconds': executors.CATALOG_REFRESH_SECONDS,
                'completion_elapsed_seconds': round(elapsed, 3), 'child_invocations': records,
                'token_revocation_stopped_process': True, 'post_revocation_task_status': 'queued',
            }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()
