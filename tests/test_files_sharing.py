import base64
import contextlib
import hashlib
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import zipfile

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from studio import accounts, core, file_extract, files, hub, hub_extensions, hub_http, search, sharing
from studio.server import Handler


class FilesSharingTests(unittest.TestCase):
    password = 'file-sharing-fixture-password-983'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': str(self.root), 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        data_binding = core.REQUEST_DATA_ROOT.set(None)
        actor_binding = core.REQUEST_ACTOR.set(None)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, data_binding)
        self.addCleanup(core.REQUEST_ACTOR.reset, actor_binding)

    def room(self):
        return hub.create_room('请处理提供的资料', agent_id='writer')

    def upload(self, room, name, raw):
        return files.add_file(room['id'], name, base64.b64encode(raw).decode('ascii'))

    def artifact(self, room, content='第一版公开内容', kind='markdown'):
        claim = hub.claim_task('fixture-worker', room_id=room['id'])
        room = hub.complete_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], {'message': '已提交成果', 'artifacts': [{'title': '可分享成果', 'kind': kind, 'content': content}]})
        return room['artifacts'][0]

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def pdf(self, text=None, encrypt=False):
        writer = PdfWriter()
        page = writer.add_blank_page(width=300, height=200)
        if text is not None:
            font = DictionaryObject({NameObject('/Type'): NameObject('/Font'), NameObject('/Subtype'): NameObject('/Type1'), NameObject('/BaseFont'): NameObject('/Helvetica')})
            page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'): DictionaryObject({NameObject('/F1'): writer._add_object(font)})})
            stream = DecodedStreamObject()
            stream.set_data(f'BT /F1 12 Tf 20 100 Td ({text}) Tj ET'.encode('ascii'))
            page[NameObject('/Contents')] = writer._add_object(stream)
        if encrypt:
            writer.encrypt('fixture-secret')
        output = io.BytesIO()
        writer.write(output)
        return output.getvalue()

    def docx(self, xml=None, extra=None, compression=zipfile.ZIP_STORED):
        if xml is None:
            xml = '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>真实 DOCX 正文</w:t><w:tab/><w:t>第二列</w:t><w:br/><w:t>下一行</w:t></w:r></w:p></w:body></w:document>'.encode('utf-8')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', compression=compression) as zipped:
            zipped.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            zipped.writestr('word/document.xml', xml)
            for name, content in extra or []:
                zipped.writestr(name, content)
        return output.getvalue()

    def test_text_keeps_exact_original_hash_context_and_revocation(self):
        room = self.room()
        original = b'\xef\xbb\xbf' + '# 原始资料\r\n\n正文  \n'.encode('utf-8')
        uploaded = self.upload(room, '资料.md', original)
        self.assertEqual(uploaded['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(uploaded['bytes'], len(original))
        self.assertEqual(uploaded['extraction_status'], 'extracted')
        self.assertEqual(files.get_file(uploaded['id'], with_bytes=True)[1], original)
        status, downloaded, mime, headers = hub_http.dispatch('GET', uploaded['download_url'])
        self.assertEqual(status, 200)
        self.assertEqual(downloaded, original)
        self.assertIn("filename*=UTF-8''", headers['Content-Disposition'])
        headers['Content-Disposition'].encode('ascii')
        claim = hub.claim_task('fixture-worker', room_id=room['id'])
        source = next(item for item in claim['context']['sources'] if item['id'] == uploaded['source_id'])
        self.assertIn(uploaded['sha256'], source['content'])
        self.assertIn('# 原始资料\r\n\n正文  \n', source['content'])
        hub.complete_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], {'message': '处理完成'})
        revoked = files.revoke_file(uploaded['id'])
        self.assertIsNotNone(revoked['revoked_at'])
        self.assert_error(410, files.get_file, uploaded['id'], True)
        self.assertFalse(hub.list_sources(room['id'])['sources'])
        hub.add_message(room['id'], '继续', 'writer')
        new_claim = hub.claim_task('fixture-worker', room_id=room['id'])
        self.assertFalse(new_claim['context']['sources'])

    def test_real_pdf_text_blank_pdf_and_encrypted_pdf_use_bounded_child(self):
        room = self.room()
        real = self.pdf('Verified PDF content 719')
        uploaded = self.upload(room, 'report.pdf', real)
        self.assertEqual(uploaded['extraction_status'], 'extracted', uploaded)
        self.assertIn('Verified PDF content 719', hub.get_source(uploaded['source_id'])['content'])
        self.assertEqual(files.get_file(uploaded['id'], True)[1], real)
        blank = self.upload(room, 'scan.pdf', self.pdf())
        self.assertEqual(blank['extraction_status'], 'no_text')
        self.assertIn('OCR', blank['warning'])
        encrypted = self.upload(room, 'locked.pdf', self.pdf('Hidden text', encrypt=True))
        self.assertEqual(encrypted['extraction_status'], 'encrypted')
        self.assertNotIn('Hidden text', hub.get_source(encrypted['source_id'])['content'])

    def test_actual_extractor_process_rejects_allocation_above_memory_limit(self):
        code = "from studio.file_extract import memory_limit\nmemory_limit()\ntry:\n    payload = bytearray(600 * 1024 * 1024)\nexcept MemoryError:\n    print('memory-limit-enforced')\nelse:\n    raise SystemExit(3)"
        flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        result = subprocess.run([sys.executable, '-X', 'utf8', '-c', code], cwd=core.ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10, **flags)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf-8', errors='replace'))
        self.assertIn(b'memory-limit-enforced', result.stdout)

    def test_docx_text_tabs_and_breaks_and_unsafe_zip_or_dtd_are_not_executed(self):
        room = self.room()
        good = self.upload(room, 'document.docx', self.docx())
        self.assertEqual(good['extraction_status'], 'extracted', good)
        self.assertIn('真实 DOCX 正文\t第二列\n下一行', hub.get_source(good['source_id'])['content'])
        dtd = '<?xml version="1.0" encoding="UTF-16"?><!DOCTYPE w:document [<!ENTITY secret "SHOULD_NOT_EXPAND">]><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&secret;</w:t></w:r></w:p></w:body></w:document>'.encode('utf-16')
        link = zipfile.ZipInfo('word/linked.xml')
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        malicious = [self.docx(xml=dtd), self.docx(extra=[('../escape.txt', b'not written')]), self.docx(extra=[(link, b'../../outside')]), self.docx(extra=[('padding.bin', b'x' * 1024 * 1024)], compression=zipfile.ZIP_DEFLATED)]
        for index, raw in enumerate(malicious):
            with self.subTest(index=index):
                uploaded = self.upload(room, f'unsafe-{index}.docx', raw)
                self.assertEqual(uploaded['extraction_status'], 'failed')
                self.assertEqual(files.get_file(uploaded['id'], True)[1], raw)
                self.assertNotIn('SHOULD_NOT_EXPAND', hub.get_source(uploaded['source_id'])['content'])
        self.assertFalse((self.root / 'escape.txt').exists())

    def test_resource_failures_preserve_original_and_extractor_queue_is_bounded(self):
        room = self.room()
        raw = self.pdf('Resource fixture')
        errors = [OSError('fixture spawn failure'), subprocess.TimeoutExpired('fixture', 20), MemoryError('fixture memory failure')]
        for index, error in enumerate(errors):
            with self.subTest(error=type(error).__name__), patch.object(files.subprocess, 'run', side_effect=error):
                uploaded = self.upload(room, f'failed-{index}.pdf', raw)
                self.assertEqual(uploaded['extraction_status'], 'failed')
                self.assertEqual(files.get_file(uploaded['id'], True)[1], raw)

        def malformed(*args, **kwargs):
            kwargs['stdout'].write(b'{"text":null,"status":"extracted"}')
            return subprocess.CompletedProcess(args[0], 0)

        with patch.object(files.subprocess, 'run', side_effect=malformed):
            self.assertEqual(self.upload(room, 'malformed.pdf', raw)['extraction_status'], 'failed')
        files._extractors.acquire()
        files._extractors.acquire()
        try:
            self.assert_error(503, self.upload, room, 'busy.pdf', raw)
        finally:
            files._extractors.release()
            files._extractors.release()
        self.assertEqual(self.upload(room, 'after-recovery.pdf', raw)['extraction_status'], 'extracted')

    def test_image_has_no_invented_ocr_and_invalid_inputs_leave_no_files(self):
        room = self.room()
        png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a5VIAAAAASUVORK5CYII=')
        image = self.upload(room, 'pixel.png', png)
        self.assertEqual(image['extraction_status'], 'image_only')
        self.assertIn('未执行 OCR', image['warning'])
        self.assertEqual(hub.get_source(image['source_id'])['content'].split('\n\n', 1)[-1], '')
        invalid = [('..\\escape.txt', b'no'), ('../escape.txt', b'no'), ('header\r\n.txt', b'no'), ('fake.png', b'not a png'), ('bad.json', b'NaN'), ('nested.json', b'[' * 3000 + b']' * 3000), ('bad.txt', b'\xff\xfe')]
        for filename, raw in invalid:
            with self.subTest(filename=filename):
                self.assert_error(400, self.upload, room, filename, raw)
        self.assert_error(400, files.add_file, room['id'], 'bad.txt', 'not base64')
        self.assertEqual(len(files.list_files(room['id'])['files']), 1)

    def test_search_uses_upload_body_and_active_latest_records_only(self):
        room = self.room()
        uploaded = self.upload(room, 'contract.txt', b'Visible contract wording with 100%_coverage.')
        result = next(item for item in search.find('Visible')['results'] if item['type'] == 'source')
        self.assertNotIn('SHA256', result['snippet'])
        self.assertNotIn('/api/hub/files/', result['snippet'])
        self.assertTrue(any(item['id'] == uploaded['source_id'] for item in search.find('%_')['results']))
        self.assertFalse(search.find('SHA256')['results'])
        self.assertFalse(search.find('/api/hub/files/')['results'])
        artifact = self.artifact(room, 'old-artifact-marker')
        hub.edit_artifact(artifact['id'], 1, '成果', 'text', 'new-artifact-marker')
        self.assertFalse(any(item['type'] == 'artifact' for item in search.find('old-artifact-marker')['results']))
        skill = hub_extensions.create_skill('技能', '', 'archived-skill-marker')
        hub_extensions.archive_skill(skill['id'], 1)
        self.assertFalse(search.find('archived-skill-marker')['results'])
        files.revoke_file(uploaded['id'])
        self.assertFalse(any(item['id'] == uploaded['source_id'] for item in search.find('Visible')['results']))

    def test_shares_pin_versions_hide_tokens_and_support_expiry_and_revocation(self):
        room = self.room()
        hub.update_context(room['id'], '私密会话背景不应发布')
        artifact = self.artifact(room, '固定的第一版')
        created = sharing.create_share(artifact['id'], 1)
        hub.edit_artifact(artifact['id'], 1, '后来修改', 'text', '不应改变已分享内容的第二版')
        public = sharing.public_content(created['token']).decode('utf-8')
        self.assertIn('固定的第一版', public)
        self.assertNotIn('第二版', public)
        self.assertNotIn('私密会话背景', public)
        listed = sharing.list_shares(artifact['id'])
        self.assertNotIn(created['token'], json.dumps(listed))
        self.assertNotIn('token_hash', json.dumps(listed))
        self.assert_error(400, sharing.create_share, artifact['id'], 1, 31)
        with contextlib.closing(sharing._db()) as conn:
            conn.execute("UPDATE artifact_shares SET expires_at='2000-01-01T00:00:00.000000+00:00' WHERE id=?", (created['share']['id'],))
            conn.commit()
        self.assert_error(404, sharing.public_content, created['token'])
        second = sharing.create_share(artifact['id'], 2)
        sharing.revoke_share(artifact['id'], second['share']['id'])
        self.assert_error(404, sharing.public_content, second['token'])
        self.assert_error(404, sharing.public_content, '\ud800')

    def test_files_search_and_share_management_are_workspace_scoped(self):
        _, headers = accounts.setup({'email': 'owner@example.test', 'name': '所有者', 'password': self.password})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        principal = accounts.identify({'Cookie': cookie})
        room_a = self.room()
        uploaded_a = self.upload(room_a, 'tenant-a.txt', b'tenant-only-marker')
        artifact_a = self.artifact(room_a)
        share_a = sharing.create_share(artifact_a['id'], 1)
        accounts.dispatch('POST', '/api/account/workspaces', {'name': '第二空间'}, principal)
        other = accounts.identify({'Cookie': cookie})
        binding = core.REQUEST_DATA_ROOT.set(accounts.data_directory(other))
        try:
            self.assertFalse(search.find('tenant-only-marker')['results'])
            self.assert_error(404, files.get_file, uploaded_a['id'], True)
            self.assert_error(404, sharing.list_shares, artifact_a['id'])
            self.assert_error(404, sharing.revoke_share, artifact_a['id'], share_a['share']['id'])
            room_b = self.room()
            artifact_b = self.artifact(room_b)
            self.assertEqual(sharing.list_shares(artifact_b['id'])['shares'], [])
        finally:
            core.REQUEST_DATA_ROOT.reset(binding)
        self.assertTrue(sharing.public_content(share_a['token']))

    def test_public_http_response_has_fixed_origin_csp_and_no_account_details(self):
        _, headers = accounts.setup({'email': 'secret-owner@example.test', 'name': '私密所有者名称', 'password': self.password})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        room = self.room()
        artifact = self.artifact(room, '<h1>Public snapshot</h1><script>fetch("/api/account/session")</script>', kind='html')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def request(method, path, body=None, supplied_cookie=None, token=None):
            conn = HTTPConnection('127.0.0.1', server.server_port, timeout=10)
            request_headers = {'Content-Type': 'application/json'}
            if supplied_cookie:
                request_headers['Cookie'] = supplied_cookie
            if token:
                request_headers['Authorization'] = 'Bearer ' + token
            try:
                conn.request(method, path, json.dumps(body).encode() if body is not None else None, request_headers)
                response = conn.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally:
                conn.close()

        try:
            status, raw, _ = request('POST', f'/api/hub/artifacts/{artifact["id"]}/shares', {'version': 1}, cookie)
            self.assertEqual(status, 201)
            created = json.loads(raw)
            self.assertTrue(created['url'].startswith(f'http://127.0.0.1:{server.server_port}/share/'), created['url'])
            status, public, response_headers = request('GET', '/share/' + created['token'])
            self.assertEqual(status, 200)
            csp = response_headers['Content-Security-Policy']
            self.assertTrue(csp.startswith('sandbox;'))
            self.assertIn("default-src 'none'", csp)
            self.assertNotIn('allow-scripts', csp)
            self.assertNotIn('Set-Cookie', response_headers)
            self.assertNotIn(b'secret-owner@example.test', public)
            self.assertNotIn(self.password.encode(), public)
            self.assertNotIn(cookie.split('=', 1)[1].encode(), public)
            self.assertEqual(request('GET', '/api/hub/bootstrap')[0], 401)
            owner = accounts.identify({'Cookie': cookie})
            invitation = accounts.dispatch('POST', '/api/account/invitations', {'email': 'viewer@example.test', 'role': 'viewer'}, owner)[1]
            _, viewer_headers = accounts.accept_invitation({'token': invitation['token'], 'email': 'viewer@example.test', 'name': '只读成员', 'password': self.password})
            viewer_cookie = viewer_headers['Set-Cookie'].split(';', 1)[0]
            self.assertEqual(request('GET', f'/api/hub/artifacts/{artifact["id"]}/shares', supplied_cookie=viewer_cookie)[0], 200)
            self.assertEqual(request('POST', f'/api/hub/artifacts/{artifact["id"]}/shares', {'version': 1}, viewer_cookie)[0], 403)
            self.assertEqual(request('DELETE', f'/api/hub/artifacts/{artifact["id"]}/shares/{created["share"]["id"]}', {}, viewer_cookie)[0], 403)
            read_token = accounts.dispatch('POST', '/api/account/tokens', {'name': '只读凭据', 'scopes': ['read']}, owner)[1]['token']
            self.assertEqual(request('POST', f'/api/hub/artifacts/{artifact["id"]}/shares', {'version': 1}, token=read_token)[0], 403)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()
