import concurrent.futures
import contextlib
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from studio import hub, hub_http


class HubTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name})
        self.env.start()
        self.addCleanup(self.env.stop)

    def room(self, **kwargs):
        return hub.create_room('研究一个真实项目', **kwargs)

    def claim(self, room=None, worker='test-worker', **kwargs):
        return hub.claim_task(worker, room_id=room['id'] if room else None, **kwargs)

    def complete(self, claim, result=None, worker='test-worker'):
        return hub.complete_task(claim['task']['id'], worker, claim['lease_token'], result or {'message': '测试结果'})

    def assert_hub_error(self, status, fn, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, status)

    def expire(self, task_id):
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / 'studio.sqlite3')) as conn:
            conn.execute('UPDATE hub_tasks SET lease_expires_at=? WHERE id=?', ('2000-01-01T00:00:00.000000+00:00', task_id))
            conn.commit()

    def test_agents_context_persistence_and_public_credentials(self):
        agent = hub.create_agent('采购助手', '品牌研究', '核对资料并提供来源')
        changed = hub.update_agent(agent['id'], '采购研究员', '核对品牌', '必须保留来源')
        self.assertFalse(changed['builtin'])
        room = self.room(agent_id=agent['id'])
        hub.update_context(room['id'], '面向精品买手店')
        claimed = self.claim(room)
        self.assertEqual(claimed['context']['shared_context'], '面向精品买手店')
        self.assertEqual(claimed['context']['agent']['instructions'], '必须保留来源')
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 1)
        public = hub.get_room(room['id'])
        self.assertNotIn('lease_token', json.dumps(public))
        self.assertNotIn(claimed['lease_token'], json.dumps(public))
        finished = self.complete(claimed)
        self.assertEqual(finished['runs'][0]['status'], 'completed')
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 0)
        self.assertEqual(hub.get_room(room['id'])['messages'][-1]['text'], '测试结果')

    def test_exclusive_claim_across_independent_connections(self):
        room = self.room()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claims = list(pool.map(lambda i: self.claim(room, worker=f'worker-{i}'), range(8)))
        successful = [c for c in claims if c['task']]
        self.assertEqual(len(successful), 1)
        self.assertEqual(successful[0]['task']['attempts'], 1)

    def test_expiry_reclaim_heartbeat_and_stale_writer(self):
        room = self.room()
        original = self.claim(room)
        pulse = hub.heartbeat_task(original['task']['id'], 'test-worker', original['lease_token'], '正在核验来源')
        self.assertEqual(pulse['task']['progress'], '正在核验来源')
        self.expire(original['task']['id'])
        self.assertEqual(hub.get_room(room['id'])['runs'][0]['tasks'][0]['status'], 'queued')
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 0)
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / 'studio.sqlite3')) as conn:
            stored = conn.execute('SELECT status FROM hub_tasks WHERE id=?', (original['task']['id'],)).fetchone()[0]
        self.assertEqual(stored, 'running', 'GET must not mutate expired tasks')
        self.assert_hub_error(409, self.complete, original)
        reclaimed = self.claim(room, worker='replacement')
        self.assertEqual(reclaimed['task']['attempts'], 2)
        self.assertNotEqual(reclaimed['lease_token'], original['lease_token'])
        self.assert_hub_error(409, hub.heartbeat_task, original['task']['id'], 'test-worker', original['lease_token'])
        self.complete(reclaimed, worker='replacement')

    def test_plan_validation_is_atomic_and_dependency_schedule_is_real(self):
        room = self.room()
        planner = self.claim(room)
        cycle = [{'key': 'a', 'agent_id': 'researcher', 'title': 'A', 'instructions': 'A', 'depends_on': ['b']}, {'key': 'b', 'agent_id': 'writer', 'title': 'B', 'instructions': 'B', 'depends_on': ['a']}]
        invalid = {'message': '不应保存', 'plan': cycle, 'artifacts': [{'title': '不应保存', 'kind': 'text', 'content': 'x'}]}
        self.assert_hub_error(400, self.complete, planner, invalid)
        unchanged = hub.get_room(room['id'])
        self.assertEqual(len(unchanged['runs'][0]['tasks']), 1)
        self.assertEqual(len(unchanged['messages']), 1)
        self.assertFalse(unchanged['artifacts'])
        plan = [
            {'key': 'review', 'agent_id': 'reviewer', 'title': '审查', 'instructions': '检查两份结果', 'depends_on': ['design', 'code']},
            {'key': 'design', 'agent_id': 'designer', 'title': '设计', 'instructions': '设计界面', 'depends_on': []},
            {'key': 'code', 'agent_id': 'developer', 'title': '开发', 'instructions': '实现功能', 'depends_on': []},
        ]
        self.complete(planner, {'message': '已安排设计与开发', 'plan': plan})
        self.assertIsNone(self.claim(room, agent_ids=['reviewer'])['task'])
        designer = self.claim(room, agent_ids=['designer'])
        developer = self.claim(room, agent_ids=['developer'])
        self.assertIsNotNone(designer['task'])
        self.assertIsNotNone(developer['task'])
        self.complete(designer, {'message': '设计结果'})
        self.assertIsNone(self.claim(room, agent_ids=['reviewer'])['task'])
        self.complete(developer, {'message': '开发结果'})
        reviewer = self.claim(room, agent_ids=['reviewer'])
        self.assertEqual({dep['result']['message'] for dep in reviewer['context']['dependency_results']}, {'设计结果', '开发结果'})
        final = self.complete(reviewer)
        self.assertEqual(final['runs'][0]['status'], 'completed')

    def test_non_coordinator_cannot_plan(self):
        room = self.room(agent_id='writer')
        claim = self.claim(room)
        result = {'message': '安排任务', 'plan': [{'key': 'x', 'agent_id': 'reviewer', 'title': '核验', 'instructions': '核验'}]}
        self.assert_hub_error(400, self.complete, claim, result)

    def test_artifact_revision_conflict_rolls_back_and_preserves_exact_bytes(self):
        room = self.room(agent_id='writer')
        first = self.claim(room)
        content = '# 标题\r\n\n正文  \n'
        result = {'message': '初稿', 'artifacts': [{'title': '报告', 'kind': 'markdown', 'content': content}]}
        room = self.complete(first, result)
        artifact = room['artifacts'][0]
        again = self.complete(first, result)
        self.assertEqual(len(again['messages']), 2)
        self.assertEqual(len(again['artifacts']), 1)
        self.assert_hub_error(409, self.complete, first, {'message': '篡改结果'})
        hub.add_message(room['id'], '修改报告', 'writer')
        second = self.claim(room)
        update = {'id': artifact['id'], 'base_version': 1, 'title': '修订报告', 'kind': 'markdown', 'content': '第二版'}
        revised = self.complete(second, {'message': '已修订', 'artifacts': [update]})
        self.assertEqual(revised['artifacts'][0]['version'], 2)
        old = hub.get_artifact(artifact['id'], 1)
        self.assertEqual(old['content'], content)
        self.assertEqual(old['title'], '报告')
        hub.add_message(room['id'], '再次修改', 'writer')
        third = self.claim(room)
        bad = {'message': '不应提交', 'artifacts': [{'title': '不应产生', 'kind': 'text', 'content': 'bad'}, update]}
        self.assert_hub_error(409, self.complete, third, bad)
        stored = hub.get_room(room['id'])
        self.assertEqual(len(stored['artifacts']), 1)
        self.assertEqual(stored['runs'][-1]['tasks'][0]['status'], 'running')
        status, raw, mime, headers = hub_http.dispatch('GET', f'/api/hub/artifacts/{artifact["id"]}/download', query={'version': ['1']})
        self.assertEqual(status, 200)
        self.assertEqual(raw, content.encode('utf-8'))
        self.assertIn("filename*=UTF-8''", headers['Content-Disposition'])
        headers['Content-Disposition'].encode('ascii')

    def test_cross_room_artifact_rejected_and_html_preview_is_sandboxed(self):
        room = self.room(agent_id='writer')
        original = self.complete(self.claim(room), {'message': '页面', 'artifacts': [{'title': '页面', 'kind': 'html', 'content': '<script>fetch("/api/hub/bootstrap")</script>'}]})
        artifact_id = original['artifacts'][0]['id']
        other = self.room(agent_id='writer')
        self.assert_hub_error(409, self.complete, self.claim(other), {'message': '越界修改', 'artifacts': [{'id': artifact_id, 'base_version': 1, 'title': '越界', 'kind': 'text', 'content': 'bad'}]})
        status, raw, mime, headers = hub_http.dispatch('GET', f'/api/hub/artifacts/{artifact_id}/preview')
        self.assertEqual(status, 200)
        self.assertIn('text/html', mime)
        self.assertIn("default-src 'none'", headers['Content-Security-Policy'])
        self.assertTrue(headers['Content-Security-Policy'].startswith('sandbox;'))
        self.assertNotIn('allow-scripts', headers['Content-Security-Policy'])

    def test_cancel_invalidates_running_results_and_preserves_completed_work(self):
        room = self.room(template_id='product')
        self.complete(self.claim(room))
        design = self.claim(room, agent_ids=['designer'])
        cancelled = hub.cancel_run(room['runs'][0]['id'])
        self.assertEqual(cancelled['runs'][0]['status'], 'cancelled')
        self.assertEqual(cancelled['runs'][0]['tasks'][0]['status'], 'completed')
        self.assert_hub_error(409, self.complete, design)
        self.assertIsNone(self.claim(room)['task'])
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 0)

    def test_failed_retry_only_replays_failed_and_unstarted_tasks(self):
        room = self.room(template_id='research')
        self.complete(self.claim(room))
        writing = self.claim(room)
        failed = hub.fail_task(writing['task']['id'], 'test-worker', writing['lease_token'], '来源暂不可用')
        self.assertEqual(failed['runs'][0]['status'], 'failed')
        self.assertIsNone(self.claim(room)['task'])
        retried = hub.retry_run(room['runs'][0]['id'])
        self.assertEqual(retried['runs'][0]['tasks'][0]['status'], 'completed')
        retried_task = self.claim(room)
        self.assertEqual(retried_task['task']['id'], writing['task']['id'])
        self.assertEqual(retried_task['task']['attempts'], 2)
        self.complete(retried_task)
        self.complete(self.claim(room))
        self.assertEqual(hub.get_room(room['id'])['status'], 'completed')

    def test_room_runs_are_serial_and_future_user_messages_are_not_claim_context(self):
        room = self.room(agent_id='researcher')
        hub.add_message(room['id'], '后续机密指令', 'writer')
        self.assertIsNone(self.claim(room, agent_ids=['writer'])['task'])
        first = self.claim(room, agent_ids=['researcher'])
        self.assertNotIn('后续机密指令', json.dumps(first['context'], ensure_ascii=False))
        self.complete(first)
        second = self.claim(room, agent_ids=['writer'])
        self.assertIn('后续机密指令', json.dumps(second['context'], ensure_ascii=False))

    def test_retry_old_failure_cannot_reorder_started_followup(self):
        room = self.room()
        task = self.claim(room)
        hub.fail_task(task['task']['id'], 'test-worker', task['lease_token'], '失败')
        hub.add_message(room['id'], '继续处理')
        later = self.claim(room)
        self.assertIsNotNone(later['task'])
        self.assert_hub_error(409, hub.retry_run, room['runs'][0]['id'])

    def test_http_validation_events_and_unknown_routes(self):
        room = self.room()
        first = hub.get_events(room['id'])
        self.assertTrue(first['events'])
        self.assertEqual(hub.get_events(room['id'], first['cursor'])['events'], [])
        self.assertEqual(hub_http.dispatch('POST', '/api/hub/rooms', {'text': 'x', 'template_id': []})[0], 404)
        self.assertEqual(hub_http.dispatch('POST', '/api/hub/rooms', [1])[0], 400)
        self.assertEqual(hub_http.dispatch('POST', '/api/hub/rooms', {'text': '\ud800'})[0], 400)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/rooms/{room["id"]}/events', query={'after': ['bad']})[0], 400)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/rooms/{room["id"]}/events', query={'after': ['1', '2']})[0], 400)
        self.assertEqual(hub_http.dispatch('GET', '/api/hub/missing')[0], 404)
        self.assertIsNone(hub_http.dispatch('GET', '/api/projects'))

    def test_workflow_edits_validate_dag_and_snapshot_instantiated_runs(self):
        steps = [
            {'key': 'review', 'agent_id': 'reviewer', 'title': '验收', 'instructions': '核对研究', 'depends_on': ['research']},
            {'key': 'research', 'agent_id': 'researcher', 'title': '初始研究', 'instructions': '保留证据', 'depends_on': []},
        ]
        template = hub.create_template('研究小队', '可复用流程', steps)
        room = self.room(template_id=template['id'])
        self.assertEqual(room['runs'][0]['template_version'], 1)
        self.assertEqual(room['runs'][0]['template_id'], template['id'])
        self.assertIn('研究一个真实项目', room['runs'][0]['tasks'][0]['instructions'])
        self.assertEqual(room['runs'][0]['tasks'][0]['title'], '初始研究')
        steps[1]['title'] = '新研究'
        edited = hub.update_template(template['id'], 1, '改名小队', '', steps)
        self.assertEqual(edited['version'], 2)
        self.assertEqual(hub.get_room(room['id'])['runs'][0]['tasks'][0]['title'], '初始研究')
        new_room = self.room(template_id=template['id'])
        self.assertEqual(new_room['runs'][0]['template_version'], 2)
        self.assertEqual(new_room['runs'][0]['tasks'][0]['title'], '新研究')
        self.assert_hub_error(409, hub.update_template, template['id'], 1, '过时修改', '', steps)
        steps[1]['depends_on'] = ['review']
        self.assert_hub_error(400, hub.update_template, template['id'], 2, '循环', '', steps)
        self.assertEqual(hub.get_template(template['id'])['version'], 2)
        self.assert_hub_error(400, hub.create_template, '空团队', '', [])
        built_in = hub.get_template('product')
        hub.update_template('product', built_in['version'], '自己的产品团队', '', built_in['steps'])
        self.assertEqual(hub.bootstrap()['templates'][1]['name'], '自己的产品团队')

    def test_concurrent_workflow_edits_only_accept_one_base_version(self):
        template = hub.get_template('research')

        def edit(index):
            try:
                return hub.update_template('research', 1, f'编辑 {index}', '', template['steps'])['version']
            except hub.HubError as exc:
                return exc.status

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(edit, range(4)))
        self.assertEqual(results.count(2), 1)
        self.assertEqual(results.count(409), 3)

    def test_sources_preserve_hash_record_unfetched_urls_and_revoke_from_new_context(self):
        room = self.room()
        content = '  原始资料\r\n保留换行  \n'
        source = hub.add_source(room['id'], '原文', 'text', content, 'https://example.com/source')
        self.assertEqual(source['sha256'], hashlib.sha256(content.encode('utf-8')).hexdigest())
        self.assertEqual(source['content'], content)
        self.assertEqual(source['version'], 1)
        url = hub.add_source(room['id'], '网址', 'url', 'https://example.com/page')
        self.assertEqual(url['retrieval_status'], 'not_fetched')
        self.assertEqual(url['content'], 'https://example.com/page')
        self.assertNotIn('content', hub.get_room(room['id'])['sources'][0])
        first = self.claim(room)
        self.assertEqual(len(first['context']['sources']), 2)
        self.assertEqual(first['context']['sources'][0]['content'], content)
        self.complete(first)
        revoked = hub.revoke_source(source['id'])
        self.assertIsNotNone(revoked['source']['revoked_at'])
        self.assertEqual(hub.revoke_source(source['id']), revoked)
        self.assertEqual(hub.list_sources(room['id'])['revoked_count'], 1)
        hub.add_message(room['id'], '重新阅读资料')
        later = self.claim(room)
        self.assertEqual([item['id'] for item in later['context']['sources']], [url['id']])
        self.assertNotIn(source['id'], [item['id'] for item in later['context']['references']])
        self.assertEqual(hub.get_source(source['id'])['content'], content)
        self.assert_hub_error(400, hub.add_source, room['id'], '无效网址', 'url', 'javascript:alert(1)')

    def test_claim_context_has_explicit_omissions_and_readable_versioned_artifacts(self):
        room = self.room(agent_id='writer')
        completed = self.complete(self.claim(room), {'message': '已提交', 'artifacts': [{'title': '现有成果', 'kind': 'text', 'content': '需要记住的成果'}]})
        artifact_id = completed['artifacts'][0]['id']
        hub.review_artifact(artifact_id, 1, 'approved', '这一版通过')
        huge = hub.add_source(room['id'], '长资料', 'markdown', 'a' * (hub.MAX_CONTEXT + 20000))
        small = hub.add_source(room['id'], '短资料', 'text', '可读的小段资料')
        hub.add_message(room['id'], '使用已有成果和资料')
        claim = self.claim(room)
        context = claim['context']
        self.assertLessEqual(len(json.dumps(context, ensure_ascii=False).encode('utf-8')), hub.MAX_CONTEXT)
        self.assertEqual(context['sources'][0]['id'], small['id'])
        self.assertTrue(any(huge['id'] in item['ids'] for item in context['omissions']))
        self.assertTrue(any(item['id'] == huge['id'] and item['url'].endswith(huge['id']) for item in context['references']))
        self.assertEqual(context['artifact_contents'][0]['content'], '需要记住的成果')
        self.assertEqual(context['artifact_contents'][0]['author_type'], 'agent')
        self.assertEqual(context['artifact_contents'][0]['reviews'][0]['decision'], 'approved')

    def test_human_edits_are_atomic_and_reviews_belong_to_versions(self):
        room = self.room(agent_id='writer')
        initial = self.complete(self.claim(room), {'message': '已提交', 'artifacts': [{'title': '报告', 'kind': 'markdown', 'content': '初稿'}]})
        artifact_id = initial['artifacts'][0]['id']
        hub.review_artifact(artifact_id, 1, 'approved', '认可这一版')
        updated = hub.edit_artifact(artifact_id, 1, '人工修订', 'markdown', '手工修改内容\n')
        self.assertEqual(updated['version'], 2)
        self.assertEqual(updated['author_type'], 'human')
        self.assertEqual(updated['versions'][0]['author_type'], 'human')
        self.assertEqual(updated['versions'][1]['author_type'], 'agent')
        self.assertEqual([review['version'] for review in updated['reviews']], [1])
        self.assert_hub_error(409, hub.edit_artifact, artifact_id, 1, '过时修改', 'text', '不应覆盖')
        self.assertEqual(hub.get_artifact(artifact_id)['content'], '手工修改内容\n')
        self.assert_hub_error(400, hub.edit_artifact, artifact_id, 2, '损坏数据', 'json', '{')
        self.assertEqual(hub.get_artifact(artifact_id)['version'], 2)
        hub.review_artifact(artifact_id, 2, 'changes_requested', '补充来源')
        hub.review_artifact(artifact_id, 2, 'approved', '已通过人工核验')
        reviews = hub.get_artifact(artifact_id)['reviews']
        self.assertEqual(len(reviews), 3)
        self.assertEqual(reviews[-1]['decision'], 'approved')
        self.assert_hub_error(404, hub.review_artifact, artifact_id, 99, 'approved')
        self.assert_hub_error(400, hub.review_artifact, artifact_id, None, 'approved')

    def test_worker_session_cannot_be_stolen_and_claims_respect_registered_roles(self):
        registered = hub.register_worker('fixture-worker', ['researcher'], name='测试执行器', capabilities=['web'])
        token = registered['session_token']
        self.assertEqual(hub.bootstrap()['connection']['connected_workers'], 1)
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 0)
        self.assertNotIn(token, json.dumps(hub.list_workers()))
        self.assertNotIn('session_hash', json.dumps(hub.bootstrap()))
        self.assert_hub_error(409, hub.register_worker, 'fixture-worker', ['writer'])
        room = self.room(agent_id='researcher')
        self.assert_hub_error(409, hub.claim_task, 'fixture-worker', room_id=room['id'])
        self.assert_hub_error(409, hub.claim_task, 'fixture-worker', ['writer'], room['id'], token)
        self.assert_hub_error(409, hub.ping_worker, 'fixture-worker', 'wrong-token')
        claim = hub.claim_task('fixture-worker', room_id=room['id'], session_token=token)
        self.assertEqual(claim['task']['agent_id'], 'researcher')
        self.assertEqual(hub.ping_worker('fixture-worker', token)['worker']['connected'], True)
        self.assertFalse(hub.disconnect_worker('fixture-worker', token)['worker']['connected'])
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 1, 'disconnect does not silently cancel a task')
        self.assert_hub_error(409, hub.claim_task, 'fixture-worker', session_token=token)
        self.complete(claim, worker='fixture-worker')
        replacement = hub.register_worker('fixture-worker', ['writer'])
        self.assertNotEqual(replacement['session_token'], token)
        self.assert_hub_error(409, hub.disconnect_worker, 'fixture-worker', token)
        self.assert_hub_error(409, hub.claim_task, 'unknown-worker', session_token='invented')

    def test_worker_registration_expiry_is_truthful_and_independent_of_task_lease(self):
        registered = hub.register_worker('expiring-worker', ['researcher'])
        token = registered['session_token']
        room = self.room(agent_id='researcher')
        claim = hub.claim_task('expiring-worker', room_id=room['id'], session_token=token)
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / 'studio.sqlite3')) as conn:
            conn.execute('UPDATE hub_workers SET expires_at=? WHERE id=?', ('2000-01-01T00:00:00.000000+00:00', 'expiring-worker'))
            conn.commit()
        self.assertEqual(hub.bootstrap()['connection']['connected_workers'], 0)
        self.assertEqual(hub.bootstrap()['connection']['active_workers'], 1)
        self.assert_hub_error(409, hub.ping_worker, 'expiring-worker', token)
        hub.heartbeat_task(claim['task']['id'], 'expiring-worker', claim['lease_token'], '任务仍有独立租约')
        self.assertEqual(hub.bootstrap()['connection']['connected_workers'], 0)
        self.complete(claim, worker='expiring-worker')
        replacement = hub.register_worker('expiring-worker', ['researcher'])
        self.assertEqual(replacement['worker']['connected'], True)
        self.assert_hub_error(409, hub.ping_worker, 'expiring-worker', token)

    def test_v04_http_routes_and_legacy_schema_migration(self):
        db_file = Path(self.temp.name) / 'studio.sqlite3'
        with contextlib.closing(sqlite3.connect(db_file)) as conn:
            conn.executescript('CREATE TABLE hub_runs (id TEXT PRIMARY KEY, room_id TEXT NOT NULL, cancelled INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL); CREATE TABLE hub_artifact_versions (artifact_id TEXT NOT NULL,version INTEGER NOT NULL,task_id TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,content TEXT NOT NULL,created_at TEXT NOT NULL,PRIMARY KEY(artifact_id,version));')
            conn.commit()
        room = self.room()
        self.assertIsNone(room['runs'][0]['template_id'])
        self.assertEqual(hub_http.dispatch('GET', '/api/hub/templates')[0], 200)
        source = hub_http.dispatch('POST', f'/api/hub/rooms/{room["id"]}/sources', {'title': '资料', 'kind': 'text', 'content': '证据'})
        self.assertEqual(source[0], 201)
        self.assertEqual(hub_http.dispatch('GET', f'/api/hub/sources/{source[1]["id"]}')[1]['content'], '证据')
        self.assertEqual(hub_http.dispatch('DELETE', f'/api/hub/sources/{source[1]["id"]}')[0], 200)
        registration = hub_http.dispatch('POST', '/api/hub/workers/register', {'worker_id': 'http-worker', 'agent_ids': ['coordinator']})
        self.assertEqual(registration[0], 201)
        self.assertEqual(hub_http.dispatch('POST', '/api/hub/workers/http-worker/ping', {'session_token': registration[1]['session_token']})[0], 200)


if __name__ == '__main__':
    unittest.main()
