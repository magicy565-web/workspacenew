import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from studio import accounts, admin, backup, core, hub


class OperationsTests(unittest.TestCase):
    password = 'operator-fixture-password-143'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.data = self.root / 'data'
        self.data.mkdir()
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': str(self.data), 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        binding = core.REQUEST_DATA_ROOT.set(None)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, binding)

    def owner(self):
        info, headers = accounts.setup({'email': 'owner@example.test', 'name': '所有者', 'password': self.password})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        return info, accounts.identify({'Cookie': cookie}), cookie

    def archive(self):
        hub.create_room('测试备份持久数据')
        target = self.root / 'snapshot.zip'
        backup.create_backup(self.data, target, writers_stopped=True)
        return target

    def custom_archive(self, name, files, directories=None, compression=zipfile.ZIP_STORED, mode=None):
        entries = [{'path': path, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'kind': 'file'} for path, content in files]
        manifest = {'format': backup.FORMAT, 'version': 1, 'files': entries, 'directories': directories or [], 'total_bytes': sum(entry['bytes'] for entry in entries)}
        target = self.root / name
        with zipfile.ZipFile(target, 'w', compression=compression) as zipped:
            zipped.writestr('manifest.json', json.dumps(manifest))
            for path, content in files:
                if mode:
                    item = zipfile.ZipInfo('data/' + path)
                    item.create_system = 3
                    item.external_attr = mode << 16
                    zipped.writestr(item, content)
                else:
                    zipped.writestr('data/' + path, content)
        return target

    def test_backup_restores_accounts_two_workspaces_and_magazine_files(self):
        info, owner, cookie = self.owner()
        room_a = hub.create_room('租户 A 的内容')
        project = core.create_project('买手杂志 A', 'https://example.com')
        magazine = core.project_dir(project['id']) / 'build' / 'index.html'
        magazine.parent.mkdir(parents=True)
        magazine.write_bytes('<html><body>真实保存的杂志文件</body></html>'.encode('utf-8'))
        (magazine.parent / 'empty-assets').mkdir()
        workspace_b = accounts.dispatch('POST', '/api/account/workspaces', {'name': '租户 B'}, owner)[1]
        principal_b = accounts.identify({'Cookie': cookie})
        root_b = accounts.data_directory(principal_b)
        binding = core.REQUEST_DATA_ROOT.set(root_b)
        try:
            room_b = hub.create_room('租户 B 的独立内容')
            (root_b / 'magazine').mkdir()
            (root_b / 'magazine' / 'image.bin').write_bytes(bytes(range(256)))
        finally:
            core.REQUEST_DATA_ROOT.reset(binding)
        archive = self.root / 'complete.zip'
        result = backup.create_backup(self.data, archive, writers_stopped=True)
        self.assertEqual(result['databases'], 3)
        self.assertTrue(result['sha256_verified'])
        restored = self.root / 'restored'
        restored.mkdir()
        backup.restore_backup(archive, restored)
        self.assertEqual((restored / magazine.relative_to(self.data)).read_bytes(), magazine.read_bytes())
        self.assertTrue((restored / magazine.parent.relative_to(self.data) / 'empty-assets').is_dir())
        self.assertEqual((restored / 'workspaces' / workspace_b['id'] / 'magazine' / 'image.bin').read_bytes(), bytes(range(256)))
        with patch.dict(os.environ, {'STUDIO_DATA_DIR': str(restored)}):
            restored_principal = accounts.identify({'Cookie': cookie})
            self.assertEqual(restored_principal['workspace']['id'], workspace_b['id'])
            binding = core.REQUEST_DATA_ROOT.set(accounts.data_directory(restored_principal))
            try:
                self.assertEqual(hub.get_room(room_b['id'])['messages'][0]['text'], '租户 B 的独立内容')
                with self.assertRaises(hub.HubError):
                    hub.get_room(room_a['id'])
            finally:
                core.REQUEST_DATA_ROOT.reset(binding)
            accounts.dispatch('POST', f'/api/account/workspaces/{info["workspace"]["id"]}/select', {}, restored_principal)
            self.assertEqual(hub.get_room(room_a['id'])['messages'][0]['text'], '租户 A 的内容')
            self.assertTrue(admin.check_storage()['ok'])
        self.assertTrue(self.data.exists(), 'restore must preserve the original data tree')

    def test_sqlite_backup_includes_committed_wal_without_raw_sidecars(self):
        hub.create_room('WAL 数据')
        db_path = self.data / 'studio.sqlite3'
        with contextlib.closing(sqlite3.connect(db_path)) as writer:
            writer.execute('PRAGMA journal_mode=WAL')
            writer.execute('CREATE TABLE fixture_wal (value TEXT)')
            writer.execute('INSERT INTO fixture_wal VALUES (?)', ('已提交的 WAL 内容',))
            writer.commit()
            archive = self.root / 'wal.zip'
            backup.create_backup(self.data, archive, writers_stopped=True)
            with zipfile.ZipFile(archive) as zipped:
                self.assertFalse(any(name.endswith(('-wal', '-shm')) for name in zipped.namelist()))
        target = self.root / 'wal-restored'
        backup.restore_backup(archive, target)
        with contextlib.closing(sqlite3.connect(target / 'studio.sqlite3')) as conn:
            self.assertEqual(conn.execute('SELECT value FROM fixture_wal').fetchone()[0], '已提交的 WAL 内容')

    def test_hash_failure_does_not_modify_empty_or_missing_target(self):
        original = self.archive()
        damaged = self.root / 'changed.zip'
        with zipfile.ZipFile(original) as source, zipfile.ZipFile(damaged, 'w') as target:
            changed = False
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename != 'manifest.json' and not changed:
                    data = bytes([data[0] ^ 1]) + data[1:]
                    changed = True
                target.writestr(item.filename, data)
        empty = self.root / 'empty'
        empty.mkdir()
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(damaged, empty)
        self.assertEqual(list(empty.iterdir()), [])
        missing = self.root / 'missing'
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(damaged, missing)
        self.assertFalse(missing.exists())

    def test_restore_rejects_traversal_links_zipbombs_and_case_collisions(self):
        archives = [
            self.custom_archive('traversal.zip', [('../outside.txt', b'escape')]),
            self.custom_archive('absolute.zip', [('/outside.txt', b'escape')]),
            self.custom_archive('drive.zip', [('C:/outside.txt', b'escape')]),
            self.custom_archive('links.zip', [('link.txt', b'../../outside')], mode=stat.S_IFLNK | 0o777),
            self.custom_archive('bomb.zip', [('huge.txt', b'x' * (1024 * 1024))], compression=zipfile.ZIP_DEFLATED),
            self.custom_archive('case.zip', [('Report.txt', b'A'), ('report.txt', b'B')]),
            self.custom_archive('conflict.zip', [('same', b'file')], directories=['same']),
        ]
        for archive in archives:
            with self.subTest(archive=archive.name):
                destination = self.root / ('restore-' + archive.stem)
                with self.assertRaises(backup.BackupError):
                    backup.restore_backup(archive, destination)
                self.assertFalse(destination.exists())
        self.assertFalse((self.root / 'outside.txt').exists())

    def test_existing_data_is_never_overwritten_and_stop_writes_is_required(self):
        archive = self.archive()
        existing = self.root / 'existing'
        existing.mkdir()
        sentinel = existing / 'keep.txt'
        sentinel.write_text('保留已有数据', encoding='utf-8')
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(archive, existing)
        self.assertEqual(sentinel.read_text(encoding='utf-8'), '保留已有数据')
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, self.root / 'unsafe.zip')
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, self.data / 'recursive.zip', writers_stopped=True)
        with self.assertRaises(backup.BackupError):
            backup.create_backup(self.data, archive, writers_stopped=True)

    def test_declared_hash_cannot_hide_a_corrupt_database(self):
        original = self.archive()
        damaged = self.root / 'corrupt-database.zip'
        with zipfile.ZipFile(original) as source:
            manifest = json.loads(source.read('manifest.json'))
            files = {item['path']: source.read('data/' + item['path']) for item in manifest['files']}
        first = next(item for item in manifest['files'] if item['kind'] == 'sqlite')
        files[first['path']] = b'not a valid SQLite database'
        first['bytes'] = len(files[first['path']])
        first['sha256'] = hashlib.sha256(files[first['path']]).hexdigest()
        manifest['total_bytes'] = sum(item['bytes'] for item in manifest['files'])
        with zipfile.ZipFile(damaged, 'w') as zipped:
            zipped.writestr('manifest.json', json.dumps(manifest))
            for path, content in files.items():
                zipped.writestr('data/' + path, content)
        self.assertTrue(backup.verify_backup(damaged)['sha256_verified'])
        empty = self.root / 'database-empty'
        empty.mkdir()
        with self.assertRaises(backup.BackupError):
            backup.restore_backup(damaged, empty)
        self.assertEqual(list(empty.iterdir()), [])

    def test_admin_interactive_init_reset_revokes_all_sessions_and_tokens(self):
        output = io.StringIO()
        with patch.object(admin.getpass, 'getpass', side_effect=[self.password, self.password]), contextlib.redirect_stdout(output):
            status = admin.main(['init', '--email', 'owner@example.test', '--name', '管理员', '--workspace', '团队'])
        self.assertEqual(status, 0)
        self.assertNotIn(self.password, output.getvalue())
        info, headers = accounts.login({'email': 'owner@example.test', 'password': self.password})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        principal = accounts.identify({'Cookie': cookie})
        token = accounts.dispatch('POST', '/api/account/tokens', {'name': '自动化', 'scopes': ['read', 'execute']}, principal)[1]['token']
        new_password = 'new-operator-password-583'
        output = io.StringIO()
        with patch.object(admin.getpass, 'getpass', side_effect=[new_password, new_password]), contextlib.redirect_stdout(output):
            status = admin.main(['reset-password', '--email', 'owner@example.test'])
        self.assertEqual(status, 0)
        self.assertNotIn(new_password, output.getvalue())
        self.assertNotIn(token, output.getvalue())
        self.assertIsNone(accounts.identify({'Cookie': cookie}))
        with self.assertRaises(hub.HubError):
            accounts.identify({'Authorization': f'Bearer {token}'})
        with self.assertRaises(hub.HubError):
            accounts.login({'email': 'owner@example.test', 'password': self.password})
        self.assertEqual(accounts.login({'email': 'owner@example.test', 'password': new_password})[0]['user']['id'], info['user']['id'])

    def test_admin_check_reports_corruption_without_credentials_or_document_text(self):
        self.owner()
        hub.create_room('不能出现在健康检查中的正文')
        healthy = admin.check_storage()
        self.assertTrue(healthy['ok'])
        serialized = json.dumps(healthy, ensure_ascii=False)
        self.assertNotIn('owner@example.test', serialized)
        self.assertNotIn('不能出现在健康检查中的正文', serialized)
        self.assertNotIn('password_hash', serialized)
        broken = self.data / 'broken.sqlite3'
        broken.write_bytes(b'not a database')
        failed = admin.check_storage()
        self.assertFalse(failed['ok'])
        self.assertTrue(any(item['path'] == 'broken.sqlite3' and item['integrity'] == 'failed' for item in failed['databases']))


if __name__ == '__main__':
    unittest.main()
