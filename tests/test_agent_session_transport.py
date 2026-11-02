"""Real HTTP permission/pagination checks for persistent Agent-session reads."""
import json
import unittest

import test_accounts as account_helpers


class SessionTransportTests(unittest.TestCase):
    def setUp(self):
        self.http = account_helpers.AccountsTests()
        self.http.setUp()
        self.addCleanup(self.http.doCleanups)

    def request(self, method, path, body=None, **auth):
        return self.http.request(method, path, body, **auth)

    def first_turn(self):
        status, room, _ = self.request('POST', '/api/hub/rooms', {'text': 'Transport fixture first turn', 'agent_id': 'writer'})
        self.assertEqual(status, 201)
        status, claim, _ = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'transport-test', 'room_id': room['id'], 'host_identity': 'transport-host'})
        self.assertEqual(status, 200)
        sid = claim['execution']['agent_session']['id']
        status, _, _ = self.request('POST', '/api/hub/tasks/' + claim['task']['id'] + '/complete', {'worker_id': 'transport-test', 'lease_token': claim['lease_token'], 'result': {'message': 'Protocol fixture, not model work'}})
        self.assertEqual(status, 200)
        return room, claim, sid

    def test_real_http_two_turns_share_identity_and_paginate_without_credentials(self):
        room, first, sid = self.first_turn()
        status, listing, _ = self.request('GET', '/api/hub/rooms/' + room['id'] + '/agent-sessions')
        self.assertEqual(status, 200)
        self.assertEqual(listing['sessions'][0]['id'], sid)
        status, _, _ = self.request('POST', '/api/hub/rooms/' + room['id'] + '/messages', {'text': 'Next work in the same conversation', 'agent_id': 'writer'})
        self.assertEqual(status, 200)
        status, second, _ = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'transport-test', 'room_id': room['id'], 'host_identity': 'transport-host'})
        self.assertEqual(status, 200)
        self.assertNotEqual(first['task']['id'], second['task']['id'])
        self.assertEqual(second['execution']['agent_session']['id'], sid)
        status, page, _ = self.request('GET', '/api/hub/agent-sessions/' + sid + '?after=0&limit=1')
        self.assertEqual(status, 200)
        self.assertEqual(page['turns'][0]['task_id'], first['task']['id'])
        self.assertTrue(page['has_more'])
        status, next_page, _ = self.request('GET', '/api/hub/agent-sessions/' + sid + '?after=' + str(page['cursor']) + '&limit=1')
        self.assertEqual(status, 200)
        self.assertEqual(next_page['turns'][0]['task_id'], second['task']['id'])
        self.assertFalse(next_page['has_more'])
        encoded = json.dumps([listing, page, next_page])
        for secret in (first['lease_token'], second['lease_token'], 'lease_token', 'session_hash'):
            self.assertNotIn(secret, encoded)
        for query in ('after=-1', 'after=no', 'limit=0', 'limit=101', 'limit=1&limit=2'):
            self.assertEqual(self.request('GET', '/api/hub/agent-sessions/' + sid + '?' + query)[0], 400)

    def test_read_scope_viewer_and_other_workspace_use_actual_http_boundaries(self):
        room, claim, sid = self.first_turn()
        _, cookie = self.http.setup_owner()
        read = self.http.make_token(cookie, ['read'])['token']
        execute = self.http.make_token(cookie, ['execute'])['token']
        _, viewer = self.http.accept(self.http.invite(cookie, role='viewer'))
        paths = ('/api/hub/rooms/' + room['id'] + '/agent-sessions', '/api/hub/agent-sessions/' + sid)
        for path in paths:
            self.assertEqual(self.request('GET', path, token=read)[0], 200)
            self.assertEqual(self.request('GET', path, cookie=viewer)[0], 200)
            self.assertEqual(self.request('GET', path, token=execute)[0], 403)
            self.assertEqual(self.request('GET', path)[0], 401)
        self.assertEqual(self.request('POST', '/api/hub/rooms/' + room['id'] + '/messages', {'text': 'Viewer must not execute'}, cookie=viewer)[0], 403)
        self.assertEqual(self.request('POST', '/api/account/workspaces', {'name': 'Separate scope'}, cookie=cookie)[0], 201)
        for path in paths:
            self.assertEqual(self.request('GET', path, cookie=cookie)[0], 404)
            self.assertEqual(self.request('GET', path, token=read)[0], 200)


if __name__ == '__main__':
    unittest.main()
