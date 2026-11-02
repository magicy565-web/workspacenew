import base64
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

from studio import core
from studio.server import Handler

ROOT = Path(__file__).resolve().parents[1]


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name})
        self.env.start()
        self.brief = json.loads((ROOT / 'examples' / 'example-brief.json').read_text(encoding='utf-8'))
        self.image = base64.b64encode((ROOT / 'examples' / 'example.png').read_bytes()).decode('ascii')
        self.project = core.create_project('Example', 'https://example.com')
        self.pid = self.project['id']

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def prepare(self):
        core.save_image(self.pid, 'example.png', self.image)
        core.save_brief(self.pid, self.brief)

    def test_real_renderer_build_preview_download_and_stale_status(self):
        self.prepare()
        result = core.build_project(self.pid)
        self.assertEqual(result['checks']['chapters'], 4)
        self.assertTrue(result['project']['build_current'])
        html = core.preview_file(self.pid, 'index.html').read_text(encoding='utf-8')
        self.assertIn('Fictional Example', html)
        with zipfile.ZipFile(io.BytesIO(core.download_zip(self.pid))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertIn('images/example.png', archive.namelist())
            self.assertIn('source-manifest.json', archive.namelist())
        self.brief['title'] = 'Updated'
        result = core.save_brief(self.pid, self.brief)
        self.assertFalse(result['build_current'])
        self.assertTrue(result['has_build'])
        self.assertIn('Fictional Example', core.preview_file(self.pid, 'index.html').read_text(encoding='utf-8'))

    def test_project_instructions_are_persisted_and_mark_old_build_stale(self):
        self.prepare()
        core.build_project(self.pid)
        saved = core.add_instruction(self.pid, '请突出产品材料与用途。')
        self.assertEqual(saved['messages'][0]['text'], '请突出产品材料与用途。')
        self.assertFalse(saved['build_current'])
        self.assertEqual(core.get_project(self.pid)['messages'][0]['text'], '请突出产品材料与用途。')

    def test_design_selection_changes_rendered_theme_and_cover(self):
        self.brief['design'] = {'theme': 'modern'}
        self.brief['chapters'][0]['layout'] = 'full_bleed'
        self.prepare()
        core.build_project(self.pid)
        html = core.preview_file(self.pid, 'index.html').read_text(encoding='utf-8')
        css = core.preview_file(self.pid, 'editorial.css').read_text(encoding='utf-8')
        self.assertIn('data-theme="modern"', html)
        self.assertIn('class="chapter cover cover--bleed"', html)
        self.assertIn('body[data-theme="modern"]', css)
        self.brief['design']['theme'] = 'not-a-theme'
        with self.assertRaises(ValueError): core.save_brief(self.pid, self.brief)

    def test_concurrent_edit_does_not_publish_build_as_current(self):
        self.prepare()
        original = core.renderer.main
        def change_during_build(args):
            original(args)
            changed = dict(self.brief, title='Changed during build')
            core.save_brief(self.pid, changed)
        with patch.object(core.renderer, 'main', side_effect=change_during_build):
            with self.assertRaisesRegex(ValueError, '发生变化'):
                core.build_project(self.pid)
        self.assertFalse(core.get_project(self.pid)['has_build'])

    def test_incomplete_or_unsafe_inputs_are_rejected(self):
        with self.assertRaises(ValueError): core.create_project('Local', 'http://127.0.0.1/')
        with self.assertRaises(ValueError): core.save_image(self.pid, '../example.png', self.image)
        with self.assertRaises(ValueError): core.save_image(self.pid, 'bad.png', base64.b64encode(b'<script>').decode())
        with self.assertRaises(ValueError): core.save_brief(self.pid, {'brand': 'incomplete'})
        core.save_brief(self.pid, self.brief)
        with self.assertRaisesRegex(ValueError, '缺少图片'): core.build_project(self.pid)

    def test_user_text_is_escaped_and_generic_layout_does_not_invent_claims(self):
        self.brief['chapters'][0]['lead'] = '<script>alert(1)</script>'
        self.brief['chapters'].insert(1, {'kind': 'maker', 'title': 'Process', 'lead': 'Unverified', 'process_steps': []})
        self.brief['chapters'].insert(2, {'kind': 'model_matrix', 'title': 'Compare', 'lead': 'Example', 'models': [], 'rows': []})
        self.prepare()
        core.build_project(self.pid)
        html = core.preview_file(self.pid, 'index.html').read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;alert(1)&lt;/script&gt;', html)
        self.assertNotIn('<script>alert(1)</script>', html)
        self.assertNotIn('Percy series feature comparison', html)
        self.assertNotIn('OBSERVED 02 NOV 2026', html)
        self.assertNotIn('Final inspection claimed', html)

    def test_http_round_trip_and_cross_origin_rejection(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        base = f'http://127.0.0.1:{server.server_port}'
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def request(path, method='GET', payload=None, headers=None):
            data = json.dumps(payload).encode() if payload is not None else None
            h = {'Content-Type': 'application/json'} if data is not None else {}
            h.update(headers or {})
            with opener.open(urllib.request.Request(base + path, data=data, method=method, headers=h)) as response:
                return response.read()
        try:
            self.assertIn(b'Buyer Magazine Studio', request('/magazines'))
            created = json.loads(request('/api/projects', 'POST', {'name':'Web project','website':'https://example.com','request':'请为 https://example.com 制作杂志。'}))
            pid = created['id']
            self.assertEqual(created['messages'][0]['text'], '请为 https://example.com 制作杂志。')
            updated = json.loads(request(f'/api/projects/{pid}/messages', 'POST', {'text':'突出材料。'}))
            self.assertEqual(updated['messages'][1]['text'], '突出材料。')
            request(f'/api/projects/{pid}/images', 'POST', {'filename':'example.png','data_base64':self.image})
            request(f'/api/projects/{pid}/brief', 'PUT', {'brief':self.brief})
            built = json.loads(request(f'/api/projects/{pid}/build', 'POST', {}))
            self.assertEqual(built['checks']['chapters'], 4)
            self.assertIn(b'Fictional Example', request(f'/preview/{pid}/index.html'))
            with zipfile.ZipFile(io.BytesIO(request(f'/api/projects/{pid}/download'))) as archive:
                self.assertIsNone(archive.testzip())
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                request('/api/projects', 'POST', {'name':'Bad','website':'https://example.com'}, {'Origin':'https://evil.example'})
            self.assertEqual(rejected.exception.code, 403)
            with self.assertRaises(urllib.error.HTTPError) as rejected:
                request('/api/projects', headers={'Host':'evil.example'})
            self.assertEqual(rejected.exception.code, 403)
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
