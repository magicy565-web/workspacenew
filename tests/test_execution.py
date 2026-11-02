import concurrent.futures
import contextlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from studio import accounts, core, hub, hub_collaboration, hub_execution as execution, hub_http


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, core.REQUEST_DATA_ROOT.set(None))
        self.addCleanup(core.REQUEST_ACTOR.reset, core.REQUEST_ACTOR.set(None))

    def claim(self, room=None, worker='worker', capabilities=None, host_identity='host-a'):
        room = room or hub.create_room('Perform the fixture task', agent_id='writer')
        return hub.claim_task(worker, room_id=room['id'], capabilities=capabilities, host_identity=host_identity)

    def emit(self, claim, event_id, kind, payload, **kwargs):
        return execution.append_event(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], event_id, kind, payload, **kwargs)

    def expire(self, task_id):
        with hub._transaction() as conn:
            conn.execute('UPDATE hub_tasks SET lease_expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', task_id))

    def done(self, claim, result=None):
        return hub.complete_task(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], result or {'message': 'Fixture completed'})

    def stage(self, claim, result):
        return execution.stage_result(claim['task']['id'], claim['task']['worker_id'], claim['lease_token'], result)

    def host(self, claim, prefix='host', host='fixture', identity='host-a'):
        payload = {'host': host, 'session_id': 'host-session-1', 'host_identity': identity}
        created = self.emit(claim, prefix + ':session', 'host_session', payload)
        return self.emit(claim, prefix + ':checkpoint', 'checkpoint', dict(payload, checkpoint_id='checkpoint-1', through_seq=created['event']['seq']))

    def call(self, claim, call_id='external', safe=False):
        return self.emit(claim, call_id + ':call', 'tool_call', {'call_id': call_id, 'tool': 'fixture.external', 'summary': 'Public description of the fixture operation', 'retry_safe': safe})

    def review(self, claim, decision='confirmed_succeeded', call_id='external', key='review-1', note='Checked the actual fixture state.'):
        detail = execution.get_execution(claim['task']['id'])
        return execution.reconcile(detail['id'], key, detail['last_seq'], decision, note, call_id)

    def assert_error(self, expected_status, fn, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected_status, str(caught.exception))

    def test_session_is_stable_and_event_retries_are_atomic_and_fingerprinted(self):
        claim = self.claim()
        self.assertEqual(claim['execution']['id'], claim['task']['execution_session_id'])
        self.assertEqual(claim['execution']['recovery']['decision'], 'start')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.emit(claim, 'same-event', 'progress', {'message': 'Public progress'}, expected_seq=1), range(8)))
        self.assertEqual({item['event']['seq'] for item in results}, {2})
        self.assert_error(409, self.emit, claim, 'same-event', 'progress', {'message': 'Different content'})
        self.assertEqual(execution.get_execution(claim['task']['id'])['last_seq'], 2)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            events = list(pool.map(lambda index: self.emit(claim, f'unique-{index}', 'progress', {'message': str(index)}), range(6)))
        self.assertEqual(sorted(item['event']['seq'] for item in events), list(range(3, 9)))
        self.assert_error(409, self.emit, claim, 'stale-seq', 'progress', {'message': 'stale'}, expected_seq=2)
        collected, cursor = [], 0
        while True:
            page = execution.list_events(claim['execution']['id'], cursor, 3)
            collected.extend(page['events'])
            cursor = page['cursor']
            if not page['has_more']:
                break
        self.assertEqual([item['seq'] for item in collected], list(range(1, 9)))
        self.assertEqual(cursor, 8)
        self.assertNotIn(claim['lease_token'], json.dumps(collected))
        self.assertNotIn('lease_token', json.dumps(execution.get_execution(claim['task']['id'])))

    def test_old_lease_cannot_append_even_identical_event_or_complete(self):
        room = hub.create_room('Lease test', agent_id='writer')
        old = self.claim(room)
        self.emit(old, 'progress-1', 'progress', {'message': 'Before process loss'})
        self.expire(old['task']['id'])
        self.assert_error(409, self.emit, old, 'progress-1', 'progress', {'message': 'Before process loss'})
        new = self.claim(room, worker='replacement')
        self.assertEqual(new['execution']['id'], old['execution']['id'])
        self.assertEqual(new['execution']['recovery']['decision'], 'restart')
        self.assert_error(409, self.emit, old, 'late', 'progress', {'message': 'Late write'})
        self.assert_error(409, self.done, old)
        duplicate = self.emit(new, 'progress-1', 'progress', {'message': 'Before process loss'})
        self.assertEqual(duplicate['event']['attempt'], 1)
        self.done(new)
        self.assertEqual(execution.get_execution(old['task']['id'])['recovery']['decision'], 'completed')

    def test_checkpoint_survives_a_real_python_process_restart(self):
        room = hub.create_room('Process restart', agent_id='writer')
        claim = self.claim(room, capabilities=['continuation:fixture'])
        self.host(claim)
        self.expire(claim['task']['id'])
        script = 'import json,sys; from studio import hub; print(json.dumps(hub.claim_task("worker",room_id=sys.argv[1],capabilities=["continuation:fixture"],host_identity="host-a")))'
        child = subprocess.run([sys.executable, '-c', script, room['id']], cwd=core.ROOT, env=dict(os.environ, PYTHONUTF8='1'), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15, check=True)
        resumed = json.loads(child.stdout)
        self.assertEqual(resumed['execution']['id'], claim['execution']['id'])
        self.assertEqual(resumed['execution']['attempt'], 2)
        self.assertEqual(resumed['execution']['recovery']['decision'], 'continue')
        self.assertEqual(resumed['execution']['recovery']['checkpoint']['session_id'], 'host-session-1')
        self.assertNotEqual(resumed['lease_token'], claim['lease_token'])
        self.assertEqual(len(execution.get_execution(claim['task']['id'])['attempts']), 2)

    def test_continuation_requires_capability_worker_identity_and_current_checkpoint(self):
        for mode in ('capability', 'worker', 'host_identity', 'stale_checkpoint'):
            with self.subTest(mode=mode):
                room = hub.create_room(mode, agent_id='writer')
                claim = self.claim(room, capabilities=['continuation:fixture'])
                self.host(claim)
                if mode == 'stale_checkpoint':
                    self.call(claim)
                    self.emit(claim, 'result', 'tool_result', {'call_id': 'external', 'outcome': 'succeeded'})
                self.expire(claim['task']['id'])
                other = self.claim(room, worker='different' if mode == 'worker' else 'worker', capabilities=[] if mode == 'capability' else ['continuation:fixture'], host_identity='changed-config' if mode == 'host_identity' else 'host-a')
                self.assertEqual(other['execution']['recovery']['decision'], 'restart')
        registered = hub.register_worker('registered', ['writer'], capabilities=['events'])
        room = hub.create_room('Capability escalation is rejected', agent_id='writer')
        self.assert_error(409, hub.claim_task, 'registered', room_id=room['id'], session_token=registered['session_token'], capabilities=['continuation:fixture'])

    def test_active_call_is_not_blocked_but_unknown_outcome_requires_audited_review(self):
        room = hub.create_room('Review interrupted side effect', agent_id='writer')
        claim = self.claim(room, capabilities=['continuation:fixture'])
        self.host(claim)
        receipt = Path(self.temp.name) / 'external-receipt.txt'
        self.call(claim, 'external')
        receipt.write_text('applied once', encoding='utf-8')
        self.call(claim, 'never-ran')
        active = execution.get_execution(claim['task']['id'])
        self.assertTrue(active['active'])
        self.assertFalse(active['can_reconcile'])
        self.assertEqual(active['recovery']['decision'], 'start')
        self.assert_error(409, self.review, claim)
        unknown = self.emit(claim, 'external:unknown', 'tool_result', {'call_id': 'external', 'outcome': 'unknown', 'summary': 'Transport response was lost.'})
        detail = unknown['execution']
        self.assertEqual((detail['task_status'], detail['recovery']['decision']), ('failed', 'review_required'))
        self.assertFalse(detail['active'])
        self.assertTrue(detail['can_reconcile'])
        self.assertFalse(detail['can_retry'])
        self.assert_error(409, hub.retry_run, claim['task']['run_id'])
        self.assert_error(409, self.emit, claim, 'late-result', 'tool_result', {'call_id': 'external', 'outcome': 'succeeded'})
        before = detail['last_seq']
        self.review(claim, 'confirmed_not_executed', 'never-ran', 'review-never')
        self.assert_error(409, execution.reconcile, detail['id'], 'stale-review', before, 'confirmed_succeeded', 'Checked receipt.', 'external')
        reviewed = self.review(claim, note='Verified external-receipt.txt contains one application.')
        self.assertEqual(receipt.read_text(encoding='utf-8'), 'applied once')
        self.assertTrue(reviewed['can_retry'])
        notes = [event for event in execution.list_events(detail['id'], limit=100)['events'] if event['kind'] == 'recovery.reviewed']
        self.assertEqual(len(notes), 2)
        self.assertEqual(notes[-1]['payload']['decision'], 'confirmed_succeeded')
        hub.retry_run(claim['task']['run_id'])
        next_claim = self.claim(room, capabilities=['continuation:fixture'])
        self.assertEqual(next_claim['execution']['recovery']['decision'], 'restart', 'old host checkpoint cannot skip human reconciliation')
        self.assertEqual([call['state'] for call in next_claim['execution']['recovery']['calls']], ['succeeded', 'not_executed'])
        self.done(next_claim)
        self.assertEqual(receipt.read_text(encoding='utf-8'), 'applied once')

    def test_expired_pending_unsafe_call_is_not_automatically_reclaimed_or_completed(self):
        room = hub.create_room('Unacknowledged external call', agent_id='writer')
        old = self.claim(room)
        self.call(old)
        self.assert_error(409, self.done, old)
        self.expire(old['task']['id'])
        result = self.claim(room, worker='replacement')
        self.assertIsNone(result['task'])
        detail = execution.get_execution(old['task']['id'])
        self.assertEqual(detail['task_status'], 'failed')
        self.assertEqual(detail['recovery']['decision'], 'review_required')
        self.assertEqual(len(detail['attempts']), 1)
        self.assert_error(409, self.done, old)
        final = self.review(old, decision='terminate', call_id=None, note='No reliable external verification; stop the run.')
        self.assertEqual(final['recovery']['decision'], 'cancelled')
        self.assertIsNone(self.claim(room)['task'])
        self.assert_error(409, hub.retry_run, old['task']['run_id'])

    def test_retry_safe_pending_call_can_restart_without_fabricating_a_result(self):
        room = hub.create_room('Safe read', agent_id='writer')
        old = self.claim(room)
        self.call(old, safe=True)
        self.expire(old['task']['id'])
        new = self.claim(room)
        self.assertEqual(new['execution']['recovery']['decision'], 'restart')
        self.assertEqual(new['execution']['recovery']['calls'][0]['state'], 'pending')
        self.assertIsNone(new['execution']['recovery']['calls'][0]['result_seq'])
        self.emit(new, 'recovered-result', 'tool_result', {'call_id': 'external', 'outcome': 'succeeded', 'summary': 'Read completed after restart.'})
        self.done(new)

    def test_final_result_checkpoint_reclaims_without_model_and_completion_ack_loss_is_idempotent(self):
        room = hub.create_room('Final result receipt', agent_id='writer')
        old = self.claim(room)
        result = {'message': 'Fixture result', 'artifacts': [{'title': 'Output', 'kind': 'text', 'content': 'Durable artifact exact\r\n'}]}
        staged = self.stage(old, result)
        self.assertEqual(self.stage(old, result)['sha256'], staged['sha256'])
        self.assertEqual(hub.get_room(room['id'])['artifacts'], [])
        self.assert_error(409, self.stage, old, {'message': 'Replacement'})
        self.assert_error(409, self.done, old, {'message': 'Bypass staged result'})
        public = json.dumps(execution.list_events(old['execution']['id']))
        self.assertNotIn('Durable artifact exact', public)
        self.assertIn('result.ready', public)
        self.expire(old['task']['id'])
        reclaimed = self.claim(room, worker='new-worker', host_identity='new-host')
        self.assertEqual(reclaimed['execution']['recovery']['decision'], 'submit_result')
        frozen = execution.read_result(reclaimed['task']['id'], 'new-worker', reclaimed['lease_token'])
        self.assertEqual(frozen['result'], result)
        self.done(reclaimed, frozen['result'])  # Simulate server committed, response lost.
        self.done(reclaimed, frozen['result'])
        saved = hub.get_room(room['id'])
        self.assertEqual(len(saved['artifacts']), 1)
        artifact = hub.get_artifact(saved['artifacts'][0]['id'])
        self.assertEqual(len(artifact['versions']), 1)
        self.assertEqual(artifact['content'], result['artifacts'][0]['content'])
        events = execution.list_events(reclaimed['execution']['id'], limit=100)['events']
        self.assertEqual(sum(event['kind'] == 'task.completed' for event in events), 1)
        self.assertIsNone(self.claim(room)['task'])
        self.assert_error(409, execution.read_result, reclaimed['task']['id'], 'new-worker', reclaimed['lease_token'])

    def test_staged_result_does_not_override_unknown_side_effect_gate(self):
        room = hub.create_room('Result cannot erase unknown tool outcome', agent_id='writer')
        old = self.claim(room)
        self.call(old)
        self.stage(old, {'message': 'Host has a final answer but missing receipt'})
        self.expire(old['task']['id'])
        self.assertIsNone(self.claim(room)['task'])
        detail = execution.get_execution(old['task']['id'])
        self.assertTrue(detail['recovery']['result_available'])
        self.assertEqual(detail['recovery']['decision'], 'review_required')
        self.review(old, decision='confirmed_not_executed')
        hub.retry_run(old['task']['run_id'])
        new = self.claim(room)
        self.assertEqual(new['execution']['recovery']['decision'], 'submit_result')

    def test_modification_result_validation_has_no_early_version_side_effect(self):
        seed = self.claim()
        room = self.done(seed, {'message': 'Seed', 'artifacts': [{'title': 'Editable', 'kind': 'markdown', 'content': 'Original'}]})
        artifact = hub.get_artifact(room['artifacts'][0]['id'])
        comment = hub_collaboration.create_comment(artifact['id'], 1, 'Make it shorter', 'comment')
        request = hub_collaboration.create_modification(artifact['id'], 1, [comment['id']], 'mod')
        claim = self.claim(room)
        valid = {'message': 'Shortened', 'artifacts': [{'id': artifact['id'], 'base_version': 1, 'title': 'Editable', 'kind': 'markdown', 'content': 'Short'}]}
        self.assert_error(400, self.stage, claim, {'message': 'No target artifact'})
        self.stage(claim, valid)
        self.assertEqual(hub.get_artifact(artifact['id'])['version'], 1)
        self.assertIsNone(hub_collaboration.get_modification(request['id'])['result_version'])
        hub.edit_artifact(artifact['id'], 1, 'Human edit', 'markdown', 'Concurrent edit')
        self.assertEqual(self.stage(claim, valid)['sha256'], execution.read_result(claim['task']['id'], 'worker', claim['lease_token'])['sha256'])
        self.done(claim, valid)
        conflict = hub_collaboration.get_modification(request['id'])
        self.assertEqual(conflict['status'], 'conflict')
        self.assertEqual(conflict['candidate']['content'], 'Short')
        self.assertEqual(hub.get_artifact(artifact['id'])['content'], 'Concurrent edit')

    def test_reconciliation_is_atomic_idempotent_and_records_actor(self):
        claim = self.claim()
        self.call(claim)
        self.expire(claim['task']['id'])
        detail = execution.get_execution(claim['task']['id'])
        token = core.REQUEST_ACTOR.set({'user_id': 'fixture-operator', 'user_name': 'Fixture Operator', 'role': 'editor'})
        try:
            one = execution.reconcile(detail['id'], 'once', detail['last_seq'], 'confirmed_not_executed', 'Verified no external write.', 'external')
            two = execution.reconcile(detail['id'], 'once', detail['last_seq'], 'confirmed_not_executed', 'Verified no external write.', 'external')
            self.assertEqual(one['last_seq'], two['last_seq'])
            self.assert_error(409, execution.reconcile, detail['id'], 'once', detail['last_seq'], 'confirmed_succeeded', 'A changed decision', 'external')
            event = execution.list_events(detail['id'], limit=100)['events'][-1]
            self.assertEqual((event['author_id'], event['author_name']), ('fixture-operator', 'Fixture Operator'))
            self.assertEqual(event['kind'], 'recovery.reviewed')
        finally:
            core.REQUEST_ACTOR.reset(token)

    def test_membership_revocation_viewer_and_workspace_isolation(self):
        with accounts.transaction() as conn:
            conn.execute('INSERT INTO users VALUES (?,?,?,?,?)', ('editor', 'editor@example.test', 'Editor', 'not-used-by-this-fixture', hub.now()))
            conn.execute('INSERT INTO workspaces VALUES (?,?,?,?)', ('workspace', 'Fixture', '', hub.now()))
            conn.execute('INSERT INTO members VALUES (?,?,?)', ('workspace', 'editor', 'editor'))
        token = core.REQUEST_ACTOR.set({'user_id': 'editor', 'workspace_id': 'workspace', 'user_name': 'Editor', 'role': 'editor'})
        try:
            claim = self.claim()
            self.emit(claim, 'allowed', 'progress', {'message': 'Authorized before removal'})
            with accounts.transaction() as conn:
                conn.execute('DELETE FROM members WHERE workspace_id=? AND user_id=?', ('workspace', 'editor'))
            self.assert_error(403, self.emit, claim, 'after-removal', 'progress', {'message': 'Must not append'})
            self.assert_error(403, self.stage, claim, {'message': 'Must not stage'})
            self.assert_error(403, self.done, claim)
            self.assertEqual(execution.get_execution(claim['task']['id'])['task_status'], 'running')
        finally:
            core.REQUEST_ACTOR.reset(token)
        root_token = core.REQUEST_DATA_ROOT.set(Path(self.temp.name) / 'different-workspace')
        try:
            self.assert_error(404, execution.get_execution, claim['task']['id'])
            self.assert_error(404, execution.list_events, claim['execution']['id'])
            self.assert_error(404, self.emit, claim, 'cross-space', 'progress', {'message': 'No cross-space access'})
        finally:
            core.REQUEST_DATA_ROOT.reset(root_token)
        token = core.REQUEST_ACTOR.set({'role': 'viewer'})
        try:
            self.assertFalse(execution.get_execution(claim['task']['id'])['can_cancel'])
            self.assert_error(403, self.emit, claim, 'viewer', 'progress', {'message': 'No viewer append'})
            self.assert_error(403, execution.reconcile, claim['execution']['id'], 'viewer-review', 1, 'terminate', 'No viewer action')
        finally:
            core.REQUEST_ACTOR.reset(token)
        token = core.REQUEST_ACTOR.set({'role': 'editor', 'scopes': ['execute']})
        try:
            self.assert_error(403, execution.reconcile, claim['execution']['id'], 'execute-review', 1, 'terminate', 'No write scope')
        finally:
            core.REQUEST_ACTOR.reset(token)

    def test_schema_limits_reserved_terminal_capacity_and_no_private_reasoning(self):
        claim = self.claim()
        for kind, payload in [('thinking', {'text': 'Private trace'}), ({}, {}), ('progress', {'message': 'Public', 'reasoning': 'Private'}), ('tool_call', {'call_id': 'x', 'tool': 'execute', 'retry_safe': 1}), ('tool_result', {'call_id': 'missing', 'outcome': 'succeeded'})]:
            self.assert_error(409 if kind == 'tool_result' else 400, self.emit, claim, 'invalid', kind, payload)
        self.assert_error(400, self.emit, claim, 'system:forged', 'progress', {'message': 'Forged server event'})
        self.assert_error(400, self.emit, claim, 'secret', 'progress', {'message': claim['lease_token']})
        self.assert_error(400, execution.list_events, claim['execution']['id'], limit=101)
        self.assert_error(400, execution.list_events, claim['execution']['id'], after=True)
        self.assert_error(400, execution.list_events, claim['execution']['id'], after=2**100)
        self.assert_error(409, self.emit, claim, 'premature-checkpoint', 'checkpoint', {'host': 'fixture', 'session_id': 's', 'host_identity': 'host-a', 'checkpoint_id': 'c', 'through_seq': 1})
        with patch.object(execution, 'MAX_WORKER_EVENTS', 2):
            self.emit(claim, 'one', 'progress', {'message': 'One'})
            self.emit(claim, 'two', 'progress', {'message': 'Two'})
            self.assert_error(413, self.emit, claim, 'three', 'progress', {'message': 'Three'})
            self.done(claim)
        self.assertEqual(execution.list_events(claim['execution']['id'])['events'][-1]['kind'], 'task.completed')

    def test_cancel_blocks_append_and_http_routes_preserve_permissions_boundary(self):
        claim = self.claim()
        task_id, sid = claim['task']['id'], claim['execution']['id']
        body = {'worker_id': 'worker', 'lease_token': claim['lease_token'], 'event_id': 'http-progress', 'kind': 'progress', 'payload': {'message': 'HTTP fixture'}}
        self.assertEqual(hub_http.dispatch('POST', f'/api/hub/tasks/{task_id}/execution/events', body)[0], 201)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/tasks/{task_id}/execution')[1]['id'], sid)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/executions/{sid}/events', query={'after': ['0'], 'limit': ['1']})[1]['has_more'], True)
        self.assertEqual(hub_http.dispatch('POST', f'/api/hub/tasks/{task_id}/execution/result/read', {'worker_id': 'worker', 'lease_token': claim['lease_token']})[0], 404)
        hub.cancel_run(claim['task']['run_id'])
        self.assertEqual(hub_http.dispatch('POST', f'/api/hub/tasks/{task_id}/execution/events', body)[0], 409)
        self.assertEqual(execution.get_execution(task_id)['recovery']['decision'], 'cancelled')
        self.assertEqual(execution.list_events(sid, limit=100)['events'][-1]['kind'], 'task.cancelled')


if __name__ == '__main__':
    unittest.main()
