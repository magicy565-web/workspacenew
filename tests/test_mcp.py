import asyncio
import importlib.util
import base64
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(importlib.util.find_spec('mcp'), 'optional MCP SDK not installed')
class MCPTests(unittest.TestCase):
    def test_local_team_workflow_migration_and_message_schema_are_real_stdio(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        async def check():
            with tempfile.TemporaryDirectory() as data:
                params = StdioServerParameters(command=sys.executable, args=['-m', 'studio.mcp_server'], env=dict(os.environ, STUDIO_DATA_DIR=data, PYTHONPATH=str(ROOT), PYTHONUTF8='1'), cwd=str(ROOT))
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        async def call(name, args):
                            result = await session.call_tool(name, args)
                            self.assertFalse(result.isError, str(result))
                            return result.structuredContent or json.loads(result.content[0].text)
                        catalog = {tool.name: tool for tool in (await session.list_tools()).tools}
                        self.assertTrue({'workspace_create_team', 'workspace_update_team', 'workspace_create_workflow', 'workspace_update_workflow'} <= catalog.keys())
                        self.assertIn('host_identity', catalog['workspace_claim_task'].inputSchema['properties'])
                        self.assertIn('request_key', catalog['workspace_add_message'].inputSchema['properties'])
                        step = {'key': 'draft', 'agent_id': 'writer', 'title': '实际步骤', 'instructions': '执行测试步骤', 'depends_on': []}
                        old = await session.call_tool('workspace_create_team', {'name': '旧错误叫法', 'description': '', 'steps': [step]})
                        self.assertTrue(old.isError)
                        self.assertIn('workspace_create_workflow', old.content[0].text)
                        workflow = await call('workspace_create_workflow', {'name': '独立工作流', 'description': '', 'steps': [step]})
                        old_update = await session.call_tool('workspace_update_team', {'template_id': workflow['id'], 'base_version': 1, 'name': '旧更新', 'description': '', 'steps': [step]})
                        self.assertTrue(old_update.isError)
                        self.assertIn('workspace_update_workflow', old_update.content[0].text)
                        updated = await call('workspace_update_workflow', {'template_id': workflow['id'], 'base_version': 1, 'name': '新版工作流', 'description': '仍是DAG', 'steps': [step]})
                        self.assertEqual(updated['version'], 2)
                        team = await call('workspace_create_team', {'name': '持续群聊', 'member_ids': ['writer'], 'request_key': 'local-team', 'routing_mode': 'mention'})
                        note = await call('workspace_add_message', {'room_id': team['primary_room_id'], 'text': '先保存背景', 'request_key': 'local-note'})
                        self.assertEqual(note['runs'], [])
                        self.assertIsNone(note['messages'][0]['run_id'])
                        changed = await call('workspace_update_team', {'team_id': team['id'], 'base_version': 1, 'name': '更名后的群聊'})
                        self.assertEqual(changed['primary_room_id'], team['primary_room_id'])
                        direct = await call('workspace_add_message', {'room_id': team['primary_room_id'], 'text': '请写内容', 'request_key': 'local-direct', 'target_agent_ids': ['writer'], 'base_team_version': 2})
                        self.assertEqual(direct['runs'][0]['tasks'][0]['agent_id'], 'writer')
                        claim = await call('workspace_claim_task', {'worker_id': 'local-mcp-host', 'room_id': team['primary_room_id'], 'capabilities': ['continuation:fixture'], 'host_identity': 'local-fixture-host'})
                        self.assertEqual(claim['execution']['recovery']['decision'], 'start')
                        failed = await session.call_tool('workspace_write', {'method': 'PUT', 'path': f'/teams/{team["id"]}', 'body': {'base_version': 1, 'name': '陈旧版本'}})
                        self.assertTrue(failed.isError, 'HTTP validation errors must also be MCP errors locally')
                        with contextlib.closing(sqlite3.connect(Path(data) / 'studio.sqlite3')) as conn:
                            self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_teams').fetchone()[0], 1)
                            self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_rooms').fetchone()[0], 1)
                            attempt = conn.execute('SELECT host_identity,capabilities_json FROM hub_execution_attempts').fetchone()
                            self.assertEqual(attempt[0], 'local-fixture-host')
                            self.assertEqual(json.loads(attempt[1]), ['continuation:fixture'])
        asyncio.run(check())

    def test_remote_team_stdio_http_sqlite_member_fencing_and_recovery(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from studio import accounts
        from studio.server import Handler, WorkspaceServer

        async def check():
            with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'STUDIO_DATA_DIR': data, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''}):
                _, headers = accounts.setup({'email': 'team-owner@example.test', 'name': '团队协议验收', 'password': 'teams-fixture-password-7328'})
                principal = accounts.identify({'Cookie': headers['Set-Cookie'].split(';', 1)[0]})
                token = accounts.dispatch('POST', '/api/account/tokens', {'name': '团队协议调用方', 'scopes': ['read', 'write', 'execute']}, principal)[1]
                server = WorkspaceServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    params = StdioServerParameters(command=sys.executable, args=['-m', 'studio.remote_mcp'], env=dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8='1', STUDIO_SERVER=f'http://127.0.0.1:{server.server_port}', STUDIO_WORKER_TOKEN=token['token']), cwd=str(ROOT))
                    async with stdio_client(params) as (read, write):
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            async def call(name, args):
                                result = await session.call_tool(name, args)
                                self.assertFalse(result.isError, str(result))
                                return result.structuredContent or json.loads(result.content[0].text)
                            async def write_api(path, body):
                                return await call('workspace_write', {'method': 'POST', 'path': path, 'body': body})
                            names = {tool.name for tool in (await session.list_tools()).tools}
                            self.assertTrue({'workspace_create_team', 'workspace_update_team', 'workspace_create_workflow', 'workspace_update_workflow', 'workspace_add_message'} <= names)
                            step = {'key': 'draft', 'agent_id': 'writer', 'title': '确定步骤', 'instructions': '执行已保存步骤', 'depends_on': []}
                            legacy = await session.call_tool('workspace_create_team', {'name': '不是团队', 'steps': [step]})
                            self.assertTrue(legacy.isError)
                            self.assertIn('workspace_create_workflow', legacy.content[0].text)
                            workflow = await call('workspace_create_workflow', {'name': '远程工作流', 'description': '', 'steps': [step]})
                            workflow = await call('workspace_update_workflow', {'template_id': workflow['id'], 'base_version': 1, 'name': '远程工作流2', 'description': '', 'steps': [step]})
                            self.assertEqual(workflow['version'], 2)
                            team_args = {'name': '远程持续团队', 'member_ids': ['writer', 'researcher'], 'request_key': 'remote-team', 'routing_mode': 'mention'}
                            team = await call('workspace_create_team', team_args)
                            same = await call('workspace_create_team', team_args)
                            self.assertEqual(team['primary_room_id'], same['primary_room_id'])
                            room_id = team['primary_room_id']
                            note_args = {'room_id': room_id, 'text': '待执行的团队背景', 'request_key': 'remote-note'}
                            await call('workspace_add_message', note_args)
                            note = await call('workspace_add_message', note_args)
                            self.assertEqual(len(note['messages']), 1)
                            self.assertEqual(note['runs'], [])
                            await call('workspace_add_message', {'room_id': room_id, 'text': '由成员执行', 'request_key': 'remote-direct', 'target_agent_ids': ['writer']})
                            claim_args = {'worker_id': 'remote-team-host', 'room_id': room_id, 'capabilities': ['continuation:fixture'], 'host_identity': 'remote-fixture-host'}
                            direct = await call('workspace_claim_task', claim_args)
                            self.assertEqual(direct['task']['agent_id'], 'writer')
                            owner = {'worker_id': 'remote-team-host', 'lease_token': direct['lease_token']}
                            saved = await write_api(f'/tasks/{direct["task"]["id"]}/complete', dict(owner, result={'message': '实际协议回传', 'artifacts': [{'title': '远程成果', 'kind': 'text', 'content': 'Protocol fixture, not a model response.'}]}))
                            artifact_id = saved['artifacts'][0]['id']
                            team = await call('workspace_update_team', {'team_id': team['id'], 'base_version': 1, 'routing_mode': 'auto'})
                            await call('workspace_add_message', {'room_id': room_id, 'text': '下一轮按成员分工', 'request_key': 'remote-auto', 'base_team_version': 2})
                            claim = await call('workspace_claim_task', claim_args)
                            self.assertEqual(claim['task']['agent_id'], 'coordinator')
                            task_id = claim['task']['id']
                            owner = {'worker_id': 'remote-team-host', 'lease_token': claim['lease_token']}
                            host = {'host': 'fixture', 'session_id': 'remote-host-session', 'host_identity': 'remote-fixture-host'}
                            observed = await write_api(f'/tasks/{task_id}/execution/events', dict(owner, event_id='remote-session', kind='host_session', payload=host))
                            await write_api(f'/tasks/{task_id}/execution/events', dict(owner, event_id='remote-checkpoint', kind='checkpoint', payload=dict(host, checkpoint_id='remote-checkpoint-1', through_seq=observed['event']['seq'])))
                            with contextlib.closing(sqlite3.connect(Path(data) / 'studio.sqlite3')) as conn:
                                conn.execute('UPDATE hub_tasks SET lease_expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', task_id))
                                conn.commit()
                            recovered = await call('workspace_claim_task', claim_args)
                            self.assertEqual(recovered['execution']['recovery']['decision'], 'continue')
                            self.assertEqual(recovered['execution']['recovery']['checkpoint']['session_id'], 'remote-host-session')
                            owner = {'worker_id': 'remote-team-host', 'lease_token': recovered['lease_token']}
                            team = await call('workspace_update_team', {'team_id': team['id'], 'base_version': 2, 'member_ids': ['writer', 'researcher', 'developer']})
                            outside_result = {'message': '不得扩展旧授权', 'plan': [dict(step, agent_id='developer')]}
                            rejected = await session.call_tool('workspace_write', {'method': 'POST', 'path': f'/tasks/{task_id}/complete', 'body': dict(owner, result=outside_result)})
                            self.assertTrue(rejected.isError)
                            removed = await call('workspace_update_team', {'team_id': team['id'], 'base_version': 3, 'member_ids': ['writer', 'developer']})
                            self.assertEqual(removed['cancelled_run_ids'], [claim['task']['run_id']])
                            late = await session.call_tool('workspace_write', {'method': 'POST', 'path': f'/tasks/{task_id}/complete', 'body': dict(owner, result={'message': '过时回传'})})
                            self.assertTrue(late.isError)
                            continued = await call('workspace_read', {'path': f'/rooms/{room_id}'})
                            self.assertEqual(continued['team']['primary_room_id'], room_id)
                            self.assertEqual(continued['runs'][-1]['cancel_reason'], 'team_members_changed')
                            self.assertEqual(continued['artifacts'][0]['id'], artifact_id)
                            with contextlib.closing(sqlite3.connect(Path(data) / 'studio.sqlite3')) as conn:
                                conn.row_factory = sqlite3.Row
                                self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_teams').fetchone()[0], 1)
                                self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_rooms').fetchone()[0], 1)
                                row = conn.execute('SELECT * FROM hub_runs WHERE id=?', (claim['task']['run_id'],)).fetchone()
                                self.assertEqual(json.loads(row['team_members_json']), ['researcher', 'writer'])
                                self.assertEqual((row['team_version'], row['cancelled']), (2, 1))
                                self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_tasks WHERE run_id=?', (row['id'],)).fetchone()[0], 1)
                                self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_team_revisions').fetchone()[0], 4)
                                self.assertEqual(conn.execute('SELECT COUNT(*) FROM hub_artifact_versions WHERE artifact_id=?', (artifact_id,)).fetchone()[0], 1)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
        asyncio.run(check())

    def test_remote_stdio_uses_real_http_permissions_and_immediate_revocation(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        from studio import accounts
        from studio.server import Handler, WorkspaceServer

        async def check():
            with tempfile.TemporaryDirectory() as data, patch.dict(os.environ, {'STUDIO_DATA_DIR': data, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''}):
                _, headers = accounts.setup({'email': 'remote-owner@example.test', 'name': '远程验收', 'password': 'remote-fixture-password-7328'})
                principal = accounts.identify({'Cookie': headers['Set-Cookie'].split(';', 1)[0]})
                read_only = accounts.dispatch('POST', '/api/account/tokens', {'name': '只读宿主', 'scopes': ['read']}, principal)[1]
                executor = accounts.dispatch('POST', '/api/account/tokens', {'name': '执行宿主', 'scopes': ['read', 'write', 'execute']}, principal)[1]
                server = WorkspaceServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                try:
                    for credential, allowed in ((read_only, False), (executor, True)):
                        env = dict(os.environ, PYTHONPATH=str(ROOT), PYTHONUTF8='1', STUDIO_SERVER=f'http://127.0.0.1:{server.server_port}', STUDIO_WORKER_TOKEN=credential['token'])
                        params = StdioServerParameters(command=sys.executable, args=['-m', 'studio.remote_mcp'], env=env, cwd=str(ROOT))
                        async with stdio_client(params) as (read, write):
                            async with ClientSession(read, write) as session:
                                await session.initialize()
                                overview = await session.call_tool('workspace_read', {'path': '/bootstrap'})
                                self.assertFalse(overview.isError)
                                created = await session.call_tool('workspace_write', {'method': 'POST', 'path': '/rooms', 'body': {'text': '真实远程 MCP 协议验收', 'agent_id': 'reviewer'}})
                                if not allowed:
                                    self.assertTrue(created.isError)
                                    forbidden_team = await session.call_tool('workspace_create_team', {'name': '只读不能建群', 'member_ids': ['writer'], 'request_key': 'read-only-team'})
                                    self.assertTrue(forbidden_team.isError)
                                    continue
                                self.assertFalse(created.isError, str(created))
                                room = created.structuredContent or json.loads(created.content[0].text)
                                claimed = await session.call_tool('workspace_claim_task', {'worker_id': 'remote-mcp-fixture', 'room_id': room['id']})
                                self.assertFalse(claimed.isError)
                                claim = claimed.structuredContent or json.loads(claimed.content[0].text)
                                completed = await session.call_tool('workspace_write', {'method': 'POST', 'path': f'/tasks/{claim["task"]["id"]}/complete', 'body': {'worker_id': 'remote-mcp-fixture', 'lease_token': claim['lease_token'], 'result': {'message': '协议测试实际回传', 'artifacts': [{'title': '远程协议成果', 'kind': 'text', 'content': 'fixture only; not a model response'}]}}})
                                self.assertFalse(completed.isError, str(completed))
                                saved = completed.structuredContent or json.loads(completed.content[0].text)
                                self.assertEqual(saved['status'], 'completed')
                                accounts.dispatch('DELETE', f'/api/account/tokens/{credential["record"]["id"]}', {}, principal)
                                revoked = await session.call_tool('workspace_read', {'path': '/bootstrap'})
                                self.assertTrue(revoked.isError)
                finally:
                    server.shutdown()
                    server.server_close()
                    thread.join()
        asyncio.run(check())

    def test_workspace_stdio_claim_plan_artifact_and_cancel(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def check():
            with tempfile.TemporaryDirectory() as data:
                params = StdioServerParameters(command=sys.executable, args=['-m', 'studio.mcp_server'], env=dict(os.environ, STUDIO_DATA_DIR=data, PYTHONPATH=str(ROOT), PYTHONUTF8='1'), cwd=str(ROOT))
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        async def call(name, args):
                            result = await session.call_tool(name, args)
                            self.assertFalse(result.isError, str(result))
                            return result.structuredContent or json.loads(result.content[0].text)
                        overview = await call('workspace_overview', {})
                        self.assertEqual(overview['connection']['active_workers'], 0)
                        skill = await call('workspace_write', {'method': 'POST', 'path': '/skills', 'body': {'name': 'MCP 版本指南', 'description': '', 'content': '只回传已实际执行的结果。'}})
                        pinned = await call('workspace_read', {'path': f'/skills/{skill["id"]}?version=1'})
                        self.assertEqual(pinned['content'], '只回传已实际执行的结果。')
                        forbidden = await session.call_tool('workspace_read', {'path': '/../../api/account/session'})
                        self.assertTrue(forbidden.isError)
                        room = await call('workspace_create_room', {'text': 'MCP 工作区测试'})
                        claim = await call('workspace_claim_task', {'worker_id': 'mcp-test', 'room_id': room['id']})
                        owner = {'task_id': claim['task']['id'], 'worker_id': 'mcp-test', 'lease_token': claim['lease_token']}
                        await call('workspace_complete_task', dict(owner, result={'message': '计划已保存', 'plan': [{'key': 'write', 'agent_id': 'writer', 'title': '编写成果', 'instructions': '输出测试成果', 'depends_on': []}]}))
                        claim = await call('workspace_claim_task', {'worker_id': 'mcp-test', 'room_id': room['id']})
                        owner = {'task_id': claim['task']['id'], 'worker_id': 'mcp-test', 'lease_token': claim['lease_token']}
                        await call('workspace_heartbeat', dict(owner, progress='测试协议'))
                        saved = await call('workspace_complete_task', dict(owner, result={'message': '测试完成', 'artifacts': [{'title': 'MCP 成果', 'kind': 'text', 'content': '实际协议回传'}]}))
                        artifact = await call('workspace_get_artifact', {'artifact_id': saved['artifacts'][0]['id'], 'version': 1})
                        self.assertEqual(artifact['content'], '实际协议回传')
                        changed = await call('workspace_add_message', {'room_id': room['id'], 'text': '继续修改'})
                        cancelled = await call('workspace_cancel_run', {'run_id': changed['runs'][-1]['id']})
                        self.assertEqual(cancelled['runs'][-1]['status'], 'cancelled')
        asyncio.run(check())

    def test_real_stdio_client_lists_tools_and_creates_project(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        async def check():
            with tempfile.TemporaryDirectory() as data:
                env = dict(os.environ, STUDIO_DATA_DIR=data, PYTHONPATH=str(ROOT), PYTHONUTF8='1')
                params = StdioServerParameters(command=sys.executable, args=['-m', 'studio.mcp_server'], env=env, cwd=str(ROOT))
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        tools = await session.list_tools()
                        names = {tool.name for tool in tools.tools}
                        self.assertTrue({'create_project', 'add_instruction', 'save_editorial_brief', 'upload_image', 'build_magazine'} <= names)
                        result = await session.call_tool('create_project', {'name':'Agent project', 'website':'https://example.com'})
                        self.assertFalse(result.isError)
                        project = result.structuredContent or json.loads(result.content[0].text)
                        self.assertEqual(project['name'], 'Agent project')
                        self.assertEqual(project['website'], 'https://example.com')
                        image = base64.b64encode((ROOT / 'examples' / 'example.png').read_bytes()).decode('ascii')
                        brief = json.loads((ROOT / 'examples' / 'example-brief.json').read_text(encoding='utf-8'))
                        for tool, arguments in [
                            ('add_instruction', {'project_id':project['id'], 'text':'Focus on verified materials.'}),
                            ('upload_image', {'project_id':project['id'], 'filename':'example.png', 'data_base64':image}),
                            ('save_editorial_brief', {'project_id':project['id'], 'brief':brief}),
                            ('build_magazine', {'project_id':project['id']})
                        ]:
                            outcome = await session.call_tool(tool, arguments)
                            self.assertFalse(outcome.isError, str(outcome))
                        built = outcome.structuredContent or json.loads(outcome.content[0].text)
                        self.assertEqual(built['checks']['chapters'], 4)
        asyncio.run(check())
