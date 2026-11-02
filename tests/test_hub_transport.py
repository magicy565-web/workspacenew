import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

from studio import hub, hub_cli, worker
from studio.server import Handler

ROOT = Path(__file__).resolve().parents[1]


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.base = f'http://127.0.0.1:{self.server.server_port}'
        self.client = worker.Client(self.base)
        self.opener = build_opener(ProxyHandler({}))

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def get(self, path):
        return self.opener.open(self.base + path)

    def test_http_persistence_exact_download_headers_and_host_guards(self):
        with self.get('/api/hub/connection') as response:
            config = json.load(response)
        self.assertEqual(Path(config['mcp_config']['mcpServers']['agent-workspace']['env']['STUDIO_DATA_DIR']).resolve(), Path(self.temp.name).resolve())
        room = self.client.post('/rooms', {'text': '验证 HTML 成果', 'agent_id': 'designer'})
        claim = self.client.post('/tasks/claim', {'worker_id': 'http-test', 'room_id': room['id']})
        content = '<html><body>版本一<script>parent.document.body.textContent="unsafe"</script></body></html>\n'
        room = self.client.post('/tasks/' + claim['task']['id'] + '/complete', {
            'worker_id': 'http-test', 'lease_token': claim['lease_token'],
            'result': {'message': '测试成果已保存', 'artifacts': [{'title': '中文界面', 'kind': 'html', 'content': content}]},
        })
        artifact = room['artifacts'][0]['id']
        with self.get('/api/hub/artifacts/' + artifact + '/download?version=1') as response:
            self.assertEqual(response.read(), content.encode())
            self.assertIn("filename*=UTF-8''", response.headers['Content-Disposition'])
        with self.get('/api/hub/artifacts/' + artifact + '/preview?version=1') as response:
            self.assertEqual(len(response.headers.get_all('Content-Security-Policy')), 1)
            self.assertIn('sandbox;', response.headers['Content-Security-Policy'])
            self.assertIn("default-src 'none'", response.headers['Content-Security-Policy'])
        for headers, path, data in [
            ({'Origin': 'https://evil.example', 'Content-Type': 'application/json'}, '/api/hub/rooms', b'{"text":"cross-origin"}'),
            ({'Host': 'evil.example'}, '/api/hub/bootstrap', None),
        ]:
            with self.assertRaises(HTTPError) as rejected:
                self.opener.open(Request(self.base + path, headers=headers, data=data))
            self.assertEqual(rejected.exception.code, 403)
        with self.assertRaises(HTTPError) as invalid:
            self.get('/api/hub/artifacts/' + artifact + '?version=-1')
        self.assertEqual(invalid.exception.code, 400)

    def test_cli_and_web_share_the_same_database_and_exact_output(self):
        room = self.client.post('/rooms', {'text': 'CLI 互通', 'agent_id': 'writer'})
        result = subprocess.run([sys.executable, '-X', 'utf8', '-m', 'studio.hub_cli', 'POST', '/tasks/claim', '--json', json.dumps({'worker_id': 'cli-test', 'room_id': room['id']})], cwd=ROOT, env=os.environ.copy(), capture_output=True, encoding='utf-8', timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        claim = json.loads(result.stdout)
        hub.complete_task(claim['task']['id'], 'cli-test', claim['lease_token'], {'message': '完整测试', 'artifacts': [{'title': '原文', 'kind': 'text', 'content': '保持\n字节\n'}]})
        artifact = hub.get_room(room['id'])['artifacts'][0]['id']
        output = Path(self.temp.name) / 'export.txt'
        with contextlib.redirect_stdout(io.StringIO()):
            code = hub_cli.main(['GET', f'/artifacts/{artifact}/download?version=1', '--out', str(output)])
        self.assertEqual(code, 0)
        self.assertEqual(output.read_bytes(), '保持\n字节\n'.encode())

    def fixture_config(self, code):
        script = Path(self.temp.name) / 'synthetic_executor.py'
        script.write_text(code, encoding='utf-8')
        return {'worker_id': 'fixture-executor', 'command': [sys.executable, '-X', 'utf8', str(script)], 'timeout_seconds': 15}

    def test_external_command_consumes_real_queue_and_persists_output(self):
        room = hub.create_room('桥接执行测试', agent_id='writer')
        config = self.fixture_config('import json,sys\npayload=json.load(sys.stdin)\nassert payload["protocol"]=="agent-workspace/1"\nprint(json.dumps({"message":"测试执行器已完成", "artifacts":[{"title":"测试执行器输出","kind":"text","content":payload["task"]["instructions"]}]}))\n')
        config['room_id'] = room['id']
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(worker.run_once(config, self.client, heartbeat_seconds=0.1))
        saved = hub.get_room(room['id'])
        self.assertEqual(saved['status'], 'completed')
        self.assertEqual(hub.get_artifact(saved['artifacts'][0]['id'])['content'], '桥接执行测试')
        self.assertFalse(worker.run_once(config, self.client))

    def test_cancel_stops_a_running_executor_and_rejects_output(self):
        room = hub.create_room('取消桥接执行', agent_id='developer')
        claim = hub.claim_task('fixture-executor', room_id=room['id'])
        config = self.fixture_config('import time\ntime.sleep(30)\nprint("{\\"message\\":\\"late\\"}")\n')
        seen = []
        def execute():
            try:
                worker.execute(config, claim, self.client, heartbeat_seconds=0.1, poll_seconds=0.02)
            except worker.APIError as exc:
                seen.append(exc.status)
        thread = threading.Thread(target=execute)
        thread.start()
        hub.cancel_run(room['runs'][0]['id'])
        thread.join(timeout=8)
        self.assertFalse(thread.is_alive(), 'Cancelled executor must terminate promptly on next heartbeat')
        self.assertEqual(seen, [409])
        saved = hub.get_room(room['id'])
        self.assertEqual(saved['status'], 'cancelled')
        self.assertEqual(saved['artifacts'], [])
