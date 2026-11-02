import contextlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from studio import core, hub, hub_execution, hub_teams


class RoomSummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, core.REQUEST_DATA_ROOT.set(None))
        self.addCleanup(core.REQUEST_ACTOR.reset, core.REQUEST_ACTOR.set(None))

    def summary(self, room_id):
        return next(room for room in hub.bootstrap()['rooms'] if room['id'] == room_id)

    @contextlib.contextmanager
    def actor(self, user_id, name):
        token = core.REQUEST_ACTOR.set({'user_id': user_id, 'user_name': name, 'role': 'editor'})
        try:
            yield
        finally:
            core.REQUEST_ACTOR.reset(token)

    def test_latest_human_and_agent_identity_and_message_time_ignore_progress(self):
        with self.actor('alice', '林晓'):
            room = hub.create_room('请核对这份给定内容', agent_id='writer')
        first = self.summary(room['id'])
        self.assertEqual(first['last_speaker'], {'role': 'user', 'agent_id': None, 'author_id': 'alice', 'name': '林晓'})
        self.assertEqual(first['last_message_at'], room['messages'][-1]['created_at'])
        self.assertEqual(first['team_members_preview'], [])
        self.assertEqual(first['team_member_count'], 0)
        claim = hub.claim_task('summary-worker', room_id=room['id'])
        hub.heartbeat_task(claim['task']['id'], 'summary-worker', claim['lease_token'], '进度不是消息正文')
        progress = self.summary(room['id'])
        self.assertEqual(progress['last_message_at'], first['last_message_at'])
        self.assertEqual(progress['last_message'], first['last_message'])
        self.assertNotEqual(progress['updated_at'], first['last_message_at'])
        hub.add_source(room['id'], '附件资料', 'text', '资料内容也不是最后发言')
        self.assertEqual(self.summary(room['id'])['last_message_at'], first['last_message_at'])
        completed = hub.complete_task(claim['task']['id'], 'summary-worker', claim['lease_token'], {'message': '已核对给定内容。'})
        agent = self.summary(room['id'])
        self.assertEqual(agent['last_message'], '已核对给定内容。')
        self.assertEqual(agent['last_speaker'], {'role': 'agent', 'agent_id': 'writer', 'author_id': None, 'name': '内容编辑'})
        self.assertEqual(agent['last_message_at'], completed['messages'][-1]['created_at'])
        hub.update_agent('writer', '交付编辑', '现在的真实名称', '沿既有授权执行')
        renamed = self.summary(room['id'])
        self.assertEqual(renamed['last_speaker']['name'], '交付编辑')
        self.assertEqual(renamed['last_message_at'], agent['last_message_at'])
        with self.actor('bob', '周清'):
            followup = hub.add_message(room['id'], '下一条实际需求', agent_id='writer')
        summary = self.summary(room['id'])
        self.assertEqual(summary['last_speaker'], {'role': 'user', 'agent_id': None, 'author_id': 'bob', 'name': '周清'})
        self.assertEqual(summary['last_message_at'], followup['messages'][-1]['created_at'])
        detail = hub.get_room(room['id'])
        for key in ('last_message', 'last_message_at', 'last_speaker', 'team_members_preview', 'team_member_count', 'status'):
            self.assertEqual(summary[key], detail[key])

    def test_empty_team_has_no_invented_message_and_member_preview_is_current_bounded(self):
        custom = [hub.create_agent(f'成员 {index}', '', '测试角色') for index in range(3)]
        members = ['writer', 'reviewer', 'researcher'] + [item['id'] for item in custom]
        team = hub_teams.create_team('新建的真实团队', members, 'summary-team')
        empty = self.summary(team['primary_room_id'])
        self.assertEqual((empty['last_message'], empty['last_message_at'], empty['last_speaker'], empty['status']), ('', None, None, 'idle'))
        self.assertEqual(empty['team_member_count'], 6)
        self.assertEqual(empty['team_members_preview'], [{'agent_id': item['agent_id'], 'name': item['name']} for item in team['members'][:4]])
        self.assertEqual(hub.get_room(team['primary_room_id'])['messages'], [])
        changed = hub_teams.update_team(team['id'], 1, member_ids=['writer', 'reviewer'], name='新名称')
        current = self.summary(team['primary_room_id'])
        self.assertEqual(current['team_member_count'], 2)
        self.assertEqual({item['agent_id'] for item in current['team_members_preview']}, {'writer', 'reviewer'})
        self.assertIsNone(current['last_message_at'])
        self.assertIsNone(current['last_speaker'])
        hub_teams.archive_team(team['id'], changed['version'])
        archived = self.summary(team['primary_room_id'])
        self.assertEqual(archived['team_members_preview'], current['team_members_preview'])
        self.assertIsNone(archived['last_message_at'])

    def test_batch_statuses_match_existing_detail_semantics_including_lease_expiry(self):
        live, expired = '2031-01-01T00:00:00+00:00', '2000-01-01T00:00:00+00:00'
        cases = [
            ('idle', [], 'idle'),
            ('queued', [(False, [('queued', None)])], 'queued'),
            ('running', [(False, [('running', live)])], 'running'),
            ('expired', [(False, [('running', expired)])], 'queued'),
            ('null-lease', [(False, [('running', None)])], 'queued'),
            ('failed', [(False, [('failed', None)])], 'failed'),
            ('completed', [(False, [('completed', None), ('completed', None)])], 'completed'),
            ('cancelled', [(True, [('running', live)])], 'cancelled'),
            ('live-before-failure', [(False, [('failed', None), ('running', live)])], 'running'),
            ('expired-and-failed', [(False, [('failed', None), ('running', expired)])], 'failed'),
            ('old-queued-new-failed', [(False, [('queued', None)]), (False, [('failed', None)])], 'queued'),
            ('first-active-wins', [(False, [('queued', None)]), (False, [('running', live)])], 'queued'),
            ('latest-terminal', [(False, [('failed', None)]), (False, [('completed', None)])], 'completed'),
            ('empty-run', [(False, [])], 'queued'),
        ]
        expected = {}
        with hub._transaction() as conn:
            for title, runs, wanted in cases:
                room_id, stamp = uuid4().hex, '2030-01-01T00:00:00+00:00'
                conn.execute('INSERT INTO hub_rooms (id,title,created_at,updated_at) VALUES (?,?,?,?)', (room_id, title, stamp, stamp))
                expected[room_id] = wanted
                for cancelled, states in runs:
                    run_id = uuid4().hex
                    conn.execute('INSERT INTO hub_runs (id,room_id,created_at,cancelled) VALUES (?,?,?,?)', (run_id, room_id, stamp, int(cancelled)))
                    for state, lease in states:
                        task_id = hub._new_task(conn, room_id, run_id, 'writer', 'Isolated status fixture', 'No model result is invented.', [])
                        conn.execute('UPDATE hub_tasks SET status=?,lease_expires_at=? WHERE id=?', (state, lease, task_id))
            with patch.object(hub, 'now', return_value='2030-01-01T00:00:00+00:00'):
                summaries = hub._room_summaries(conn)
                self.assertEqual({item['id']: item['status'] for item in summaries}, expected)
                for item in summaries:
                    self.assertEqual(item['status'], hub._room(conn, item['id'])['status'])

    def test_preview_preserves_unicode_and_never_reads_private_execution_or_sources(self):
        text = '公开消息\x00' + '🙂中' * 120
        room = hub.create_room(text, agent_id='writer')
        registration = hub.register_worker('registered-identity', ['writer'])
        claim = hub.claim_task('actual-summary-task', room_id=room['id'])
        source_secret, result_secret, event_secret = 'SOURCE-SECRET-816', 'RESULT-SECRET-994', 'EVENT-SECRET-275'
        hub.add_source(room['id'], '私有来源内容不属于聊天摘要', 'text', source_secret, 'https://example.test/?credential=' + source_secret)
        hub_execution.append_event(claim['task']['id'], 'actual-summary-task', claim['lease_token'], 'private-progress', 'progress', {'message': event_secret})
        hub_execution.stage_result(claim['task']['id'], 'actual-summary-task', claim['lease_token'], {'message': result_secret})
        with contextlib.closing(hub._db()) as conn:
            forbidden = {'hub_sources', 'hub_workers', 'hub_execution_events', 'hub_execution_results'}
            def authorizer(action, table, column, _database, _trigger):
                if action == sqlite3.SQLITE_READ and (table in forbidden or (table == 'hub_tasks' and column in ('result_json', 'lease_token', 'completion_hash'))):
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            conn.set_authorizer(authorizer)
            summary = hub._room_summaries(conn)[0]
        self.assertEqual(summary['last_message'], text[:160])
        self.assertEqual(len(summary['last_message']), 160)
        encoded = json.dumps(summary, ensure_ascii=False)
        for secret in (source_secret, result_secret, event_secret, claim['lease_token'], registration['session_token']):
            self.assertNotIn(secret, encoded)
        self.assertEqual(summary['last_speaker']['role'], 'user')
        detail = hub.get_room(room['id'])
        self.assertEqual(summary['last_message_at'], detail['messages'][0]['created_at'])

    def test_append_order_and_legacy_speaker_fallback_are_truthful(self):
        room = hub.create_room('较早追加的消息', agent_id='writer')
        later = hub.add_message(room['id'], '最后追加的消息', agent_id='writer')
        with hub._transaction() as conn:
            conn.execute('UPDATE hub_messages SET created_at=?,author_id=NULL,author_name=NULL WHERE id=?', ('1999-01-01T00:00:00+00:00', later['messages'][-1]['id']))
        summary = self.summary(room['id'])
        self.assertEqual(summary['last_message'], '最后追加的消息')
        self.assertEqual(summary['last_message_at'], '1999-01-01T00:00:00+00:00')
        self.assertEqual(summary['last_speaker']['name'], '用户')
        with hub._transaction() as conn:
            conn.execute('UPDATE hub_messages SET role=?,agent_id=? WHERE id=?', ('agent', 'legacy-agent-without-record', later['messages'][-1]['id']))
        self.assertEqual(self.summary(room['id'])['last_speaker']['name'], 'Agent')

    def test_room_summary_query_count_does_not_grow_with_rooms_or_runs(self):
        hub.create_room('最初的真实记录')
        with hub._transaction() as conn:
            def measure(names=None):
                trace = []
                conn.set_trace_callback(trace.append)
                try:
                    result = hub._room_summaries(conn, names)
                finally:
                    conn.set_trace_callback(None)
                return result, [sql for sql in trace if sql.lstrip().upper().startswith(('SELECT', 'WITH'))]
            small, initial_queries = measure()
            stamp = hub.now()
            for index in range(100):
                room_id, run_id, message_id = uuid4().hex, uuid4().hex, uuid4().hex
                conn.execute('INSERT INTO hub_rooms (id,title,created_at,updated_at) VALUES (?,?,?,?)', (room_id, f'Isolated query fixture {index}', stamp, stamp))
                conn.execute('INSERT INTO hub_runs (id,room_id,created_at) VALUES (?,?,?)', (run_id, room_id, stamp))
                conn.execute('INSERT INTO hub_messages (id,room_id,role,text,created_at) VALUES (?,?,?,?,?)', (message_id, room_id, 'user', 'Stored fixture text; not a generated model response.', stamp))
                task_id = hub._new_task(conn, room_id, run_id, 'writer', 'Status only', 'Fixture', [])
                # Deliberately invalid private JSON proves summaries do not parse result bodies.
                conn.execute('UPDATE hub_tasks SET result_json=? WHERE id=?', ('private body deliberately not JSON', task_id))
            large, large_queries = measure()
            self.assertEqual((len(small), len(large)), (1, 101))
            self.assertEqual(len(initial_queries), 3)
            self.assertEqual(len(large_queries), 3)
            _, shared_queries = measure({'writer': '内容编辑'})
            self.assertEqual(len(shared_queries), 2)
            self.assertTrue(all('result_json' not in sql for sql in large_queries))

    def test_request_workspace_scope_is_preserved_for_people_and_members(self):
        with self.actor('workspace-a-user', 'A 空间成员'):
            a = hub_teams.create_team('A 团队', ['writer', 'reviewer'], 'a-team', routing_mode='mention')
            hub.add_message(a['primary_room_id'], '仅 A 空间的对话', request_key='a-message')
        binding = core.REQUEST_DATA_ROOT.set(Path(self.temp.name) / 'workspace-b')
        try:
            self.assertEqual(hub.bootstrap()['rooms'], [])
            with self.actor('workspace-b-user', 'B 空间成员'):
                b = hub_teams.create_team('B 团队', ['developer'], 'b-team', routing_mode='mention')
                hub.add_message(b['primary_room_id'], '仅 B 空间的对话', request_key='b-message')
            rooms = hub.bootstrap()['rooms']
            self.assertEqual([room['id'] for room in rooms], [b['primary_room_id']])
            self.assertEqual(rooms[0]['last_speaker']['author_id'], 'workspace-b-user')
            self.assertEqual(rooms[0]['team_members_preview'], [{'agent_id': 'developer', 'name': '开发者'}])
            self.assertNotIn('workspace-a-user', json.dumps(rooms))
        finally:
            core.REQUEST_DATA_ROOT.reset(binding)
        rooms = hub.bootstrap()['rooms']
        self.assertEqual([room['id'] for room in rooms], [a['primary_room_id']])
        self.assertEqual(rooms[0]['last_speaker']['name'], 'A 空间成员')
        self.assertNotIn('workspace-b-user', json.dumps(rooms))


if __name__ == '__main__':
    unittest.main()
