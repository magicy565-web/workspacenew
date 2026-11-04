"""Protocol tests against real temporary SQLite files, not live customer actions."""
import copy
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest

from studio.action_approvals import ApprovalError, ApprovalLedger, Authority, MAX_TTL_MS, OPERATIONS


class ActionApprovalsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'studio.sqlite3'
        self.now = 1_800_000_000_000
        self.expires = self.now + 60_000
        self.subject = 'worker-1'
        self.kind = 'worker'
        self.workspace = 'workspace-1'
        self.revision = 'task-lease-and-policy-v1'
        self.grants = OPERATIONS
        self.action = {
            'workspace_id': self.workspace, 'task_id': 'task-1',
            'execution_id': 'execution-1', 'executor_id': 'worker-1',
            'action': 'outreach.send',
            'parameters': {'recipient': 'protocol-test@example.invalid',
                           'body': 'Protocol test only; no delivery tool is registered.'},
            'budget': {'currency': 'USD', 'max_cost_minor': 0},
        }
        self.ledger = ApprovalLedger(self.path, self.authorize, clock_ms=lambda: self.now)

    def authorize(self, operation, action):
        return Authority(self.subject, self.workspace, self.kind, self.revision, self.grants)

    def human(self):
        self.subject, self.kind = 'human-1', 'human'

    def worker(self):
        self.subject, self.kind = 'worker-1', 'worker'

    def request(self, key='action-1'):
        return self.ledger.request(self.action, key, self.expires)

    def approved(self):
        record = self.request()
        self.human()
        result = self.ledger.decide(record['id'], record['digest'], 'approve', 'Reviewed exact action')
        self.worker()
        return result

    def assert_error(self, status, function, *args, **kwargs):
        with self.assertRaises(ApprovalError) as caught:
            function(*args, **kwargs)
        self.assertEqual(status, caught.exception.status)

    def test_request_starts_pending_with_immutable_copy(self):
        record = self.request()
        self.assertEqual('pending', record['state'])
        record['envelope']['action']['parameters']['recipient'] = 'modified@example.invalid'
        self.action['parameters']['body'] = 'changed locally'
        stored = self.ledger.inspect(record['id'])
        self.assertEqual('protocol-test@example.invalid', stored['envelope']['action']['parameters']['recipient'])
        self.assertNotEqual('changed locally', stored['envelope']['action']['parameters']['body'])

    def test_pending_cannot_be_consumed(self):
        record = self.request()
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])
        self.assertEqual(1, len(self.ledger.inspect(record['id'])['events']))

    def test_worker_cannot_approve_even_with_decide_grant(self):
        record = self.request()
        self.assert_error(403, self.ledger.decide, record['id'], record['digest'], 'approve')

    def test_human_without_decide_grant_cannot_approve(self):
        record = self.request()
        self.human()
        self.grants = frozenset({'read'})
        self.assert_error(403, self.ledger.decide, record['id'], record['digest'], 'approve')

    def test_successful_approval_is_not_execution_and_consumes_once(self):
        record = self.approved()
        self.assertEqual('approved', record['state'])
        self.assertIsNone(record['consumption_id'])
        receipt = self.ledger.consume(record['id'], record['digest'])
        self.assertEqual('consumed', receipt['state'])
        self.assertTrue(receipt['consumption_id'])
        self.assertNotIn('completed', receipt)
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])
        self.assertEqual(['requested', 'approved', 'consumed'],
                         [e['event'] for e in self.ledger.inspect(record['id'])['events']])

    def test_rejection_is_terminal(self):
        record = self.request()
        self.human()
        self.ledger.decide(record['id'], record['digest'], 'reject', 'Not authorized')
        self.assert_error(409, self.ledger.decide, record['id'], record['digest'], 'approve')
        self.worker()
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])

    def test_pending_and_approved_can_be_revoked(self):
        for approve in (False, True):
            with self.subTest(approved=approve):
                self.worker()
                record = self.request(f'revoke-{approve}')
                self.human()
                if approve:
                    self.ledger.decide(record['id'], record['digest'], 'approve')
                self.ledger.revoke(record['id'], record['digest'], 'Permission withdrawn')
                self.worker()
                self.assert_error(409, self.ledger.consume, record['id'], record['digest'])

    def test_revocation_requires_human_and_nonempty_reason(self):
        record = self.approved()
        self.assert_error(403, self.ledger.revoke, record['id'], record['digest'], 'reason')
        self.human()
        self.assert_error(400, self.ledger.revoke, record['id'], record['digest'], '   ')

    def test_consumed_permission_cannot_be_undone_by_revocation(self):
        record = self.approved()
        self.ledger.consume(record['id'], record['digest'])
        self.human()
        self.assert_error(409, self.ledger.revoke, record['id'], record['digest'], 'Too late')

    def test_expiry_boundary_blocks_pending_approval(self):
        record = self.request()
        self.human()
        self.now = self.expires
        self.assert_error(409, self.ledger.decide, record['id'], record['digest'], 'approve')
        self.assertEqual('expired', self.ledger.inspect(record['id'])['state'])

    def test_expiry_boundary_blocks_consumption(self):
        record = self.approved()
        self.now = self.expires
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])

    def test_observed_expiry_is_terminal_after_clock_rollback(self):
        record = self.approved()
        self.now = self.expires
        self.assertEqual('expired', self.ledger.inspect(record['id'])['state'])
        self.now -= 30_000
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])
        stored = self.ledger.inspect(record['id'])
        self.assertEqual('expired', stored['state'])
        self.assertEqual(1, sum(e['event'] == 'expired' for e in stored['events']))

    def test_failed_expired_consumption_commits_expiration(self):
        record = self.approved()
        self.now = self.expires
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])
        self.now -= 30_000
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])
        self.assertEqual('expired', self.ledger.inspect(record['id'])['state'])

    def test_failed_expired_decision_commits_expiration(self):
        record = self.request()
        self.human()
        self.now = self.expires
        self.assert_error(409, self.ledger.decide, record['id'], record['digest'], 'approve')
        self.now -= 30_000
        self.assert_error(409, self.ledger.decide, record['id'], record['digest'], 'approve')

    def test_expiry_is_checked_after_live_authorization(self):
        record = self.approved()
        def slow_authorizer(operation, action):
            self.now = self.expires
            return self.authorize(operation, action)
        self.ledger.authorize = slow_authorizer
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])

    def test_expiry_must_be_finite_integer_in_next_day(self):
        for value in (True, 1.5, 'tomorrow', self.now, self.now - 1, self.now + MAX_TTL_MS + 1):
            with self.subTest(expiry=value):
                self.assert_error(400, self.ledger.request, self.action, 'invalid-expiry', value)

    def test_same_request_key_is_idempotent_including_after_consumption(self):
        record = self.approved()
        self.ledger.consume(record['id'], record['digest'])
        replay = self.request()
        self.assertEqual(record['id'], replay['id'])
        self.assertEqual('consumed', replay['state'])
        self.assertEqual(3, len(self.ledger.inspect(record['id'])['events']))

    def test_expired_request_retry_does_not_create_fresh_approval(self):
        record = self.request()
        self.now = self.expires
        replay = self.request()
        self.assertEqual(record['id'], replay['id'])
        self.assertEqual('expired', replay['state'])

    def test_reusing_key_cannot_change_action_parameters_budget_or_expiry(self):
        record = self.request()
        for change in ('recipient', 'body', 'cost', 'currency', 'task', 'execution', 'executor', 'action', 'expiry'):
            action = copy.deepcopy(self.action)
            expiry = self.expires
            if change in ('recipient', 'body'):
                action['parameters'][change] = 'changed'
            elif change == 'cost':
                action['budget']['max_cost_minor'] = 1
            elif change == 'currency':
                action['budget']['currency'] = 'EUR'
            elif change == 'expiry':
                expiry += 1
            else:
                action[{'task': 'task_id', 'execution': 'execution_id', 'executor': 'executor_id', 'action': 'action'}[change]] = 'changed'
            with self.subTest(change=change):
                self.assert_error(409, self.ledger.request, action, 'action-1', expiry)
        self.assertEqual('pending', self.ledger.inspect(record['id'])['state'])

    def test_other_requester_cannot_reuse_action_key(self):
        self.request()
        self.subject = 'worker-2'
        self.assert_error(409, self.request)

    def test_exact_digest_required_for_decision_and_consumption(self):
        record = self.request()
        self.human()
        self.assert_error(409, self.ledger.decide, record['id'], '0' * 64, 'approve')
        for bad in (None, '', '\N{SNOWMAN}' * 64, True):
            self.assert_error(400, self.ledger.decide, record['id'], bad, 'approve')
        self.ledger.decide(record['id'], record['digest'], 'approve')
        self.worker()
        self.assert_error(409, self.ledger.consume, record['id'], 'f' * 64)

    def test_digest_binds_authority_and_exact_canonical_envelope(self):
        record = self.request()
        payload = json.dumps(record['envelope'], ensure_ascii=False, sort_keys=True,
                             separators=(',', ':'), allow_nan=False)
        self.assertEqual(hashlib.sha256(payload.encode()).hexdigest(), record['digest'])
        self.revision = 'new-policy'
        self.assert_error(409, self.request)

    def test_authority_revision_change_blocks_approval_and_consumption(self):
        record = self.request()
        self.human()
        self.revision = 'new-lease'
        self.assert_error(409, self.ledger.decide, record['id'], record['digest'], 'approve')
        self.revision = 'task-lease-and-policy-v1'
        self.ledger.decide(record['id'], record['digest'], 'approve')
        self.worker()
        self.revision = 'new-membership-or-budget'
        self.assert_error(409, self.ledger.consume, record['id'], record['digest'])

    def test_revocation_can_close_an_approval_with_stale_authority(self):
        record = self.approved()
        self.human()
        self.revision = 'new-policy'
        self.assertEqual('revoked', self.ledger.revoke(record['id'], record['digest'], 'Authority changed')['state'])

    def test_live_grant_revocation_blocks_execution(self):
        record = self.approved()
        self.grants = frozenset({'read'})
        self.assert_error(403, self.ledger.consume, record['id'], record['digest'])

    def test_only_bound_worker_can_consume(self):
        record = self.approved()
        self.subject = 'worker-2'
        self.assert_error(403, self.ledger.consume, record['id'], record['digest'])
        self.human()
        self.assert_error(403, self.ledger.consume, record['id'], record['digest'])

    def test_workspace_isolation_for_read_request_decide_and_consume(self):
        record = self.approved()
        self.workspace = 'workspace-2'
        self.assert_error(403, self.ledger.inspect, record['id'])
        self.assert_error(403, self.ledger.consume, record['id'], record['digest'])
        self.assert_error(403, self.request)
        self.human()
        self.assert_error(403, self.ledger.decide, record['id'], record['digest'], 'approve')

    def test_same_action_key_is_separate_in_an_authorized_second_workspace(self):
        first = self.request()
        self.workspace = self.action['workspace_id'] = 'workspace-2'
        second = self.request()
        self.assertNotEqual(first['id'], second['id'])

    def test_authorizer_cannot_mutate_stored_action(self):
        def mutating(operation, action):
            action['parameters']['recipient'] = 'tampered@example.invalid'
            return self.authorize(operation, action)
        self.ledger.authorize = mutating
        record = self.approved()
        self.assertEqual(self.action, self.ledger.consume(record['id'], record['digest'])['envelope']['action'])

    def test_authorizer_required_and_fail_closed(self):
        self.assert_error(403, ApprovalLedger, self.path, None)
        for result in (None, True, {'kind': 'human'}):
            self.ledger.authorize = lambda operation, action, result=result: result
            self.assert_error(403, self.request)

    def test_authorizer_exception_rolls_back_without_consuming(self):
        record = self.approved()
        def failing(operation, action):
            raise RuntimeError('Authority service unavailable')
        self.ledger.authorize = failing
        with self.assertRaises(RuntimeError):
            self.ledger.consume(record['id'], record['digest'])
        self.ledger.authorize = self.authorize
        self.assertEqual('approved', self.ledger.inspect(record['id'])['state'])

    def test_audit_failure_rolls_back_consumption_atomically(self):
        record = self.approved()
        with sqlite3.connect(self.path) as conn:
            conn.execute("""CREATE TRIGGER fail_approval_event BEFORE INSERT ON hub_action_approval_events
                WHEN NEW.event='consumed' BEGIN SELECT RAISE(ABORT,'test event failure'); END""")
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.consume(record['id'], record['digest'])
        stored = self.ledger.inspect(record['id'])
        self.assertEqual('approved', stored['state'])
        self.assertIsNone(stored['consumption_id'])
        self.assertEqual(2, len(stored['events']))

    def test_invalid_json_and_budgets_rejected(self):
        invalid = [float('nan'), float('inf'), {1: 'not a string key'}, {'x': object()}, {'x': 2**60}, {'x': '\ud800'}]
        for value in invalid:
            action = copy.deepcopy(self.action)
            action['parameters'] = {'invalid': value}
            with self.subTest(value=repr(value)):
                self.assert_error(400, self.ledger.request, action, 'bad-json', self.expires)
        for cost in (True, -1, 0.5, '1', 2**60):
            action = copy.deepcopy(self.action)
            action['budget']['max_cost_minor'] = cost
            self.assert_error(400, self.ledger.request, action, 'bad-budget', self.expires)

    def test_unknown_fields_missing_budget_and_oversized_payload_rejected(self):
        cases = []
        extra = copy.deepcopy(self.action)
        extra['approved'] = True
        cases.append(extra)
        missing = copy.deepcopy(self.action)
        del missing['budget']
        cases.append(missing)
        huge = copy.deepcopy(self.action)
        huge['parameters']['body'] = 'x' * 65536
        cases.append(huge)
        for action in cases:
            self.assert_error(400, self.ledger.request, action, 'bad-schema', self.expires)

    def test_no_schema_collision_or_existing_data_changes(self):
        with sqlite3.connect(self.path) as conn:
            conn.execute('CREATE TABLE hub_tasks (id TEXT PRIMARY KEY, result_json TEXT)')
            conn.execute("INSERT INTO hub_tasks VALUES ('existing','existing-result')")
        self.approved()
        ApprovalLedger(self.path, self.authorize, clock_ms=lambda: self.now)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual([('existing', 'existing-result')], conn.execute('SELECT * FROM hub_tasks').fetchall())

    def test_concurrent_consumers_use_approval_once(self):
        record = self.approved()
        barrier = threading.Barrier(12)
        def consume(_):
            ledger = ApprovalLedger(self.path, self.authorize, clock_ms=lambda: self.now)
            barrier.wait(timeout=10)
            try:
                return ledger.consume(record['id'], record['digest'])['consumption_id']
            except ApprovalError as exc:
                self.assertEqual(409, exc.status)
                return None
        with ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(consume, range(12)))
        self.assertEqual(1, sum(value is not None for value in results))
        self.assertEqual(3, len(self.ledger.inspect(record['id'])['events']))

    def test_concurrent_identical_requests_create_one_record(self):
        barrier = threading.Barrier(8)
        def request(_):
            ledger = ApprovalLedger(self.path, self.authorize, clock_ms=lambda: self.now)
            barrier.wait(timeout=10)
            return ledger.request(self.action, 'one-action', self.expires)['id']
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(request, range(8)))
        self.assertEqual(1, len(set(ids)))
        self.assertEqual(1, len(self.ledger.inspect(ids[0])['events']))

    def test_concurrent_revocation_and_consumption_are_serialized(self):
        record = self.approved()
        barrier = threading.Barrier(2)
        def change(operation):
            actor = Authority('human-1' if operation == 'revoke' else 'worker-1',
                              self.workspace, 'human' if operation == 'revoke' else 'worker',
                              self.revision, OPERATIONS)
            ledger = ApprovalLedger(self.path, lambda op, action: actor, clock_ms=lambda: self.now)
            barrier.wait(timeout=10)
            try:
                if operation == 'revoke':
                    ledger.revoke(record['id'], record['digest'], 'Revoked during race')
                else:
                    ledger.consume(record['id'], record['digest'])
                return True
            except ApprovalError as exc:
                self.assertEqual(409, exc.status)
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(1, sum(pool.map(change, ('revoke', 'consume'))))
        self.assertIn(self.ledger.inspect(record['id'])['state'], ('revoked', 'consumed'))

    def test_process_exit_after_consumption_does_not_restore_permission(self):
        record = self.approved()
        code = '''
import os, sys
from studio.action_approvals import ApprovalLedger, Authority, OPERATIONS
actor = Authority('worker-1', 'workspace-1', 'worker', 'task-lease-and-policy-v1', OPERATIONS)
ledger = ApprovalLedger(sys.argv[1], lambda op, action: actor, clock_ms=lambda: int(sys.argv[4]))
ledger.consume(sys.argv[2], sys.argv[3])
os._exit(23)
'''
        result = subprocess.run([sys.executable, '-c', code, str(self.path), record['id'], record['digest'], str(self.now)],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=15)
        self.assertEqual(23, result.returncode, result.stderr)
        reopened = ApprovalLedger(self.path, self.authorize, clock_ms=lambda: self.now)
        self.assertEqual('consumed', reopened.inspect(record['id'])['state'])
        self.assert_error(409, reopened.consume, record['id'], record['digest'])


if __name__ == '__main__':
    unittest.main()
