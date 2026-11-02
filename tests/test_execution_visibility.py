"""Actual HTTP outcome visibility is independent of mutation authority."""
import contextlib
import json
import unittest

import test_accounts as account_helpers
from studio import hub


class OutcomeVisibilityTests(unittest.TestCase):
    fields = ('outcome_review_required', 'unresolved_outcome_count', 'outcome_review_terminated')

    def setUp(self):
        self.http = account_helpers.AccountsTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)
        self.owner_info, self.owner = self.http.setup_owner()
        self.viewer_info, self.viewer = self.http.accept(self.http.invite(self.owner, role='viewer'))
        self.read = self.http.make_token(self.owner, ['read'])['token']

    def request(self, method, path, body=None, expected=200, **auth):
        status, data, _ = self.http.request(method, path, body, **auth)
        self.assertEqual(status, expected, data)
        return data

    def create(self):
        room = self.request('POST', '/api/hub/rooms', {'text': 'Outcome visibility protocol fixture', 'agent_id': 'writer'}, expected=201, cookie=self.owner)
        claim = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'visibility-worker', 'room_id': room['id']}, cookie=self.owner)
        return room, claim

    def emit(self, claim, event_id, kind, payload):
        return self.request('POST', '/api/hub/tasks/' + claim['task']['id'] + '/execution/events', {
            'worker_id': 'visibility-worker', 'lease_token': claim['lease_token'],
            'event_id': event_id, 'kind': kind, 'payload': payload,
        }, expected=201, cookie=self.owner)

    def start_call(self, claim, call_id='external', safe=False):
        self.emit(claim, call_id + '-start', 'tool_call', {
            'call_id': call_id, 'tool': 'fixture.external_action', 'retry_safe': safe,
            'summary': 'Recorded protocol fixture, not an actual external action',
        })

    def detail(self, claim, **auth):
        return self.request('GET', '/api/hub/tasks/' + claim['task']['id'] + '/execution', **auth)

    def cancel_unknown(self):
        room, claim = self.create()
        self.start_call(claim)
        self.emit(claim, 'external-unknown', 'tool_result', {'call_id': 'external', 'outcome': 'unknown'})
        self.request('POST', '/api/hub/runs/' + claim['task']['run_id'] + '/cancel', {}, cookie=self.owner)
        return room, claim

    def facts(self, detail):
        return tuple(detail[key] for key in self.fields)

    def review_body(self, detail, decision='confirmed_not_executed', key='review'):
        return {
            'request_key': key, 'expected_seq': detail['last_seq'], 'decision': decision,
            'call_id': 'external' if decision != 'terminate' else None,
            'note': 'Inspected the controlled fixture; this is outcome review, not action approval.',
        }

    def test_cancelled_unknown_is_visible_to_viewer_and_read_token_but_mutations_stay_forbidden(self):
        room, claim = self.cancel_unknown()
        owner = self.detail(claim, cookie=self.owner)
        viewer = self.detail(claim, cookie=self.viewer)
        read = self.detail(claim, token=self.read)
        for detail in (owner, viewer, read):
            self.assertEqual(self.facts(detail), (True, 1, False))
            self.assertEqual((detail['task_status'], detail['recovery']['decision']), ('cancelled', 'cancelled'))
            self.assertNotIn(claim['lease_token'], json.dumps(detail))
            self.assertNotIn('lease_token', json.dumps(detail))
        self.assertTrue(owner['can_reconcile'])
        self.assertFalse(viewer['can_reconcile'])
        self.assertFalse(read['can_reconcile'])
        path = '/api/hub/executions/' + owner['id'] + '/reconcile'
        body = self.review_body(owner)
        for auth in ({'cookie': self.viewer}, {'token': self.read}):
            self.request('POST', path, body, expected=403, **auth)
        after_denials = self.detail(claim, cookie=self.owner)
        self.assertEqual(after_denials['last_seq'], owner['last_seq'])
        self.assertEqual(self.facts(after_denials), (True, 1, False))
        self.request('POST', '/api/hub/rooms/' + room['id'] + '/messages', {'text': 'Later task must wait', 'agent_id': 'writer'}, cookie=self.owner)
        blocked = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'later-worker', 'room_id': room['id']}, cookie=self.owner)
        self.assertIsNone(blocked['task'])
        reviewed = self.request('POST', path, body, cookie=self.owner)
        self.assertEqual(self.facts(reviewed), (False, 0, False))
        self.assertEqual(self.facts(self.detail(claim, cookie=self.viewer)), (False, 0, False))
        self.assertEqual(reviewed['task_status'], 'cancelled')
        later = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'later-worker', 'room_id': room['id']}, cookie=self.owner)
        self.assertIsNotNone(later['task'])
        self.assertNotEqual(later['task']['id'], claim['task']['id'])
        self.request('POST', '/api/hub/tasks/' + claim['task']['id'] + '/complete', {
            'worker_id': 'visibility-worker', 'lease_token': claim['lease_token'], 'result': {'message': 'Late result must remain rejected'},
        }, expected=409, cookie=self.owner)

    def test_audited_termination_preserves_unknown_count_for_all_readers_without_reopening_task(self):
        _, claim = self.cancel_unknown()
        before = self.detail(claim, cookie=self.owner)
        path = '/api/hub/executions/' + before['id'] + '/reconcile'
        reviewed = self.request('POST', path, self.review_body(before, decision='terminate'), cookie=self.owner)
        for detail in (reviewed, self.detail(claim, cookie=self.viewer), self.detail(claim, token=self.read)):
            self.assertEqual(self.facts(detail), (False, 1, True))
            self.assertEqual(detail['recovery']['calls'][0]['state'], 'unknown')
            self.assertFalse(detail['can_reconcile'])
            self.assertEqual(detail['task_status'], 'cancelled')
        self.request('POST', '/api/hub/runs/' + claim['task']['run_id'] + '/retry', {}, expected=409, cookie=self.owner)
        self.request('POST', '/api/hub/tasks/' + claim['task']['id'] + '/heartbeat', {
            'worker_id': 'visibility-worker', 'lease_token': claim['lease_token'],
        }, expected=409, cookie=self.owner)

    def test_live_pending_is_not_mislabelled_but_expired_or_cancelled_pending_requires_review(self):
        _, claim = self.create()
        self.start_call(claim)
        for auth in ({'cookie': self.owner}, {'cookie': self.viewer}, {'token': self.read}):
            live = self.detail(claim, **auth)
            self.assertTrue(live['active'])
            self.assertEqual(self.facts(live), (False, 0, False))
            self.assertFalse(live['can_reconcile'])
        with hub._transaction() as conn:
            conn.execute("UPDATE hub_tasks SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE id=?", (claim['task']['id'],))
        for auth in ({'cookie': self.owner}, {'cookie': self.viewer}, {'token': self.read}):
            expired = self.detail(claim, **auth)
            self.assertFalse(expired['active'])
            self.assertEqual(self.facts(expired), (True, 1, False))
            self.assertEqual(expired['recovery']['calls'][0]['state'], 'pending')
        self.request('POST', '/api/hub/runs/' + claim['task']['run_id'] + '/cancel', {}, cookie=self.owner)
        cancelled = self.detail(claim, cookie=self.viewer)
        self.assertEqual(self.facts(cancelled), (True, 1, False))
        self.assertEqual(cancelled['task_status'], 'cancelled')
        self.assertFalse(cancelled['can_reconcile'])
        with contextlib.closing(hub._db()) as conn:
            row = conn.execute('SELECT status,lease_token FROM hub_tasks WHERE id=?', (claim['task']['id'],)).fetchone()
        self.assertEqual(row['status'], 'cancelled')
        self.assertIsNone(row['lease_token'])

    def test_count_is_unsafe_outcome_review_scope_not_a_generic_incomplete_call_counter(self):
        _, claim = self.create()
        self.start_call(claim, safe=True)
        self.emit(claim, 'safe-unknown', 'tool_result', {'call_id': 'external', 'outcome': 'unknown'})
        self.request('POST', '/api/hub/runs/' + claim['task']['run_id'] + '/cancel', {}, cookie=self.owner)
        detail = self.detail(claim, cookie=self.viewer)
        self.assertEqual(self.facts(detail), (False, 0, False))
        # The recorded uncertainty remains public; a caller's existing truthful
        # retry-safe declaration exempts it only from mandatory outcome review.
        self.assertEqual(detail['recovery']['calls'][0]['state'], 'unknown')
        self.assertTrue(detail['recovery']['calls'][0]['retry_safe'])
        self.assertFalse(detail['can_reconcile'])

    def test_outcome_fields_do_not_bypass_workspace_or_current_membership_read_access(self):
        _, claim = self.cancel_unknown()
        path = '/api/hub/tasks/' + claim['task']['id'] + '/execution'
        self.request('GET', path, expected=401)
        execute = self.http.make_token(self.owner, ['execute'])['token']
        self.request('GET', path, expected=403, token=execute)
        self.request('POST', '/api/account/workspaces', {'name': 'Other workspace'}, expected=201, cookie=self.owner)
        self.request('GET', path, expected=404, cookie=self.owner)
        self.assertEqual(self.facts(self.detail(claim, token=self.read)), (True, 1, False))
        self.request('POST', '/api/account/workspaces/' + self.owner_info['workspace']['id'] + '/select', {}, cookie=self.owner)
        self.request('DELETE', '/api/account/members/' + self.viewer_info['user']['id'], {}, cookie=self.owner)
        self.request('GET', path, expected=403, cookie=self.viewer)
        self.assertEqual(self.facts(self.detail(claim, cookie=self.owner)), (True, 1, False))


if __name__ == '__main__':
    unittest.main()
