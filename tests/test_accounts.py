import concurrent.futures
import contextlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from studio import accounts, core, hub
from studio.server import Handler


class AccountsTests(unittest.TestCase):
    password = 'correct-horse-battery-731'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, body=None, cookie=None, token=None, origin=None):
        headers = {}
        if cookie:
            headers['Cookie'] = cookie
        if token:
            headers['Authorization'] = f'Bearer {token}'
        if origin:
            headers['Origin'] = origin
        raw = None
        if body is not None:
            headers['Content-Type'] = 'application/json'
            raw = json.dumps(body).encode('utf-8')
        conn = HTTPConnection('127.0.0.1', self.server.server_port, timeout=15)
        try:
            conn.request(method, path, body=raw, headers=headers)
            response = conn.getresponse()
            content = response.read()
            data = json.loads(content) if 'application/json' in response.getheader('Content-Type', '') else content.decode('utf-8')
            return response.status, data, dict(response.getheaders())
        finally:
            conn.close()

    def setup_owner(self, email='owner@example.test'):
        status, info, headers = self.request('POST', '/api/account/setup', {'email': email, 'name': '所有者', 'password': self.password})
        self.assertEqual(status, 200, info)
        return info, headers['Set-Cookie'].split(';', 1)[0]

    def invite(self, owner_cookie, email='member@example.test', role='editor'):
        status, data, _ = self.request('POST', '/api/account/invitations', {'email': email, 'role': role}, cookie=owner_cookie)
        self.assertEqual(status, 201, data)
        return data

    def accept(self, invitation, email='member@example.test', name='协作者', password=None):
        status, data, headers = self.request('POST', '/api/account/invitations/accept', {'token': invitation['token'], 'email': email, 'name': name, 'password': password or self.password})
        self.assertEqual(status, 200, data)
        return data, headers['Set-Cookie'].split(';', 1)[0]

    def make_token(self, cookie, scopes):
        status, data, _ = self.request('POST', '/api/account/tokens', {'name': '测试凭据', 'scopes': scopes}, cookie=cookie)
        self.assertEqual(status, 201, data)
        return data

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def test_local_transition_initialization_race_keeps_exactly_one_owner_and_data(self):
        local_room = hub.create_room('初始化前的内容')
        status, info, _ = self.request('GET', '/api/account/session')
        self.assertEqual(status, 200)
        self.assertEqual(info['mode'], 'local')
        self.assertTrue(info['setup_available'])

        def initialize(index):
            return self.request('POST', '/api/account/setup', {'email': f'owner{index}@example.test', 'name': f'用户 {index}', 'password': self.password})

        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            responses = list(pool.map(initialize, range(3)))
        self.assertEqual([r[0] for r in responses].count(200), 1)
        self.assertEqual([r[0] for r in responses].count(409), 2)
        cookie = next(r[2]['Set-Cookie'].split(';', 1)[0] for r in responses if r[0] == 200)
        with contextlib.closing(accounts.db()) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM users').fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM members WHERE role='owner'").fetchone()[0], 1)
        self.assertTrue(accounts.has_users())
        self.assertEqual(self.request('GET', '/api/hub/bootstrap')[0], 401)
        self.assertEqual(self.request('GET', f'/api/hub/rooms/{local_room["id"]}', cookie=cookie)[0], 200)

    def test_workspace_storage_isolation_and_scoped_tokens(self):
        initial, cookie = self.setup_owner()
        first_id = initial['workspace']['id']
        _, room_a, _ = self.request('POST', '/api/hub/rooms', {'text': '空间 A 的专属内容'}, cookie=cookie)
        token_a = self.make_token(cookie, ['read'])['token']
        status, workspace_b, _ = self.request('POST', '/api/account/workspaces', {'name': '空间 B'}, cookie=cookie)
        self.assertEqual(status, 201)
        self.assertEqual(self.request('GET', f'/api/hub/rooms/{room_a["id"]}', cookie=cookie)[0], 404)
        status, room_b, _ = self.request('POST', '/api/hub/rooms', {'text': '空间 B 的专属内容'}, cookie=cookie)
        self.assertEqual(status, 201)
        self.assertEqual(self.request('GET', f'/api/hub/rooms/{room_a["id"]}', token=token_a)[0], 200)
        self.assertEqual(self.request('GET', f'/api/hub/rooms/{room_b["id"]}', token=token_a)[0], 404)
        self.assertEqual(self.request('GET', '/api/account/session', token=token_a)[0], 403)
        self.assertEqual(self.request('POST', f'/api/account/workspaces/{first_id}/select', {}, cookie=cookie)[0], 200)
        self.assertEqual(self.request('GET', f'/api/hub/rooms/{room_b["id"]}', cookie=cookie)[0], 404)
        self.assertTrue((Path(self.temp.name) / 'studio.sqlite3').exists())
        self.assertTrue((Path(self.temp.name) / 'workspaces' / workspace_b['id'] / 'studio.sqlite3').exists())
        self.assertEqual(core.data_root(), core.base_data_root(), 'HTTP request binding must not leak into other execution contexts')
        principal = accounts.identify({'Cookie': cookie})
        principal['workspace']['storage_key'] = '..'
        self.assert_error(403, accounts.data_directory, principal)

    def test_viewer_scopes_and_revocation_take_effect_on_sessions_and_tokens(self):
        owner, owner_cookie = self.setup_owner()
        member, member_cookie = self.accept(self.invite(owner_cookie, role='viewer'))
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=member_cookie)[0], 200)
        self.assertEqual(self.request('POST', '/api/hub/rooms', {'text': '不应写入'}, cookie=member_cookie)[0], 403)
        self.assertEqual(self.request('GET', '/api/account/members', cookie=member_cookie)[0], 403)
        self.assertEqual(self.request('POST', '/api/account/tokens', {'name': '越权', 'scopes': ['execute']}, cookie=member_cookie)[0], 403)
        read_token = self.make_token(member_cookie, ['read'])['token']
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', token=read_token)[0], 200)
        self.assertEqual(self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'x'}, token=read_token)[0], 403)
        self.assertEqual(self.request('GET', '/api/account/tokens', token=read_token)[0], 403)
        cached = accounts.identify({'Cookie': member_cookie})
        status, _, _ = self.request('DELETE', f'/api/account/members/{member["user"]["id"]}', {}, cookie=owner_cookie)
        self.assertEqual(status, 200)
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=member_cookie)[0], 403)
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', token=read_token)[0], 403)
        self.assert_error(403, accounts.authorize, cached, 'GET', '/api/hub/bootstrap')
        status, session, _ = self.request('GET', '/api/account/session', cookie=member_cookie)
        self.assertEqual(status, 200)
        self.assertIsNone(session['workspace'])
        status, personal, _ = self.request('POST', '/api/account/workspaces', {'name': '自己的空间'}, cookie=member_cookie)
        self.assertEqual(status, 201, personal)
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=member_cookie)[0], 200)

    def test_role_downgrade_is_immediate_and_owner_cannot_be_removed(self):
        owner, owner_cookie = self.setup_owner()
        member, member_cookie = self.accept(self.invite(owner_cookie))
        execute_token = self.make_token(member_cookie, ['read', 'write', 'execute'])['token']
        cached = accounts.identify({'Cookie': member_cookie})
        self.assertEqual(self.request('POST', '/api/hub/rooms', {'text': '允许创建'}, token=execute_token)[0], 201)
        self.assertEqual(self.request('PUT', f'/api/account/members/{member["user"]["id"]}', {'role': 'viewer'}, cookie=owner_cookie)[0], 200)
        self.assertEqual(self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'worker'}, token=execute_token)[0], 403)
        self.assert_error(403, accounts.authorize, cached, 'POST', '/api/hub/rooms')
        self.assertEqual(self.request('DELETE', f'/api/account/members/{owner["user"]["id"]}', {}, cookie=owner_cookie)[0], 409)
        self.assertEqual(self.request('PUT', f'/api/account/members/{owner["user"]["id"]}', {'role': 'viewer'}, cookie=owner_cookie)[0], 409)

    def test_write_and_execute_scopes_are_separate_and_tokens_are_revocable(self):
        _, cookie = self.setup_owner()
        read = self.make_token(cookie, ['read'])
        write = self.make_token(cookie, ['write'])
        execute = self.make_token(cookie, ['execute'])
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', token=write['token'])[0], 403)
        status, room, _ = self.request('POST', '/api/hub/rooms', {'text': '执行任务'}, token=write['token'])
        self.assertEqual(status, 201)
        self.assertEqual(self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'scope-worker'}, token=write['token'])[0], 403)
        status, claimed, _ = self.request('POST', '/api/hub/tasks/claim', {'worker_id': 'scope-worker'}, token=execute['token'])
        self.assertEqual(status, 200)
        self.assertIsNotNone(claimed['task'])
        self.assertEqual(self.request('POST', '/api/hub/rooms', {'text': '不该创建'}, token=execute['token'])[0], 403)
        self.assertEqual(self.request('POST', '/api/account/logout', {}, token=execute['token'])[0], 403)
        _, listed, _ = self.request('GET', '/api/account/tokens', cookie=cookie)
        serialized = json.dumps(listed)
        self.assertNotIn(read['token'], serialized)
        self.assertNotIn('token_hash', serialized)
        cached = accounts.identify({'Authorization': f'Bearer {read["token"]}'})
        self.assertEqual(self.request('DELETE', f'/api/account/tokens/{read["record"]["id"]}', {}, cookie=cookie)[0], 200)
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', token=read['token'])[0], 401)
        self.assert_error(401, accounts.authorize, cached, 'GET', '/api/hub/bootstrap')

    def test_invitation_cannot_reset_existing_account_or_reuse_consumed_token(self):
        _, owner_cookie = self.setup_owner()
        member, member_cookie = self.accept(self.invite(owner_cookie))
        _, second, _ = self.request('POST', '/api/account/workspaces', {'name': '第二个空间'}, cookie=owner_cookie)
        invitation = self.invite(owner_cookie)
        with contextlib.closing(accounts.db()) as conn:
            original = conn.execute('SELECT name,password_hash FROM users WHERE id=?', (member['user']['id'],)).fetchone()
            invitation_row = conn.execute('SELECT token_hash FROM invitations WHERE id=?', (invitation['invitation']['id'],)).fetchone()
            self.assertNotEqual(invitation_row['token_hash'], invitation['token'])
        attempted = {'token': invitation['token'], 'email': 'member@example.test', 'name': '被劫持的名称', 'password': 'attacker-password-123'}
        status, error, _ = self.request('POST', '/api/account/invitations/accept', attempted)
        self.assertEqual(status, 401)
        self.assertEqual(error, {'error': '邮箱或密码不正确'})
        with contextlib.closing(accounts.db()) as conn:
            self.assertEqual(tuple(conn.execute('SELECT name,password_hash FROM users WHERE id=?', (member['user']['id'],)).fetchone()), tuple(original))
            self.assertIsNone(conn.execute('SELECT 1 FROM members WHERE user_id=? AND workspace_id=?', (member['user']['id'], second['id'])).fetchone())
        accepted, _ = self.accept(invitation, name='不会覆盖已有名称')
        self.assertEqual(accepted['user']['name'], '协作者')
        self.assertEqual(accepted['workspace']['id'], second['id'])
        attempted['password'] = self.password
        self.assertEqual(self.request('POST', '/api/account/invitations/accept', attempted)[0], 400)
        revoked = self.invite(owner_cookie, 'someone@example.test')
        self.assertEqual(self.request('DELETE', f'/api/account/invitations/{revoked["invitation"]["id"]}', {}, cookie=owner_cookie)[0], 200)
        self.assertEqual(self.request('POST', '/api/account/invitations/accept', {'token': revoked['token'], 'email': 'someone@example.test', 'name': '新人', 'password': self.password})[0], 400)

    def test_revoked_selected_workspace_can_switch_to_another_membership(self):
        owner, owner_cookie = self.setup_owner()
        member, member_cookie = self.accept(self.invite(owner_cookie))
        _, second, _ = self.request('POST', '/api/account/workspaces', {'name': '成员自己的空间'}, cookie=member_cookie)
        self.assertEqual(self.request('POST', f'/api/account/workspaces/{owner["workspace"]["id"]}/select', {}, cookie=member_cookie)[0], 200)
        self.assertEqual(self.request('DELETE', f'/api/account/members/{member["user"]["id"]}', {}, cookie=owner_cookie)[0], 200)
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=member_cookie)[0], 403)
        status, selected, _ = self.request('POST', f'/api/account/workspaces/{second["id"]}/select', {}, cookie=member_cookie)
        self.assertEqual(status, 200, selected)
        self.assertEqual(selected['workspace']['id'], second['id'])
        self.assertEqual(self.request('POST', f'/api/account/workspaces/{owner["workspace"]["id"]}/select', {}, cookie=member_cookie)[0], 403)

    def test_login_rate_limit_password_hash_and_generic_errors(self):
        self.setup_owner()
        with contextlib.closing(accounts.db()) as conn:
            encoded = conn.execute('SELECT password_hash FROM users').fetchone()[0]
        self.assertTrue(encoded.startswith('scrypt:32768:8:3:'))
        self.assertNotIn(self.password, encoded)
        self.assertTrue(accounts.verify_password(self.password, encoded))
        self.assertNotEqual(accounts.password_hash(self.password), encoded)
        self.assertFalse(accounts.verify_password(self.password, 'malformed'))
        self.assertFalse(accounts.verify_password('\ud800' * 12, encoded))
        errors = []
        for _ in range(8):
            status, error, _ = self.request('POST', '/api/account/login', {'email': 'owner@example.test', 'password': 'incorrect-password'})
            self.assertEqual(status, 401)
            errors.append(error)
        self.assertTrue(all(error == {'error': '邮箱或密码不正确'} for error in errors))
        self.assertEqual(self.request('POST', '/api/account/login', {'email': 'owner@example.test', 'password': self.password})[0], 429)
        with contextlib.closing(accounts.db()) as conn:
            self.assertNotIn('owner@example.test', json.dumps([dict(row) for row in conn.execute('SELECT * FROM login_attempts')]))
            conn.execute("UPDATE login_attempts SET expires_at='2000-01-01T00:00:00.000000+00:00'")
            conn.commit()
        self.assertEqual(self.request('POST', '/api/account/login', {'email': 'owner@example.test', 'password': self.password})[0], 200)

    def test_cookie_flags_logout_idle_expiry_and_same_origin_guard(self):
        _, cookie = self.setup_owner()
        flags = accounts.cookie(cookie.split('=', 1)[1])['Set-Cookie']
        self.assertIn('HttpOnly', flags)
        self.assertIn('SameSite=Strict', flags)
        self.assertNotIn('; Secure', flags)
        with patch.dict(os.environ, {'STUDIO_PUBLIC_ORIGIN': 'https://workspace.example.test'}):
            self.assertIn('; Secure', accounts.cookie('safe-token')['Set-Cookie'])
        self.assert_error(400, accounts.cookie, 'bad\r\nInjected: yes')
        self.assertEqual(self.request('POST', '/api/hub/rooms', {'text': '跨站尝试'}, cookie=cookie, origin='https://attacker.example')[0], 403)
        cached = accounts.identify({'Cookie': cookie})
        status, _, headers = self.request('POST', '/api/account/logout', {}, cookie=cookie)
        self.assertEqual(status, 200)
        self.assertIn('Max-Age=0', headers['Set-Cookie'])
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=cookie)[0], 401)
        self.assert_error(401, accounts.authorize, cached, 'GET', '/api/hub/bootstrap')
        _, _, headers = self.request('POST', '/api/account/login', {'email': 'owner@example.test', 'password': self.password})
        new_cookie = headers['Set-Cookie'].split(';', 1)[0]
        with contextlib.closing(accounts.db()) as conn:
            conn.execute("UPDATE sessions SET last_seen_at='2000-01-01T00:00:00.000000+00:00'")
            conn.commit()
        self.assertEqual(self.request('GET', '/api/hub/bootstrap', cookie=new_cookie)[0], 401)

    def test_required_mode_public_setup_and_audit_redaction(self):
        with patch.dict(os.environ, {'STUDIO_AUTH_MODE': 'required'}):
            self.assertEqual(self.request('GET', '/api/hub/bootstrap')[0], 401)
            self.assertEqual(self.request('GET', '/api/account/session')[1]['mode'], 'authenticated')
        with patch.dict(os.environ, {'STUDIO_PUBLIC_ORIGIN': 'https://workspace.example.test'}):
            self.assertFalse(accounts.session_info(None)['setup_available'])
            self.assert_error(403, accounts.setup, {'email': 'owner@example.test', 'name': '所有者', 'password': self.password})
        _, cookie = self.setup_owner()
        secret = 'never-store-this-body-or-token'
        self.assertEqual(self.request('POST', '/api/hub/rooms', {'text': secret}, cookie=cookie)[0], 201)
        principal = accounts.identify({'Cookie': cookie})
        accounts.audit(principal, 'POST', f'/api/hub/workers/{secret}/ping?token={secret}', 403)
        status, audit, _ = self.request('GET', '/api/account/audit', cookie=cookie)
        self.assertEqual(status, 200)
        self.assertNotIn(secret, json.dumps(audit))
        self.assertNotIn(cookie.split('=', 1)[1], json.dumps(audit))
        self.assertTrue(any(event['path'] == '/api/hub/workers/:id/ping' for event in audit['events']))
        self.assertTrue(any(event['path'] == '/api/hub/rooms' and event['status'] == 201 for event in audit['events']))


if __name__ == '__main__':
    unittest.main()
