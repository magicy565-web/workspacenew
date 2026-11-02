import concurrent.futures
import contextlib
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from studio import accounts, core, hub, hub_extensions as ext, hub_http, scheduler


class ExtensionTests(unittest.TestCase):
    due = '2026-01-01T00:00:00+00:00'
    tick = '2026-01-03T00:00:00+00:00'
    password = 'fixture-password-with-entropy-381'

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        root_binding = core.REQUEST_DATA_ROOT.set(None)
        actor_binding = core.REQUEST_ACTOR.set(None)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, root_binding)
        self.addCleanup(core.REQUEST_ACTOR.reset, actor_binding)

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def schedule(self, **kwargs):
        return ext.create_schedule('每日报告', '整理实际资料', 60, next_run_at=self.due, **kwargs)

    def complete_room(self, room_id):
        claim = hub.claim_task('fixture-worker', room_id=room_id)
        self.assertIsNotNone(claim['task'])
        return hub.complete_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], {'message': '完成实际测试步骤'})

    def owner(self):
        _, headers = accounts.setup({'email': 'owner@example.test', 'name': '所有者', 'password': self.password})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        return accounts.identify({'Cookie': cookie}), cookie

    @contextlib.contextmanager
    def actor(self, principal):
        root = core.REQUEST_DATA_ROOT.set(accounts.data_directory(principal))
        actor = core.REQUEST_ACTOR.set({'user_id': principal['user']['id'], 'workspace_id': principal['workspace']['id'], 'role': principal['workspace']['role']})
        try:
            yield
        finally:
            core.REQUEST_ACTOR.reset(actor)
            core.REQUEST_DATA_ROOT.reset(root)

    def test_skill_versions_exact_content_archive_and_binding_conflicts(self):
        content = '---\nname: evidence-review\n---\n\n保留原文  \r\n'
        skill = ext.create_skill('证据检查', '核对来源', content, 'https://example.com/skill')
        self.assertEqual(skill['sha256'], hashlib.sha256(content.encode('utf-8')).hexdigest())
        self.assertEqual(skill['content'], content)
        binding = ext.set_agent_skills('reviewer', 0, [skill['id']])
        self.assertEqual(binding['version'], 1)
        self.assert_error(409, ext.set_agent_skills, 'reviewer', 0, [])
        updated = ext.update_skill(skill['id'], 1, '证据检查第二版', '更严格', '第二版内容')
        self.assertEqual(updated['version'], 2)
        self.assertEqual(ext.get_skill(skill['id'], 1)['content'], content)
        self.assert_error(409, ext.update_skill, skill['id'], 1, '旧编辑', '', '不应保存')
        self.assert_error(409, ext.archive_skill, skill['id'], 1)
        archived = ext.archive_skill(skill['id'], 2)
        self.assertIsNotNone(archived['archived_at'])
        self.assertEqual(ext.archive_skill(skill['id'], 2)['archived_at'], archived['archived_at'])
        self.assert_error(409, ext.update_skill, skill['id'], 2, '归档编辑', '', '不能保存')
        self.assert_error(409, ext.set_agent_skills, 'writer', 0, [skill['id']])
        self.assertEqual(ext.get_agent_skills('reviewer')['skills'][0]['id'], skill['id'])

    def test_claim_contains_pinned_guidance_and_reports_archived_or_oversized_skills(self):
        useful = ext.create_skill('核验', '', '核对证据，不能捏造执行结果。')
        large = ext.create_skill('长指南', '', 'a' * ext.MAX_SKILL)
        archived = ext.create_skill('旧指南', '', '已不再使用')
        ext.set_agent_skills('reviewer', 0, [useful['id'], large['id'], archived['id']])
        ext.archive_skill(archived['id'], 1)
        room = hub.create_room('审阅实际结果', agent_id='reviewer')
        claim = hub.claim_task('review-fixture', room_id=room['id'])
        context = claim['context']
        self.assertEqual([skill['id'] for skill in context['skills']], [useful['id']])
        self.assertEqual(context['skills'][0]['version'], 1)
        self.assertTrue(any(item['reason'] == 'skill_archived' and archived['id'] in item['ids'] for item in context['omissions']))
        self.assertTrue(any(item['kind'] == 'skills' and large['id'] in item['ids'] for item in context['omissions']))
        self.assertTrue(any(ref['id'] == useful['id'] and ref['sha256'] == useful['sha256'] for ref in context['references']))
        self.assertIn('not installed commands', context['skill_policy'])
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False).encode('utf-8')), hub.MAX_CONTEXT)

    def test_concurrent_ticks_create_exactly_one_run_and_restart_catches_up_once(self):
        schedule = self.schedule()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: ext.tick_due(self.tick), range(8)))
        self.assertEqual(sum(len(result['created']) for result in results), 1)
        saved = ext.get_schedule(schedule['id'])
        self.assertEqual(saved['run_count'], 1)
        self.assertGreater(saved['next_run_at'], self.tick.replace('+00:00', '.000000+00:00'))
        self.assertEqual(saved['last_run_status'], 'queued')
        self.assertEqual(len(saved['history']), 1)
        self.assertEqual(ext.tick_due(self.tick)['created'], [])
        self.complete_room(saved['room_id'])
        catchup = ext.tick_due('2026-02-01T00:00:00+00:00')
        self.assertEqual(len(catchup['created']), 1)
        next_saved = ext.get_schedule(schedule['id'])
        self.assertEqual(next_saved['run_count'], 2)
        self.assertEqual(next_saved['room_id'], saved['room_id'])
        self.assertEqual(len(hub.get_room(saved['room_id'])['runs']), 2)
        self.assertEqual(ext.tick_due('2026-02-01T00:00:00+00:00')['created'], [])

    def test_active_prior_run_is_skipped_and_failed_status_is_visible(self):
        schedule = self.schedule()
        first = ext.tick_due(self.tick)['created'][0]
        skipped = ext.tick_due('2026-01-03T00:05:00+00:00')
        self.assertEqual(skipped['skipped'][0]['reason'], 'previous_run_active')
        self.assertEqual(ext.get_schedule(schedule['id'])['run_count'], 1)
        manual = ext.run_schedule_now(schedule['id'])
        self.assertIsNone(manual['run_id'])
        self.assertEqual(manual['skipped_reason'], 'previous_run_active')
        claim = hub.claim_task('fixture-worker', room_id=first['room_id'])
        hub.fail_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], '实际执行失败')
        self.assertEqual(ext.get_schedule(schedule['id'])['last_run_status'], 'failed')
        self.assertEqual(ext.get_schedule(schedule['id'])['last_error'], '实际执行失败')
        self.assertEqual(len(ext.tick_due('2026-01-03T00:10:00+00:00')['created']), 1)

    def test_pause_manual_run_and_archive_keep_historical_work(self):
        schedule = self.schedule()
        paused = ext.pause_schedule(schedule['id'])
        self.assertFalse(paused['enabled'])
        self.assertEqual(ext.tick_due(self.tick)['created'], [])
        manual = ext.run_schedule_now(schedule['id'])
        self.assertIsNotNone(manual['run_id'])
        self.assertFalse(manual['schedule']['enabled'])
        archived = ext.archive_schedule(schedule['id'])
        self.assertIsNotNone(archived['archived_at'])
        self.assertEqual(ext.tick_due('2027-01-01T00:00:00+00:00')['created'], [])
        self.assertEqual(hub.get_room(manual['room_id'])['runs'][0]['status'], 'queued')
        self.complete_room(manual['room_id'])
        self.assertEqual(ext.get_schedule(schedule['id'])['last_run_status'], 'completed')
        self.assert_error(409, ext.pause_schedule, schedule['id'], False)
        self.assert_error(409, ext.run_schedule_now, schedule['id'])

    def test_schedule_edit_conflicts_validation_and_saved_template_use(self):
        self.assert_error(400, ext.create_schedule, '过快', '要求', 59)
        self.assert_error(400, ext.create_schedule, '无时区', '要求', 60, next_run_at='2026-01-01T00:00:00')
        schedule = self.schedule(template_id='product')
        edited = ext.update_schedule(schedule['id'], 1, '更新后的计划', '新要求', 120, template_id='research', next_run_at=self.due)
        self.assertEqual(edited['version'], 2)
        self.assert_error(409, ext.update_schedule, schedule['id'], 1, '旧编辑', '旧要求', 60)
        first = ext.tick_due(self.tick)['created'][0]
        run = hub.get_room(first['room_id'])['runs'][0]
        self.assertEqual(run['template_id'], 'research')
        self.assertEqual(len(run['tasks']), 3)
        self.assertIn('新要求', run['tasks'][0]['instructions'])
        another = hub.create_room('另一个会话')
        self.assert_error(409, ext.update_schedule, schedule['id'], 2, '更换会话', '要求', 60, room_id=another['id'])

    def test_member_revocation_pauses_creator_schedule_before_start(self):
        owner, _ = self.owner()
        invitation = accounts.dispatch('POST', '/api/account/invitations', {'email': 'editor@example.test', 'role': 'editor'}, owner)[1]
        _, headers = accounts.accept_invitation({'token': invitation['token'], 'email': 'editor@example.test', 'name': '编辑', 'password': self.password})
        editor = accounts.identify({'Cookie': headers['Set-Cookie'].split(';', 1)[0]})
        with self.actor(editor):
            schedule = self.schedule()
            skill = ext.create_skill('成员技能', '', '有来源的指南')
        self.assertEqual(schedule['owner_user_id'], editor['user']['id'])
        self.assertEqual(skill['created_by'], editor['user']['id'])
        accounts.dispatch('DELETE', f'/api/account/members/{editor["user"]["id"]}', {}, owner)
        result = ext.tick_due(self.tick)
        self.assertEqual(result['paused'][0]['reason'], 'creator_permission_revoked')
        saved = ext.get_schedule(schedule['id'])
        self.assertFalse(saved['enabled'])
        self.assertEqual(saved['run_count'], 0)
        self.assertIsNone(saved['room_id'])
        self.assert_error(403, ext.pause_schedule, schedule['id'], False)
        self.assert_error(403, self.schedule)

    def test_enabling_accounts_pauses_old_local_schedule(self):
        schedule = self.schedule()
        self.owner()
        result = ext.tick_due(self.tick)
        self.assertEqual(len(result['paused']), 1)
        self.assertIn('本地模式', ext.get_schedule(schedule['id'])['pause_reason'])

    def test_tick_all_isolates_workspaces_and_restores_request_context(self):
        owner, cookie = self.owner()
        with self.actor(owner):
            first = self.schedule()
        accounts.dispatch('POST', '/api/account/workspaces', {'name': '第二空间'}, owner)
        other = accounts.identify({'Cookie': cookie})
        with self.actor(other):
            second = self.schedule()
            prior_root, prior_actor = core.data_root(), core.REQUEST_ACTOR.get()
            result = scheduler.tick_all(self.tick)
            self.assertEqual(core.data_root(), prior_root)
            self.assertEqual(core.REQUEST_ACTOR.get(), prior_actor)
            self.assertEqual(ext.get_schedule(second['id'])['run_count'], 1)
            scheduled_room = hub.get_room(ext.get_schedule(second['id'])['room_id'])
            self.assertEqual(scheduled_room['messages'][0]['author_id'], owner['user']['id'])
            self.assertEqual(scheduled_room['messages'][0]['author_name'], '所有者')
            self.assert_error(404, ext.get_schedule, first['id'])
            with patch.object(ext, 'tick_due', side_effect=sqlite3.OperationalError('fixture failure')):
                failed = scheduler.tick_all(self.tick)
            self.assertEqual(core.data_root(), prior_root)
            self.assertEqual(core.REQUEST_ACTOR.get(), prior_actor)
            self.assertEqual(len(failed['errors']), 2)
        self.assertEqual(result['errors'], [])
        self.assertEqual(sum(len(item['created']) for item in result['workspaces']), 2)
        with self.actor(owner):
            self.assertEqual(ext.get_schedule(first['id'])['run_count'], 1)
            self.assert_error(404, ext.get_schedule, second['id'])

    def test_extension_http_dispatch_routes(self):
        status, skill, _, _ = hub_http.dispatch('POST', '/api/hub/skills', {'name': '文档', 'description': '', 'content': '# Skill'})
        self.assertEqual(status, 201)
        self.assertEqual(hub_http.dispatch('PUT', '/api/hub/agents/writer/skills', {'base_version': 0, 'skill_ids': [skill['id']]})[0], 200)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/skills/{skill["id"]}', query={'version': ['1']})[1]['content'], '# Skill')
        status, schedule, _, _ = hub_http.dispatch('POST', '/api/hub/schedules', {'name': '每周整理', 'text': '核验', 'interval_seconds': 604800})
        self.assertEqual(status, 201)
        self.assertEqual(hub_http.dispatch('POST', f'/api/hub/schedules/{schedule["id"]}/pause', {'paused': True})[0], 200)
        self.assertEqual(hub_http.dispatch('POST', f'/api/hub/schedules/{schedule["id"]}/run-now', {})[0], 200)
        self.assertEqual(hub_http.dispatch('DELETE', f'/api/hub/schedules/{schedule["id"]}', {})[0], 200)


if __name__ == '__main__':
    unittest.main()
