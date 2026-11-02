import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio import core, hub, hub_agent_sessions as sessions, hub_execution as execution, hub_extensions, hub_teams


class AgentSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, core.REQUEST_DATA_ROOT.set(None))
        self.addCleanup(core.REQUEST_ACTOR.reset, core.REQUEST_ACTOR.set(None))

    def claim(self, room, worker='host-worker', identity='host-fingerprint', capabilities=None, agents=None):
        return hub.claim_task(worker, room_id=room['id'], agent_ids=agents, host_identity=identity, capabilities=['continuation:fixture'] if capabilities is None else capabilities)

    def complete(self, claim, result=None):
        return hub.complete_task(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], result or {'message': 'Protocol fixture completed'})

    def event(self, claim, event_id, kind, payload):
        return execution.append_event(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], event_id, kind, payload)

    def native(self, claim, native_id=None):
        agent_continuation = claim['execution']['agent_session']['continuation']
        recovery = claim['execution']['recovery']
        if native_id is None:
            point = recovery['checkpoint'] if recovery['decision'] == 'continue' else agent_continuation['checkpoint']
            native_id = point['session_id'] if point else 'native-' + claim['task']['id'] + '-a' + str(claim['task']['attempts'])
        payload = {'host': 'fixture', 'session_id': native_id, 'host_identity': claim['execution']['agent_session']['continuation'].get('checkpoint', {}).get('host_identity') if agent_continuation['checkpoint'] else 'host-fingerprint'}
        # Claim host identity is visible in the attempt receipt; use that exact
        # declaration even for host-mismatch test cases.
        payload['host_identity'] = execution.get_execution(claim['task']['id'])['attempts'][-1]['host_identity']
        prefix = 'a' + str(claim['task']['attempts'])
        host = self.event(claim, prefix + '-host', 'host_session', payload)
        point = self.event(claim, prefix + '-checkpoint', 'checkpoint', dict(payload, checkpoint_id=native_id + ':done', through_seq=host['event']['seq']))
        return native_id, point

    def follow(self, room, text='Next user request', **kwargs):
        room = hub.add_message(room['id'], text, agent_id='writer')
        return room, self.claim(room, **kwargs)

    def seeded(self):
        room = hub.create_room('First user request', agent_id='writer')
        first = self.claim(room)
        native_id, _ = self.native(first)
        self.complete(first)
        return room, first, native_id

    def expire(self, claim):
        with hub._transaction() as conn:
            conn.execute("UPDATE hub_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (claim['task']['id'],))

    def unsafe(self, claim, call_id='external', outcome=None):
        self.event(claim, call_id + '-start', 'tool_call', {'call_id': call_id, 'tool': 'fixture.side_effect', 'retry_safe': False, 'summary': 'A protocol fixture call'})
        if outcome:
            self.event(claim, call_id + '-result', 'tool_result', {'call_id': call_id, 'outcome': outcome})

    def review(self, claim, decision='confirmed_not_executed', key='review', call_id='external'):
        detail = execution.get_execution(claim['task']['id'])
        return execution.reconcile(detail['id'], key, detail['last_seq'], decision, 'Inspected the actual fixture outcome before deciding.', call_id if decision != 'terminate' else None)

    def error(self, status, callback, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            callback(*args, **kwargs)
        self.assertEqual(caught.exception.status, status, str(caught.exception))

    def test_two_tasks_share_logical_identity_and_only_the_completed_native_head(self):
        room, first, native_id = self.seeded()
        room, second = self.follow(room)
        one, two = first['execution']['agent_session'], second['execution']['agent_session']
        self.assertEqual((one['id'], one['generation']), (two['id'], two['generation']))
        self.assertNotEqual(first['task']['id'], second['task']['id'])
        self.assertNotEqual(first['execution']['id'], second['execution']['id'])
        self.assertEqual(two['turn_index'], 2)
        self.assertEqual(second['execution']['recovery']['decision'], 'start')
        self.assertEqual(two['continuation']['decision'], 'continue')
        self.assertEqual(two['continuation']['from_task_id'], first['task']['id'])
        self.assertEqual(two['continuation']['checkpoint']['session_id'], native_id)
        self.assertNotIn('through_seq', two['continuation']['checkpoint'])
        self.assertNotIn('event_seq', two['continuation']['checkpoint'])
        self.error(409, self.event, second, 'wrong-native', 'host_session', {'host': 'fixture', 'session_id': 'different-native', 'host_identity': 'host-fingerprint'})
        self.native(second)
        self.complete(second)
        self.complete(second)  # Lost completion response: idempotent, no new turn.
        state = sessions.get_session(one['id'])
        self.assertEqual(len(state['turns']), 2)
        self.assertEqual(state['session']['last_completed_task_id'], second['task']['id'])
        self.assertEqual(state['session']['native_head']['session_id'], native_id)
        self.assertEqual(state['delegations'], [])

    def test_native_head_and_identity_survive_a_new_python_process(self):
        room, first, native_id = self.seeded()
        hub.add_message(room['id'], 'Continue after a service process restart', agent_id='writer')
        script = 'import json,sys;from studio import hub;print(json.dumps(hub.claim_task("host-worker",room_id=sys.argv[1],capabilities=["continuation:fixture"],host_identity="host-fingerprint")))'
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', script, room['id']], cwd=core.ROOT, env=dict(os.environ, PYTHONUTF8='1'), capture_output=True, text=True, timeout=15, check=True)
        second = json.loads(result.stdout)
        self.assertEqual(second['execution']['agent_session']['id'], first['execution']['agent_session']['id'])
        self.assertEqual(second['execution']['agent_session']['continuation']['checkpoint']['session_id'], native_id)

    def test_two_workers_cannot_claim_two_ready_tasks_in_one_logical_session(self):
        room = hub.create_room('Plan concurrent work')
        coordinator = self.claim(room)
        self.complete(coordinator, {'message': 'Accepted fixture plan', 'plan': [
            {'key': 'one', 'agent_id': 'writer', 'title': 'One', 'instructions': 'One', 'depends_on': []},
            {'key': 'two', 'agent_id': 'writer', 'title': 'Two', 'instructions': 'Two', 'depends_on': []},
            {'key': 'review', 'agent_id': 'reviewer', 'title': 'Independent', 'instructions': 'Independent', 'depends_on': []},
        ]})
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            claims = list(pool.map(lambda index: self.claim(room, worker='writer-' + str(index), agents=['writer']), range(5)))
        winners = [claim for claim in claims if claim['task']]
        self.assertEqual(len(winners), 1)
        self.assertTrue(all(claim.get('blocked_sessions') for claim in claims if not claim['task']))
        other = self.claim(room, worker='review-worker', agents=['reviewer'])
        self.assertIsNotNone(other['task'])
        self.complete(winners[0])
        second = self.claim(room, worker='writer-next', agents=['writer'])
        self.assertIsNotNone(second['task'])
        self.assertEqual(second['execution']['agent_session']['id'], winners[0]['execution']['agent_session']['id'])

    def test_most_recent_completion_without_checkpoint_never_resurrects_old_branch(self):
        room, first, _ = self.seeded()
        room, second = self.follow(room, worker='other-worker', identity='another-host', capabilities=[])
        self.assertEqual(second['execution']['agent_session']['continuation']['reason'], 'worker_changed')
        self.complete(second)
        room, third = self.follow(room)
        continuation = third['execution']['agent_session']['continuation']
        self.assertEqual((continuation['decision'], continuation['reason']), ('fresh', 'no_completed_checkpoint'))
        self.assertIsNone(continuation['checkpoint'])
        self.assertGreater(third['execution']['agent_session']['generation'], first['execution']['agent_session']['generation'])

    def test_host_and_capability_mismatches_start_fresh_with_explicit_reasons(self):
        for mode, kwargs, reason in [('host', {'identity': 'changed-host'}, 'host_changed'), ('capability', {'capabilities': []}, 'continuation_unsupported')]:
            with self.subTest(mode=mode):
                room, _, _ = self.seeded()
                room, second = self.follow(room, **kwargs)
                self.assertEqual(second['execution']['agent_session']['continuation']['reason'], reason)
                self.assertEqual(second['execution']['agent_session']['continuation']['decision'], 'fresh')

    def test_instruction_skill_source_and_background_changes_invalidate_native_context(self):
        for change, expected in [('instructions', 'agent_instructions_changed'), ('skills', 'skills_changed'), ('sources', 'source_visibility_changed'), ('background', 'shared_context_changed')]:
            with self.subTest(change=change):
                room = hub.create_room('Initial environment ' + change, agent_id='writer')
                source = hub.add_source(room['id'], 'Temporary source', 'text', 'Old supplied source') if change == 'sources' else None
                first = self.claim(room)
                old_native, _ = self.native(first)
                self.complete(first)
                if change == 'instructions':
                    hub.update_agent('writer', 'Writer', 'Changed description', 'New role instructions')
                elif change == 'skills':
                    skill = hub_extensions.create_skill('New skill', '', 'New host guidance')
                    current = hub_extensions.get_agent_skills('writer')
                    hub_extensions.set_agent_skills('writer', current['version'], [skill['id']])
                elif change == 'sources':
                    hub.revoke_source(source['id'])
                else:
                    hub.update_context(room['id'], 'New shared background')
                room, second = self.follow(room)
                current = second['execution']['agent_session']
                self.assertEqual(current['continuation']['reason'], expected)
                self.assertGreater(current['generation'], first['execution']['agent_session']['generation'])
                self.error(409, self.native, second, old_native)

    def test_team_membership_change_requires_fresh_context_without_expanding_old_runs(self):
        team = hub_teams.create_team('Persistent team', ['writer', 'reviewer'], 'team', routing_mode='mention')
        room = hub.get_room(team['primary_room_id'])
        hub.add_message(room['id'], 'Writer first turn', target_agent_ids=['writer'], request_key='first')
        first = self.claim(room)
        self.native(first)
        self.complete(first)
        hub_teams.update_team(team['id'], team['version'], member_ids=['writer', 'reviewer', 'designer'])
        hub.add_message(room['id'], 'Writer next turn', target_agent_ids=['writer'], request_key='second')
        second = self.claim(room)
        self.assertEqual(second['execution']['agent_session']['continuation']['reason'], 'team_permissions_changed')
        self.assertEqual(second['execution']['agent_session']['id'], first['execution']['agent_session']['id'])
        self.assertEqual(first['context']['team']['allowed_agent_ids'], ['reviewer', 'writer'])

    def test_expired_previous_task_is_recovered_before_the_next_task(self):
        room, first, _ = self.seeded()
        room, second = self.follow(room)
        self.native(second)
        self.expire(second)
        hub.add_message(room['id'], 'Third user turn queued', agent_id='writer')
        reclaimed = self.claim(room)
        self.assertEqual(reclaimed['task']['id'], second['task']['id'])
        self.assertEqual(reclaimed['execution']['recovery']['decision'], 'continue')
        self.assertEqual(reclaimed['execution']['agent_session']['continuation']['decision'], 'task_recovery')
        self.error(409, self.event, second, 'late-event', 'progress', {'message': 'Late old lease'})
        self.error(409, self.complete, second)
        self.complete(reclaimed)
        third = self.claim(room)
        self.assertNotEqual(third['task']['id'], second['task']['id'])
        self.assertEqual(third['execution']['agent_session']['continuation']['from_task_id'], second['task']['id'])

    def test_unknown_call_and_plain_cancel_block_future_turn_until_audited_review(self):
        room, _, _ = self.seeded()
        room, second = self.follow(room)
        self.native(second)
        self.unsafe(second, outcome='unknown')
        hub.cancel_run(second['task']['run_id'])
        room = hub.add_message(room['id'], 'Cannot skip unconfirmed side effect', agent_id='writer')
        blocked = self.claim(room)
        self.assertIsNone(blocked['task'])
        self.assertEqual(blocked['blocked_sessions'][0]['blocked_by_task_id'], second['task']['id'])
        self.assertIn('核对', blocked['blocked_sessions'][0]['message'])
        detail = execution.get_execution(second['task']['id'])
        self.assertEqual(detail['recovery']['decision'], 'cancelled')
        self.assertTrue(detail['can_reconcile'])
        summary = sessions.get_session(second['execution']['agent_session']['id'])['session']
        self.assertEqual(summary['status'], 'blocked')
        self.assertEqual(summary['blocked_by_task_id'], second['task']['id'])
        with patch.object(core, 'REQUEST_ACTOR') as actor:
            actor.get.return_value = {'role': 'viewer'}
            self.error(403, self.review, second)
        self.review(second)
        third = self.claim(room)
        self.assertIsNotNone(third['task'])
        self.assertEqual(third['execution']['agent_session']['continuation']['decision'], 'fresh')
        self.assertEqual(execution.get_execution(second['task']['id'])['task_status'], 'cancelled')
        self.error(409, self.complete, second)

    def test_audited_termination_releases_future_work_without_claiming_side_effect_absence(self):
        room = hub.create_room('Unknown side effect', agent_id='writer')
        first = self.claim(room)
        self.unsafe(first, outcome='unknown')
        hub.cancel_run(first['task']['run_id'])
        self.review(first, decision='terminate')
        detail = execution.get_execution(first['task']['id'])
        self.assertFalse(detail['can_reconcile'])
        self.assertEqual(detail['recovery']['calls'][0]['state'], 'unknown')
        room, second = self.follow(room)
        self.assertIsNotNone(second['task'])
        self.assertEqual(second['execution']['agent_session']['continuation']['decision'], 'fresh')

    def test_task_result_recovery_has_priority_and_context_change_invalidates_own_resume(self):
        room, _, _ = self.seeded()
        room, second = self.follow(room)
        self.native(second)
        execution.stage_result(second['task']['id'], 'host-worker', second['lease_token'], {'message': 'Frozen second result'})
        self.expire(second)
        reclaimed = self.claim(room)
        self.assertEqual(reclaimed['execution']['recovery']['decision'], 'submit_result')
        self.assertEqual(reclaimed['execution']['agent_session']['continuation']['decision'], 'task_recovery')
        self.complete(reclaimed, {'message': 'Frozen second result'})
        room, third = self.follow(room)
        self.native(third)
        hub.update_agent('writer', 'Writer changed', 'Changed', 'Different current role')
        self.expire(third)
        retry = self.claim(room)
        self.assertEqual(retry['execution']['recovery']['decision'], 'restart')
        self.assertEqual(retry['execution']['recovery']['reason'], 'agent_instructions_changed')
        self.assertEqual(retry['execution']['agent_session']['continuation']['decision'], 'task_recovery')

    def test_same_task_native_target_is_frozen_before_later_tool_events(self):
        room, _, _ = self.seeded()
        room, second = self.follow(room)
        self.native(second)
        self.expire(second)
        retry = self.claim(room)
        self.assertEqual(retry['execution']['recovery']['decision'], 'continue')
        self.unsafe(retry)
        # This new event makes the old checkpoint no longer cover all calls.
        # It must not erase the exact native target selected at claim time.
        self.error(409, self.event, retry, 'wrong-after-call', 'host_session', {'host': 'fixture', 'session_id': 'different-native', 'host_identity': 'host-fingerprint'})
        self.event(retry, 'external-confirmed', 'tool_result', {'call_id': 'external', 'outcome': 'not_executed'})
        self.native(retry)
        self.complete(retry)

    def test_legacy_upgrade_keeps_all_cancelled_unknown_tasks_visible_until_reviewed(self):
        room = hub.create_room('First legacy task', agent_id='writer')
        first = self.claim(room)
        self.unsafe(first, outcome='unknown')
        hub.cancel_run(first['task']['run_id'])
        hub.add_message(room['id'], 'Second legacy task', agent_id='writer')
        # v0.9 had no logical-session gate. Reproduce those valid legacy rows,
        # then remove only the new tables to exercise an actual schema backfill.
        with patch.object(sessions, 'claim_blocker', return_value=None):
            second = self.claim(room)
        self.unsafe(second, outcome='unknown')
        hub.cancel_run(second['task']['run_id'])
        with contextlib.closing(core.db()) as conn:
            conn.executescript('DROP TABLE hub_agent_delegations; DROP TABLE hub_agent_session_attempts; DROP TABLE hub_agent_session_turns; DROP TABLE hub_agent_sessions;')
        restored = sessions.list_sessions(room['id'])['sessions'][0]
        self.assertEqual(restored['blocked_by_task_id'], first['task']['id'])
        self.assertIsNone(restored['native_head'])
        hub.add_message(room['id'], 'New work after upgrade', agent_id='writer')
        self.assertIsNone(self.claim(room)['task'])
        self.review(first)
        next_block = self.claim(room)
        self.assertIsNone(next_block['task'])
        self.assertEqual(next_block['blocked_sessions'][0]['blocked_by_task_id'], second['task']['id'])
        self.review(second, decision='terminate')
        new = self.claim(room)
        self.assertIsNotNone(new['task'])
        self.assertNotIn(new['task']['id'], (first['task']['id'], second['task']['id']))
        self.assertEqual(new['execution']['agent_session']['continuation']['decision'], 'fresh')
        self.assertTrue(all(execution.get_execution(task['task']['id'])['task_status'] == 'cancelled' for task in (first, second)))

    def test_large_context_keeps_session_metadata_and_explicit_source_omissions_bounded(self):
        room, _, _ = self.seeded()
        source = hub.add_source(room['id'], 'Large provided fixture source', 'text', 'x' * 440000)
        room, next_turn = self.follow(room)
        self.assertLessEqual(len(json.dumps(next_turn['context'], ensure_ascii=False).encode()), hub.MAX_CONTEXT)
        self.assertLessEqual(len(json.dumps(next_turn['execution']['agent_session'], ensure_ascii=False).encode()), sessions.MAX_DESCRIPTOR)
        self.assertEqual(next_turn['execution']['agent_session']['continuation']['reason'], 'source_visibility_changed')
        omitted = [item for item in next_turn['context']['omissions'] if item['kind'] == 'sources']
        self.assertEqual(omitted[0]['ids'], [source['id']])
        self.assertTrue(any(item['id'] == source['id'] for item in next_turn['context']['references']))

    def test_delegation_edges_only_come_from_accepted_coordinator_plans(self):
        room = hub.create_room('Coordinator plan')
        coordinator = self.claim(room)
        bad_plan = {'message': 'Invalid atomic fixture', 'plan': [{'key': 'bad', 'agent_id': 'missing-agent', 'title': 'Bad', 'instructions': 'Bad', 'depends_on': []}]}
        self.error(404, self.complete, coordinator, bad_plan)
        self.assertEqual(sessions.get_session(coordinator['execution']['agent_session']['id'])['delegations'], [])
        self.complete(coordinator, {'message': 'Accepted fixture plan', 'plan': [{'key': 'write', 'agent_id': 'writer', 'title': 'Write', 'instructions': 'Write', 'depends_on': []}]})
        child = self.claim(room)
        detail = sessions.get_session(child['execution']['agent_session']['id'])
        self.assertEqual(detail['turns'][0]['delegated_from_task_id'], coordinator['task']['id'])
        self.assertEqual(detail['delegations'][0]['parent_session_id'], coordinator['execution']['agent_session']['id'])
        workflow = hub.create_room('Fixed workflow', template_id='research')
        for row in sessions.list_sessions(workflow['id'])['sessions']:
            self.assertEqual(sessions.get_session(row['id'])['delegations'], [])
        direct = hub.create_room('Explicit writer', agent_id='writer')
        self.assertEqual(sessions.get_session(sessions.list_sessions(direct['id'])['sessions'][0]['id'])['delegations'], [])

    def test_read_pagination_is_workspace_scoped_bounded_and_has_no_credentials(self):
        room, first, _ = self.seeded()
        room, second = self.follow(room)
        first_page = sessions.get_session(first['execution']['agent_session']['id'], limit=1)
        self.assertTrue(first_page['has_more'])
        second_page = sessions.get_session(first_page['session']['id'], after=first_page['cursor'], limit=1)
        self.assertEqual(second_page['turns'][0]['task_id'], second['task']['id'])
        raw = json.dumps([first_page, second_page, sessions.list_sessions(room['id'])])
        self.assertNotIn(first['lease_token'], raw)
        self.assertNotIn(second['lease_token'], raw)
        self.assertNotIn('lease_token', raw)
        self.assertLessEqual(len(json.dumps(second['execution']['agent_session']).encode()), sessions.MAX_DESCRIPTOR)
        self.error(400, sessions.get_session, first_page['session']['id'], limit=101)
        binding = core.REQUEST_DATA_ROOT.set(Path(self.temp.name) / 'other-workspace')
        try:
            self.error(404, sessions.get_session, first_page['session']['id'])
            self.error(404, sessions.list_sessions, room['id'])
        finally:
            core.REQUEST_DATA_ROOT.reset(binding)
        another = hub.create_room('Another room writer', agent_id='writer')
        third = self.claim(another)
        self.assertNotEqual(third['execution']['agent_session']['id'], first_page['session']['id'])
        self.assertEqual(third['execution']['agent_session']['continuation']['decision'], 'fresh')


if __name__ == '__main__':
    unittest.main()
