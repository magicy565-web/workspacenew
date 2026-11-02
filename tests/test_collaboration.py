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

from studio import core, hub, hub_collaboration as collab, hub_http


class CollaborationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        env = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.temp.name, 'STUDIO_AUTH_MODE': '', 'STUDIO_PUBLIC_ORIGIN': ''})
        env.start()
        self.addCleanup(env.stop)
        self.addCleanup(core.REQUEST_DATA_ROOT.reset, core.REQUEST_DATA_ROOT.set(None))
        self.addCleanup(core.REQUEST_ACTOR.reset, core.REQUEST_ACTOR.set(None))
        self.serial = 0

    def key(self):
        self.serial += 1
        return f'fixture-key-{self.serial}'

    def artifact(self, content='First paragraph.\nSecond paragraph.', room_id=None):
        room = hub.create_room('Write fixture artifact', agent_id='writer') if room_id is None else hub.add_message(room_id, 'Another fixture artifact', agent_id='writer')
        claim = hub.claim_task('fixture-worker', room_id=room['id'])
        hub.complete_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], {'message': 'Created test fixture', 'artifacts': [{'title': 'Fixture', 'kind': 'markdown', 'content': content}]})
        return hub.get_artifact(hub.get_room(room['id'])['artifacts'][-1]['id'])

    def comment(self, artifact, **kwargs):
        return collab.create_comment(artifact['id'], kwargs.pop('artifact_version', artifact['version']), kwargs.pop('body', 'Make the opening clearer.'), kwargs.pop('request_key', self.key()), **kwargs)

    def request(self, artifact, comments=None, **kwargs):
        comments = comments or [self.comment(artifact)]
        return collab.create_modification(artifact['id'], kwargs.pop('base_version', artifact['version']), [item['id'] for item in comments], kwargs.pop('request_key', self.key()), **kwargs)

    def claim(self, request, worker='fixture-worker'):
        claim = hub.claim_task(worker, room_id=request['room_id'])
        self.assertEqual(claim['task']['id'], request['task_id'])
        return claim

    def result(self, request, content='Clearer opening.\nSecond paragraph.'):
        return {'message': 'Applied the requested fixture edits.', 'artifacts': [{'id': request['artifact_id'], 'base_version': request['base_version'], 'title': 'Fixture revised', 'kind': 'markdown', 'content': content}]}

    def finish(self, claim, request, content='Clearer opening.\nSecond paragraph.', worker='fixture-worker'):
        return hub.complete_task(claim['task']['id'], worker, claim['lease_token'], self.result(request, content))

    def counts(self):
        with contextlib.closing(hub._db()) as conn:
            return tuple(conn.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in ('hub_runs', 'hub_tasks', 'hub_modifications', 'hub_comments', 'hub_artifact_versions', 'hub_collaboration_keys'))

    def assert_error(self, expected_status, fn, *args, **kwargs):
        with self.assertRaises(hub.HubError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(caught.exception.status, expected_status, str(caught.exception))

    def test_comments_anchor_history_replies_and_optimistic_thread_state(self):
        artifact = self.artifact('unique quote; repeat repeat; aaa')
        first = self.comment(artifact, anchor={'quote': 'unique quote'})
        self.assertEqual(first['anchor']['status'], 'matched')
        whitespace = self.comment(artifact, anchor={'quote': 'unique quote; '})
        self.assertEqual(whitespace['anchor']['quote'], 'unique quote; ')
        self.assertEqual(self.comment(artifact, anchor={'quote': 'repeat'})['anchor']['status'], 'ambiguous')
        self.assertEqual(self.comment(artifact, anchor={'quote': 'aa'})['anchor']['matches'], 2)
        self.assertEqual(self.comment(artifact, anchor={'quote': 'not present'})['anchor']['status'], 'missing')
        reply = self.comment(artifact, parent_id=first['id'], body='Preserve the second paragraph.')
        self.assertEqual(reply['anchor'], first['anchor'])
        self.assert_error(409, self.comment, artifact, parent_id=reply['id'])
        self.assert_error(400, collab.update_comment, reply['id'], 1, status='resolved')
        closed = collab.update_comment(first['id'], 1, body='Clearer and shorter.', status='resolved')
        self.assertEqual(closed['version'], 2)
        self.assert_error(409, collab.update_comment, first['id'], 1, status='open')
        opened = collab.update_comment(first['id'], 2, status='open')
        history = collab.get_comment(first['id'])['history']
        self.assertEqual([item['status'] for item in history], ['open', 'resolved', 'open'])
        self.assertEqual(history[0], first)
        self.assertEqual(opened['anchor'], first['anchor'])
        changed_reply = collab.update_comment(reply['id'], 1, body='Preserve all unrelated text.')
        self.assertEqual(collab.get_comment(reply['id'])['history'][0], reply)
        self.assertEqual(changed_reply['parent_id'], first['id'])

    def test_concurrent_comment_and_request_retries_create_one_entity(self):
        artifact = self.artifact()
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            comments = list(pool.map(lambda _: collab.create_comment(artifact['id'], 1, 'Feedback', 'same-comment'), range(6)))
        self.assertEqual(len({item['id'] for item in comments}), 1)
        root = comments[0]
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            requests = list(pool.map(lambda _: collab.create_modification(artifact['id'], 1, [root['id']], 'same-request'), range(6)))
        self.assertEqual(len({item['id'] for item in requests}), 1)
        self.assertEqual(len({item['task_id'] for item in requests}), 1)
        self.assertEqual(self.counts()[:4], (2, 2, 1, 1))
        collab.update_comment(root['id'], 1, body='Later feedback')
        replay = collab.create_modification(artifact['id'], 1, [root['id']], 'same-request')
        self.assertEqual(replay['snapshot'], requests[0]['snapshot'])
        self.assert_error(409, collab.create_modification, artifact['id'], 1, [root['id']], 'same-request', instructions='Different operation')
        self.assert_error(409, collab.create_comment, artifact['id'], 1, 'Other comment', 'same-comment')

    def test_frozen_baseline_comments_reply_agent_and_prioritized_bounded_claim(self):
        artifact = self.artifact('Precise original\n' + 'A' * 70000)
        root = self.comment(artifact)
        reply = self.comment(artifact, parent_id=root['id'], body='Original reply')
        request = self.request(artifact, [root], instructions='Change only the opening.')
        snapshot = request['snapshot']
        collab.update_comment(root['id'], 1, body='Changed after submission')
        collab.update_comment(reply['id'], 1, body='Reply changed after submission')
        self.comment(artifact, parent_id=root['id'], body='New late reply')
        hub.update_agent('writer', 'Changed writer', '', 'New instructions after submission')
        hub.edit_artifact(artifact['id'], 1, 'Manual newer title', 'markdown', 'Newer human version')
        hub.update_context(artifact['room_id'], 'Z' * 64000)
        for index in range(4):
            hub.add_source(artifact['room_id'], f'Source {index}', 'text', 'Y' * 150000)
        claim = self.claim(request)
        self.assertEqual(claim['context']['modification'], snapshot)
        self.assertEqual(claim['context']['agent'], snapshot['agent'])
        self.assertEqual(snapshot['baseline']['content'], artifact['content'])
        self.assertEqual([comment['body'] for comment in snapshot['comments']], [root['body'], reply['body']])
        self.assertTrue(claim['context']['omissions'])
        self.assertLessEqual(len(json.dumps(claim['context'], ensure_ascii=False).encode()), hub.MAX_CONTEXT)
        with contextlib.closing(sqlite3.connect(Path(self.temp.name) / 'studio.sqlite3')) as conn:
            raw = conn.execute('SELECT snapshot_json FROM hub_modifications WHERE id=?', (request['id'],)).fetchone()[0]
        self.assertEqual(json.loads(raw), snapshot)

    def test_round_trip_is_linked_idempotent_and_does_not_resolve_or_approve(self):
        artifact = self.artifact()
        root = self.comment(artifact)
        hub.review_artifact(artifact['id'], 1, 'changes_requested', 'Review stays independent.')
        request = self.request(artifact, [root])
        claim = self.claim(request)
        self.assertEqual(claim['task']['modification_request_id'], request['id'])
        payload = self.result(request)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            finished = list(pool.map(lambda _: hub.complete_task(claim['task']['id'], 'fixture-worker', claim['lease_token'], payload), range(4)))
        self.assertTrue(all(item['runs'][-1]['status'] == 'completed' for item in finished))
        result = hub.get_artifact(artifact['id'])
        self.assertEqual(result['version'], 2)
        self.assertEqual(result['source_version'], 1)
        self.assertEqual(result['modification_request_id'], request['id'])
        self.assertEqual(result['versions'][0]['modification_request_id'], request['id'])
        self.assertIsNone(result['versions'][1]['modification_request_id'])
        self.assertEqual(collab.get_comment(root['id'])['status'], 'open')
        self.assertEqual([review['decision'] for review in result['reviews']], ['changes_requested'])
        saved = collab.get_modification(request['id'])
        self.assertEqual((saved['status'], saved['result_version'], saved['candidate']), ('completed', 2, None))
        self.assert_error(409, self.finish, claim, request, content='Different repeated output')
        self.assertEqual(len(result['versions']), 2)
        public = json.dumps(collab.list_modifications(artifact['id']))
        self.assertNotIn('snapshot', public)
        self.assertNotIn(claim['lease_token'], public)
        self.assertNotIn('lease_token', public)

    def test_conflicting_completion_keeps_candidate_then_explicit_atomic_apply(self):
        artifact = self.artifact()
        request = self.request(artifact)
        claim = self.claim(request)
        hub.edit_artifact(artifact['id'], 1, 'Human version', 'markdown', 'Never overwrite this silently.')
        self.finish(claim, request, content='Candidate exact\r\ntext\n')
        conflict = collab.get_modification(request['id'])
        self.assertEqual((conflict['status'], conflict['task_status']), ('conflict', 'completed'))
        self.assertEqual(hub.get_artifact(artifact['id'])['content'], 'Never overwrite this silently.')
        self.assertEqual(conflict['candidate']['content'], 'Candidate exact\r\ntext\n')
        self.assertEqual(conflict['candidate']['sha256'], hashlib.sha256('Candidate exact\r\ntext\n'.encode()).hexdigest())
        self.assertNotIn('content', collab.list_modifications(artifact['id'])['modifications'][0]['candidate'])
        self.finish(claim, request, content='Candidate exact\r\ntext\n')
        self.assertEqual(hub.get_artifact(artifact['id'])['version'], 2)
        self.assert_error(409, collab.apply_candidate, request['id'], 1, 'apply-key')
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: collab.apply_candidate(request['id'], 2, 'apply-key'), range(4)))
        self.assertTrue(all(item['result_version'] == 3 for item in results))
        applied = hub.get_artifact(artifact['id'])
        self.assertEqual((applied['version'], applied['source_version']), (3, 1))
        self.assertEqual(applied['content'], conflict['candidate']['content'])
        self.assertEqual(applied['modification_request_id'], request['id'])
        hub.edit_artifact(artifact['id'], 3, 'Human v4', 'markdown', 'Latest human change')
        self.assertEqual(collab.apply_candidate(request['id'], 2, 'apply-key')['result_version'], 3)
        self.assertEqual(hub.get_artifact(artifact['id'])['content'], 'Latest human change')
        self.assert_error(409, collab.apply_candidate, request['id'], 4, 'another-apply')
        self.assertEqual(collab.get_modification(request['id'])['snapshot'], request['snapshot'])

    def test_concurrent_different_results_only_commit_one_revision(self):
        artifact = self.artifact()
        request = self.request(artifact)
        claim = self.claim(request)
        def attempt(index):
            try:
                self.finish(claim, request, content=f'Candidate {index}')
                return index
            except hub.HubError as error:
                self.assertEqual(error.status, 409)
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(attempt, range(5)))
        winners = [item for item in results if item is not None]
        self.assertEqual(len(winners), 1)
        artifact = hub.get_artifact(artifact['id'])
        self.assertEqual(artifact['version'], 2)
        self.assertEqual(artifact['content'], f'Candidate {winners[0]}')

    def test_two_requests_for_one_baseline_do_not_overwrite_each_other(self):
        artifact = self.artifact()
        comment = self.comment(artifact)
        first = self.request(artifact, [comment])
        second = self.request(artifact, [comment])
        claim = self.claim(first)
        self.assertIsNone(hub.claim_task('other', room_id=artifact['room_id'])['task'])
        self.finish(claim, first, content='First revision')
        second_claim = self.claim(second)
        self.finish(second_claim, second, content='Second candidate')
        self.assertEqual(hub.get_artifact(artifact['id'])['content'], 'First revision')
        self.assertEqual(collab.get_modification(second['id'])['status'], 'conflict')

    def test_invalid_targets_new_artifacts_plan_and_wrong_baseline_are_atomic(self):
        artifact = self.artifact()
        other = self.artifact(room_id=artifact['room_id'])
        request = self.request(artifact)
        claim = self.claim(request)
        base = self.counts()
        variations = []
        for target, version in [(other['id'], 1), (None, 1), (artifact['id'], 2), (artifact['id'], 1.0), (artifact['id'], True)]:
            payload = self.result(request)
            payload['artifacts'][0].update(id=target, base_version=version)
            variations.append((409, payload))
        for payload in [dict(self.result(request), artifacts=[]), dict(self.result(request), plan=[{'key': 'other'}]), dict(self.result(request), artifacts=self.result(request)['artifacts'] * 2)]:
            variations.append((400, payload))
        variations.append((409, dict(self.result(request), modification_request_id='wrong-request')))
        bad_json = self.result(request)
        bad_json['artifacts'][0].update(kind='json', content='{broken}')
        variations.append((400, bad_json))
        for status, payload in variations:
            self.assert_error(status, hub.complete_task, claim['task']['id'], 'fixture-worker', claim['lease_token'], payload)
            self.assertEqual(self.counts(), base)
            self.assertEqual(collab.get_modification(request['id'])['status'], 'running')
        self.finish(claim, request)
        self.assertEqual(hub.get_artifact(other['id'])['version'], 1)

    def test_expired_lease_reclaim_rejects_old_results_with_snapshot_intact(self):
        artifact = self.artifact()
        request = self.request(artifact)
        old = self.claim(request)
        with hub._transaction() as conn:
            conn.execute('UPDATE hub_tasks SET lease_expires_at=? WHERE id=?', ('2000-01-01T00:00:00+00:00', request['task_id']))
        self.assertEqual(collab.get_modification(request['id'])['status'], 'queued')
        new = self.claim(request, worker='replacement')
        self.assertNotEqual(old['lease_token'], new['lease_token'])
        self.assert_error(409, self.finish, old, request)
        self.assertIsNone(collab.get_modification(request['id'])['candidate'])
        self.assertEqual(new['context']['modification'], request['snapshot'])
        self.finish(new, request, worker='replacement')

    def test_cancel_and_failure_retry_follow_real_task_state(self):
        artifact = self.artifact()
        first = self.request(artifact)
        old = self.claim(first)
        hub.cancel_run(first['run_id'])
        self.assertEqual(collab.get_modification(first['id'])['status'], 'cancelled')
        self.assert_error(409, self.finish, old, first)
        self.assert_error(409, hub.retry_run, first['run_id'])
        root = self.comment(artifact)
        second = self.request(artifact, [root])
        claimed = self.claim(second)
        hub.fail_task(claimed['task']['id'], 'fixture-worker', claimed['lease_token'], 'Fixture host failure')
        failed = collab.get_modification(second['id'])
        self.assertEqual((failed['status'], failed['error']), ('failed', 'Fixture host failure'))
        collab.update_comment(root['id'], 1, body='Changed after failure')
        hub.retry_run(second['run_id'])
        retried = self.claim(second)
        self.assertEqual(retried['context']['modification'], second['snapshot'])
        self.assertEqual(retried['task']['attempts'], 2)
        self.finish(retried, second)
        self.assertEqual(collab.get_modification(second['id'])['result_version'], 2)

    def test_creation_failure_rolls_back_request_task_message_and_key_together(self):
        artifact = self.artifact()
        root = self.comment(artifact)
        before = self.counts()
        message_count = len(hub.get_room(artifact['room_id'])['messages'])
        with patch.object(collab, '_save_key', side_effect=RuntimeError('fixture disk failure after request insert')):
            with self.assertRaises(RuntimeError):
                collab.create_modification(artifact['id'], 1, [root['id']], 'atomic-key')
        self.assertEqual(self.counts(), before)
        self.assertEqual(len(hub.get_room(artifact['room_id'])['messages']), message_count)
        self.assertEqual(collab.create_modification(artifact['id'], 1, [root['id']], 'atomic-key')['status'], 'queued')

    def test_limits_reject_before_enqueue_instead_of_omitting_baseline(self):
        artifact = self.artifact('X' * (collab.MAX_BASELINE + 1))
        root = self.comment(artifact)
        before = self.counts()
        self.assert_error(413, self.request, artifact, [root])
        self.assertEqual(self.counts(), before)
        smaller = self.artifact()
        discussion = self.comment(smaller, body='汉' * 4000)
        for _ in range(6):
            self.comment(smaller, parent_id=discussion['id'], body='汉' * 4000)
        before = self.counts()
        self.assert_error(413, self.request, smaller, [discussion])
        self.assertEqual(self.counts(), before)
        large_context = self.artifact('B' * 120000)
        short = self.comment(large_context)
        hub.update_agent('writer', 'Large instructions', '', '汉' * 16000)
        before = self.counts()
        self.assert_error(413, self.request, large_context, [short])
        self.assertEqual(self.counts(), before)

    def test_cross_artifact_version_workspace_and_viewer_boundaries(self):
        artifact = self.artifact()
        other = self.artifact()
        root = self.comment(artifact)
        self.assert_error(409, self.comment, other, parent_id=root['id'])
        self.assert_error(409, self.request, other, [root])
        newer = hub.edit_artifact(artifact['id'], 1, 'v2', 'markdown', 'New content')
        self.assert_error(409, self.request, newer, [root])
        historical = self.request(artifact, [root])
        self.assertEqual(historical['base_version'], 1)
        self.assertEqual(collab.list_comments(artifact['id'], 2)['comments'], [])
        token = core.REQUEST_DATA_ROOT.set(Path(self.temp.name) / 'another-workspace')
        try:
            self.assert_error(404, collab.get_comment, root['id'])
            self.assert_error(404, collab.get_modification, historical['id'])
            self.assert_error(404, collab.list_modifications, artifact['id'])
        finally:
            core.REQUEST_DATA_ROOT.reset(token)
        token = core.REQUEST_ACTOR.set({'user_id': 'viewer', 'user_name': 'Viewer', 'role': 'viewer'})
        try:
            self.assertEqual(collab.get_comment(root['id'])['id'], root['id'])
            self.assert_error(403, self.comment, artifact)
            self.assert_error(403, collab.update_comment, root['id'], 1, status='resolved')
            self.assert_error(403, self.request, artifact, [root])
            self.assert_error(403, collab.apply_candidate, historical['id'], 2, 'viewer-apply')
        finally:
            core.REQUEST_ACTOR.reset(token)

    def test_http_contract_author_provenance_and_version_filters(self):
        artifact = self.artifact()
        token = core.REQUEST_ACTOR.set({'user_id': 'author-1', 'user_name': 'Editor One', 'role': 'editor'})
        try:
            status, comment, _, _ = hub_http.dispatch('POST', f'/api/hub/artifacts/{artifact["id"]}/comments', {'artifact_version': 1, 'body': 'Explain the opening.', 'request_key': 'http-comment'})
            self.assertEqual(status, 201)
            self.assertEqual(comment['author_id'], 'author-1')
            listed = hub_http.dispatch('GET', f'/api/hub/artifacts/{artifact["id"]}/comments', query={'version': ['1']})[1]
            self.assertEqual(listed['comments'][0]['id'], comment['id'])
            updated = hub_http.dispatch('PUT', f'/api/hub/comments/{comment["id"]}', {'base_version': 1, 'body': 'Make it precise.'})
            self.assertEqual(updated[0], 200)
            self.assertNotEqual(collab.list_comments(artifact['id'])['revision'], listed['revision'])
            self.assertEqual(len(hub_http.dispatch('GET', f'/api/hub/comments/{comment["id"]}')[1]['history']), 2)
            status, request, _, _ = hub_http.dispatch('POST', f'/api/hub/artifacts/{artifact["id"]}/modifications', {'base_version': 1, 'comment_ids': [comment['id']], 'agent_id': 'reviewer', 'request_key': 'http-mod'})
            self.assertEqual(status, 201)
            self.assertEqual((request['source_agent_id'], request['agent_id'], request['created_by_name']), ('writer', 'reviewer', 'Editor One'))
            self.assertEqual(hub_http.dispatch('GET', f'/api/hub/modifications/{request["id"]}')[1]['snapshot'], request['snapshot'])
            self.assertEqual(hub_http.dispatch('POST', f'/api/hub/artifacts/{artifact["id"]}/comments', {'artifact_version': 1, 'body': 'Missing key'})[0], 400)
            self.assertEqual(hub_http.dispatch('GET', f'/api/hub/artifacts/{artifact["id"]}/comments', query={'version': ['1', '2']})[0], 400)
            self.assertEqual(hub_http.dispatch('GET', '/api/hub/modifications/missing')[0], 404)
            self.assertEqual(hub_http.dispatch('PUT', f'/api/hub/comments/{comment["id"]}', {'base_version': 2, 'body': 'Relocate?', 'anchor': {'quote': 'other'}})[0], 400)
            self.assert_error(404, collab.create_comment, float('nan'), 1, 'Invalid identifier', 'malformed-id')
            self.assert_error(404, collab.create_modification, [], 1, [comment['id']], 'malformed-mod')
            self.assert_error(404, collab.apply_candidate, {}, 1, 'malformed-apply')
        finally:
            core.REQUEST_ACTOR.reset(token)


if __name__ == '__main__':
    unittest.main()
