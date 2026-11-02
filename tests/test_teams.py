import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio import accounts, core, hub, hub_collaboration, hub_execution, hub_http, hub_teams


class TeamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, core.REQUEST_DATA_ROOT.set(None))
        self.addCleanup(core.REQUEST_ACTOR.reset, core.REQUEST_ACTOR.set(None))

    def team(self, members=None, mode='auto', key='create-team'):
        return hub_teams.create_team('实际研究团队', members or ['researcher', 'writer'], key, routing_mode=mode)

    def send(self, team, text='请完成这次请求', key='message', targets=None, agent='auto', **kwargs):
        return hub.add_message(team['primary_room_id'], text, agent, targets, key, **kwargs)

    def claim(self, team, worker='fixture-worker', agents=None):
        return hub.claim_task(worker, agents, team['primary_room_id'])

    def done(self, claim, result=None):
        return hub.complete_task(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], result or {'message': '真实测试任务完成'})

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, code, str(caught.exception))

    def plan(self, agent='writer'):
        return {'message': '依据成员集合分工', 'plan': [{'key': 'draft', 'agent_id': agent, 'title': '撰写结果', 'instructions': '按实际材料撰写', 'depends_on': []}]}

    def test_create_and_open_are_atomic_persistent_and_not_workflows(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            created = list(pool.map(lambda _: self.team(), range(6)))
        self.assertEqual(len({item['id'] for item in created}), 1)
        self.assertEqual(len({item['primary_room_id'] for item in created}), 1)
        team = created[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            rooms = list(pool.map(lambda _: hub_teams.open_room(team['id']), range(6)))
        self.assertEqual({room['id'] for room in rooms}, {team['primary_room_id']})
        self.assertTrue(all(room['status'] == 'idle' and not room['runs'] for room in rooms))
        data = hub.bootstrap()
        self.assertEqual(len(data['teams']), 1)
        self.assertEqual(len(data['rooms']), 1)
        self.assertEqual({item['id'] for item in data['templates']}, {'research', 'product', 'magazine'})
        self.assert_error(409, hub_teams.create_team, '不同名称', ['writer'], 'create-team')
        script = 'import json,sys;from studio import hub_teams;print(json.dumps(hub_teams.get_team(sys.argv[1])))'
        child = subprocess.run([sys.executable, '-c', script, team['id']], cwd=core.ROOT, env=dict(os.environ, PYTHONUTF8='1'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        restored = json.loads(child.stdout)
        self.assertEqual(restored['primary_room_id'], team['primary_room_id'])
        self.assertEqual(restored['member_ids'], team['member_ids'])

    def test_explicit_targets_and_idempotent_messages_are_enforced_on_server(self):
        team = self.team()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda _: self.send(team, targets=['writer', 'researcher']), range(6)))
        room = replies[-1]
        self.assertEqual(len(room['messages']), 1)
        self.assertEqual(len(room['runs']), 1)
        self.assertEqual({task['agent_id'] for task in room['runs'][0]['tasks']}, {'writer', 'researcher'})
        self.assertEqual(room['messages'][0]['routing']['mode'], 'direct')
        self.assertEqual(room['runs'][0]['team_version'], 1)
        self.assert_error(409, self.send, team, text='改写同一请求键', targets=['writer', 'researcher'])
        self.assert_error(409, self.send, team, key='outside', targets=['developer'])
        self.assert_error(409, self.send, team, key='coordinator', agent='coordinator')
        self.assert_error(400, self.send, team, key='double', targets=['writer', 'writer'])
        self.assert_error(400, self.send, team, key='both', targets=['writer'], agent='researcher')
        self.assert_error(400, self.send, team, key=None)
        self.assertEqual(len(hub.get_room(room['id'])['messages']), 1)

    def test_automatic_router_is_bounded_and_plan_cannot_escape_frozen_members(self):
        team = self.team(['writer'])
        self.send(team)
        claim = self.claim(team)
        self.assertEqual(claim['task']['agent_id'], 'coordinator')
        self.assertEqual([agent['id'] for agent in claim['context']['agents']], ['writer'])
        self.assertEqual(claim['context']['team']['allowed_agent_ids'], ['writer'])
        added = hub_teams.update_team(team['id'], 1, member_ids=['writer', 'developer'])
        self.assertEqual(added['cancelled_run_ids'], [])
        self.assert_error(409, self.done, claim, self.plan('developer'))
        self.assert_error(409, hub_execution.stage_result, claim['task']['id'], 'fixture-worker', claim['lease_token'], self.plan('developer'))
        self.assertEqual(len(hub.get_room(team['primary_room_id'])['runs'][0]['tasks']), 1)
        self.done(claim, self.plan())
        member = self.claim(team)
        self.assertEqual(member['task']['agent_id'], 'writer')
        self.assertEqual(member['context']['team']['version'], 2)
        self.assertEqual(member['context']['team']['run_version'], 1)
        self.assertEqual(member['context']['team']['allowed_agent_ids'], ['writer'])
        self.assert_error(400, self.done, member, self.plan('developer'))
        self.done(member)
        self.assertIsNone(self.claim(team)['task'])
        room = hub.get_room(team['primary_room_id'])
        self.assertEqual(len(room['runs']), 1)
        self.assertEqual(len(room['runs'][0]['tasks']), 2)

    def test_mention_mode_saves_notes_without_runs_and_switch_never_triggers_work(self):
        team = self.team(mode='mention')
        note = self.send(team, text='只有团队背景 @writer 也不是结构化点名', key='note')
        self.assertEqual(note['runs'], [])
        self.assertEqual(note['status'], 'idle')
        self.assertIsNone(note['messages'][0]['run_id'])
        self.assertEqual(note['messages'][0]['routing']['mode'], 'note')
        self.assertIsNone(self.claim(team)['task'])
        new = hub_teams.update_team(team['id'], 1, routing_mode='auto')
        self.assertIsNone(self.claim(team)['task'])
        self.send(team, key='auto')
        claim = self.claim(team)
        self.assertEqual(claim['task']['agent_id'], 'coordinator')
        hub_teams.update_team(team['id'], new['version'], routing_mode='mention')
        self.send(team, text='排队后的备注', key='later-note')
        with hub._transaction() as conn:
            task = hub._find(conn, 'hub_tasks', claim['task']['id'], '任务')
            run = hub._find(conn, 'hub_runs', task['run_id'], '运行')
            texts = [item['text'] for item in hub._claim_context(conn, task, run)['messages']]
        self.assertIn('只有团队背景 @writer 也不是结构化点名', texts)
        self.assertNotIn('排队后的备注', texts)
        self.done(claim)
        room = self.send(team, key='explicit', agent='writer')
        self.assertEqual(room['runs'][-1]['routing']['mode'], 'direct')
        self.assertEqual(self.claim(team)['task']['agent_id'], 'writer')

    def test_removal_cancels_entire_run_and_every_old_lease_write_or_read(self):
        team = self.team()
        room = self.send(team, targets=['writer', 'researcher'])
        claim = self.claim(team, agents=['writer'])
        task_id, worker, lease = claim['task']['id'], claim['task']['worker_id'], claim['lease_token']
        result = {'message': '待回传的真实结果', 'artifacts': [{'title': '草稿', 'kind': 'text', 'content': '尚未提交'}]}
        hub_execution.stage_result(task_id, worker, lease, result)
        changed = hub_teams.update_team(team['id'], 1, member_ids=['researcher'])
        self.assertEqual(changed['cancelled_run_ids'], [room['runs'][0]['id']])
        saved = hub.get_room(room['id'])
        self.assertEqual(saved['runs'][0]['cancel_reason'], 'team_members_changed')
        self.assertTrue(all(task['status'] == 'cancelled' for task in saved['runs'][0]['tasks']))
        self.assertEqual(saved['artifacts'], [])
        for fn, args in (
            (hub.heartbeat_task, (task_id, worker, lease)),
            (hub.fail_task, (task_id, worker, lease, 'late')),
            (hub.complete_task, (task_id, worker, lease, result)),
            (hub_execution.stage_result, (task_id, worker, lease, result)),
            (hub_execution.read_result, (task_id, worker, lease)),
            (hub_execution.append_event, (task_id, worker, lease, 'late', 'progress', {'message': 'too late'})),
            (hub.retry_run, (room['runs'][0]['id'],)),
        ):
            self.assert_error(409, fn, *args)
        hub_teams.update_team(team['id'], 2, member_ids=['writer', 'researcher'])
        self.assertIsNone(self.claim(team)['task'])
        self.assert_error(409, self.done, claim, result)
        receipts = hub_execution.list_events(claim['execution']['id'], limit=100)['events']
        self.assertEqual(receipts[-1]['payload']['reason'], 'team_members_changed')
        self.assertNotIn(lease, json.dumps(saved))

    def test_removal_preserves_completed_outputs_and_only_affects_snapshotted_runs(self):
        team = self.team(['writer'])
        self.send(team, key='finished', agent='writer')
        completed = self.done(self.claim(team), {'message': '已完成', 'artifacts': [{'title': '保留成果', 'kind': 'text', 'content': '原始内容'}]})
        self.send(team, key='unaffected', agent='writer')
        unaffected = self.claim(team)
        hub_teams.update_team(team['id'], 1, member_ids=['writer', 'researcher'])
        changed = hub_teams.update_team(team['id'], 2, member_ids=['writer'])
        self.assertEqual(changed['cancelled_run_ids'], [])
        self.done(unaffected)
        self.assertEqual(hub.get_artifact(completed['artifacts'][0]['id'])['content'], '原始内容')
        added = hub_teams.update_team(team['id'], 3, member_ids=['writer', 'researcher'])
        self.send(team, key='failed', agent='writer')
        failed = self.claim(team)
        hub.fail_task(failed['task']['id'], 'fixture-worker', failed['lease_token'], '可重试的测试故障')
        removed = hub_teams.update_team(team['id'], added['version'], member_ids=['writer'])
        self.assertEqual(removed['cancelled_run_ids'], [failed['task']['run_id']])
        self.assert_error(409, hub.retry_run, failed['task']['run_id'])
        self.assertEqual(hub.get_room(team['primary_room_id'])['runs'][0]['status'], 'completed')

    def test_version_conflicts_and_immutable_audit_record_actual_actor(self):
        team = self.team()
        barrier = threading.Barrier(2)
        def edit(name):
            barrier.wait()
            try:
                return hub_teams.update_team(team['id'], 1, name=name)['version']
            except hub.HubError as exc:
                return exc.status
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(edit, ['名称一', '名称二']))
        self.assertEqual(sorted(outcomes), [2, 409])
        actor = core.REQUEST_ACTOR.set({'user_id': 'local-auditor', 'user_name': '真实操作者', 'role': 'editor'})
        try:
            changed = hub_teams.update_team(team['id'], 2, member_ids=['writer'])
        finally:
            core.REQUEST_ACTOR.reset(actor)
        self.assertEqual(changed['version'], 3)
        first = hub_teams.list_history(team['id'], limit=1)
        self.assertTrue(first['has_more'])
        self.assertEqual(first['history'][0]['member_ids'], ['researcher', 'writer'])
        second = hub_teams.list_history(team['id'], first['cursor'], 100)
        self.assertEqual(second['history'][-1]['actor_name'], '真实操作者')
        self.assertEqual(second['history'][-1]['removed_member_ids'], ['researcher'])
        self.assertEqual(first['history'][0]['name'], '实际研究团队')
        self.assert_error(409, self.send, team, key='stale-version', base_team_version=1)
        self.assertEqual(hub_teams.update_team(team['id'], 3, member_ids=['writer'])['version'], 3)

    def test_completion_and_removal_serialize_without_resurrecting_members(self):
        team = self.team()
        self.send(team)
        claim = self.claim(team)
        barrier = threading.Barrier(2)
        def complete():
            barrier.wait()
            try:
                self.done(claim, self.plan('researcher'))
                return 200
            except hub.HubError as exc:
                return exc.status
        def remove():
            barrier.wait()
            return hub_teams.update_team(team['id'], 1, member_ids=['writer'])
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            future_done, future_remove = pool.submit(complete), pool.submit(remove)
            result, changed = future_done.result(), future_remove.result()
        self.assertIn(result, [200, 409])
        self.assertEqual(changed['version'], 2)
        self.assertIsNone(self.claim(team)['task'])
        room = hub.get_room(team['primary_room_id'])
        self.assertEqual(room['runs'][0]['status'], 'cancelled')
        self.assertTrue(all(item['status'] in ('cancelled', 'completed') for item in room['runs'][0]['tasks']))

    def test_archive_stops_work_but_preserves_artifact_and_discussion_history(self):
        team = self.team()
        self.send(team, agent='writer')
        room = self.done(self.claim(team), {'message': '成果已创建', 'artifacts': [{'title': '原件', 'kind': 'text', 'content': '保存内容'}]})
        artifact_id = room['artifacts'][0]['id']
        comment = hub_collaboration.create_comment(artifact_id, 1, '保存讨论', 'comment')
        self.send(team, key='next')
        claim = self.claim(team)
        archived = hub_teams.archive_team(team['id'], 1)
        self.assertTrue(archived['archived_at'])
        self.assertEqual(archived['cancelled_run_ids'], [claim['task']['run_id']])
        self.assertEqual(hub_teams.open_room(team['id'])['runs'][-1]['cancel_reason'], 'team_archived')
        self.assertEqual(hub.get_artifact(artifact_id)['content'], '保存内容')
        self.assertEqual(hub_collaboration.get_comment(comment['id'])['body'], '保存讨论')
        self.assert_error(409, self.send, team, key='after-archive')
        self.assert_error(409, hub_teams.update_team, team['id'], 2, name='不能恢复')
        self.assert_error(409, self.done, claim)
        self.assertEqual(hub_teams.get_team(team['id'])['active_run_ids'], [])

    def test_registration_does_not_expand_team_membership_and_workflows_remain_separate(self):
        team = self.team(['writer'])
        self.send(team, agent='writer')
        registration = hub.register_worker('broad-worker', ['writer', 'developer'])
        empty = hub.claim_task('broad-worker', ['developer'], team['primary_room_id'], registration['session_token'])
        self.assertIsNone(empty['task'])
        selected = hub.claim_task('broad-worker', ['writer'], team['primary_room_id'], registration['session_token'])
        self.assertEqual(selected['task']['agent_id'], 'writer')
        self.done(selected)
        before = hub.get_room(team['primary_room_id'])
        with self.assertRaises(hub.HubError) as caught:
            with hub._transaction() as conn:
                hub._new_run(conn, team['primary_room_id'], '不能绕过成员约束', 'auto', 'product')
        self.assertEqual(caught.exception.status, 409)
        self.assertEqual(len(hub.get_room(team['primary_room_id'])['runs']), len(before['runs']))
        ordinary = hub.create_room('独立固定工作流', template_id='product')
        self.assertIsNone(ordinary['team'])
        self.assertIsNone(ordinary['runs'][0]['team_id'])
        self.assertEqual(ordinary['runs'][0]['template_id'], 'product')

    def test_modification_cannot_use_removed_agent_or_survive_roster_removal(self):
        team = self.team()
        self.send(team, agent='writer')
        room = self.done(self.claim(team), {'message': '草稿', 'artifacts': [{'title': '可修改', 'kind': 'text', 'content': '原文'}]})
        artifact_id = room['artifacts'][0]['id']
        comment = hub_collaboration.create_comment(artifact_id, 1, '修订', 'comment')
        self.assert_error(409, hub_collaboration.create_modification, artifact_id, 1, [comment['id']], 'outsider-mod', agent_id='developer')
        self.assert_error(409, hub_collaboration.create_modification, artifact_id, 1, [comment['id']], 'coordinator-bypass', agent_id='coordinator')
        request = hub_collaboration.create_modification(artifact_id, 1, [comment['id']], 'valid-mod')
        claim = self.claim(team)
        self.assertEqual(claim['context']['modification']['request_id'], request['id'])
        hub_teams.update_team(team['id'], 1, member_ids=['researcher'])
        self.assert_error(409, self.done, claim, {'message': '已修改', 'artifacts': [{'id': artifact_id, 'base_version': 1, 'title': '可修改', 'kind': 'text', 'content': '修改后'}]})
        self.assertEqual(hub.get_artifact(artifact_id)['version'], 1)
        self.assertEqual(hub_collaboration.get_modification(request['id'])['status'], 'cancelled')
        self.assert_error(409, hub_collaboration.create_modification, artifact_id, 1, [comment['id']], 'removed-author')

    def test_workspace_isolation_live_member_revocation_and_token_write_scope(self):
        with accounts.transaction() as conn:
            conn.execute('INSERT INTO users VALUES (?,?,?,?,?)', ('editor', 'editor@example.test', 'Editor', 'fixture-no-password', hub.now()))
            conn.execute('INSERT INTO workspaces VALUES (?,?,?,?)', ('workspace', 'Fixture', '', hub.now()))
            conn.execute('INSERT INTO members VALUES (?,?,?)', ('workspace', 'editor', 'editor'))
        actor = core.REQUEST_ACTOR.set({'user_id': 'editor', 'workspace_id': 'workspace', 'user_name': 'Editor', 'role': 'editor'})
        try:
            team = self.team()
            self.send(team, agent='writer')
            with accounts.transaction() as conn:
                conn.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?', ('workspace', 'editor'))
            self.assert_error(403, hub_teams.update_team, team['id'], 1, name='已撤权')
            self.assert_error(403, self.send, team, key='revoked')
        finally:
            core.REQUEST_ACTOR.reset(actor)
        for denied in ({'role': 'viewer'}, {'role': 'editor', 'scopes': ['read', 'execute']}):
            token = core.REQUEST_ACTOR.set(denied)
            try:
                self.assertEqual(hub_teams.get_team(team['id'])['id'], team['id'])
                self.assert_error(403, hub_teams.archive_team, team['id'], 1)
                self.assert_error(403, self.send, team, key='not-writer')
            finally:
                core.REQUEST_ACTOR.reset(token)
        workspace = core.REQUEST_DATA_ROOT.set(Path(self.temp.name) / 'workspace-b')
        try:
            self.assertEqual(hub_teams.list_teams()['teams'], [])
            self.assert_error(404, hub_teams.get_team, team['id'])
            self.assert_error(404, hub_teams.list_history, team['id'])
            self.assert_error(404, hub_teams.update_team, team['id'], 1, name='跨空间')
            self.assert_error(404, hub_teams.open_room, team['id'])
            self.assert_error(404, self.send, team, key='cross-space')
            self.assert_error(404, self.claim, team)
        finally:
            core.REQUEST_DATA_ROOT.reset(workspace)

    def test_explicit_modification_can_return_to_internal_coordinator_source(self):
        team = self.team()
        self.send(team)
        room = self.done(self.claim(team), {'message': '直接完成的草稿', 'artifacts': [{'title': '统筹稿件', 'kind': 'text', 'content': '原始版本'}]})
        artifact_id = room['artifacts'][0]['id']
        comment = hub_collaboration.create_comment(artifact_id, 1, '精简内容', 'coordinator-comment')
        request = hub_collaboration.create_modification(artifact_id, 1, [comment['id']], 'coordinator-modification')
        claim = self.claim(team)
        self.assertEqual(claim['task']['agent_id'], 'coordinator')
        self.assertEqual(claim['context']['modification']['request_id'], request['id'])
        self.assertEqual({agent['id'] for agent in claim['context']['agents']}, {'writer', 'researcher'})
        self.done(claim, {'message': '按讨论精简完成', 'artifacts': [{'id': artifact_id, 'base_version': 1, 'title': '统筹稿件', 'kind': 'text', 'content': '精简版本'}]})
        self.assertEqual(hub.get_artifact(artifact_id)['version'], 2)
        self.assert_error(409, self.send, team, key='still-not-member', agent='coordinator')

    def test_http_contract_validates_fields_and_exposes_actual_routing(self):
        status, team, _, _ = hub_http.dispatch('POST', '/api/hub/teams', {'name': 'API 团队', 'member_ids': ['writer'], 'request_key': 'api-create', 'routing_mode': 'mention'})
        self.assertEqual(status, 201)
        status, room, _, _ = hub_http.dispatch('POST', f'/api/hub/teams/{team["id"]}/rooms', {'text': '先保存', 'request_key': 'api-note'})
        self.assertEqual(status, 200)
        self.assertEqual(room['messages'][-1]['routing']['mode'], 'note')
        status, direct, _, _ = hub_http.dispatch('POST', f'/api/hub/rooms/{room["id"]}/messages', {'text': '真实排队', 'request_key': 'api-direct', 'target_agent_ids': ['writer']})
        self.assertEqual(status, 200)
        self.assertEqual(direct['runs'][-1]['tasks'][0]['agent_id'], 'writer')
        status, history, _, _ = hub_http.dispatch('GET', f'/api/hub/teams/{team["id"]}/history', query={'after': ['0'], 'limit': ['1']})
        self.assertEqual(status, 200)
        self.assertEqual(history['cursor'], 1)
        for body in ({'base_version': 1, 'proactive_enabled': True}, {'base_version': 1, 'member_ids': []}, {'base_version': 1, 'routing_mode': True}):
            self.assertEqual(hub_http.dispatch('PUT', f'/api/hub/teams/{team["id"]}', body)[0], 400)
        self.assertEqual(hub_http.dispatch('DELETE', f'/api/hub/teams/{team["id"]}', {'base_version': 0})[0], 409)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/teams/{team["id"]}/history', query={'after': ['0', '1']})[0], 400)


if __name__ == '__main__':
    unittest.main()
