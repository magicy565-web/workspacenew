import argparse
from copy import deepcopy
import unittest

from studio import codex_executor


SESSION = '660c5984-c980-446d-9064-cbcfa9109c74'
OTHER = '948269b1-92e1-44d2-8ba1-ceab28f75c18'


class AgentContinuationTests(unittest.TestCase):
    def execution(self):
        return {
            'id': 'exec_current', 'task_id': 'current', 'attempt': 1,
            'host_identity': 'caller-profile', 'recovery': {'decision': 'start'},
            'agent_session': {'id': 'session_stable', 'generation': 1, 'turn_index': 2,
                'continuation': {'decision': 'continue', 'reason': 'matching_completed_turn',
                    'from_task_id': 'previous', 'from_execution_id': 'exec_previous',
                    'checkpoint': {'host': 'codex', 'host_identity': 'caller-profile',
                                   'session_id': SESSION, 'worker_id': 'caller', 'checkpoint_id': 'saved'}}}}

    def command(self, execution):
        return codex_executor.build_command('caller-codex', argparse.Namespace(sandbox='read-only', profile='my-profile', model='my-model'), 'schema', 'result', execution)

    def test_new_task_uses_exact_completed_native_thread_and_preserves_caller_configuration(self):
        execution = self.execution()
        before = deepcopy(execution)
        command = self.command(execution)
        self.assertEqual(command[command.index('resume') + 1], SESSION)
        self.assertEqual(command[command.index('--profile') + 1], 'my-profile')
        self.assertEqual(command[command.index('--model') + 1], 'my-model')
        self.assertEqual(command[command.index('--sandbox') + 1], 'read-only')
        self.assertNotIn('--last', command)
        self.assertNotIn('--ephemeral', command)
        self.assertEqual(execution, before)

    def test_task_recovery_overrides_prior_turn_branch(self):
        execution = self.execution()
        execution.update(attempt=2, recovery={'decision': 'continue', 'checkpoint': {
            'host': 'codex', 'host_identity': 'caller-profile', 'session_id': OTHER}})
        self.assertEqual(codex_executor.continuation_session(execution), OTHER)
        execution['recovery'] = {'decision': 'restart'}
        self.assertNotIn('resume', self.command(execution))
        for decision in ('submit_result', 'review_required', 'completed', 'cancelled'):
            with self.subTest(decision=decision), self.assertRaises(ValueError):
                execution['recovery'] = {'decision': decision}
                self.command(execution)

    def test_cross_turn_cannot_be_used_to_retry_or_forge_source_receipt(self):
        for change in ('attempt', 'same_task', 'mismatched_execution', 'missing_source'):
            execution = self.execution()
            continuation = execution['agent_session']['continuation']
            if change == 'attempt':
                execution['attempt'] = 2
            elif change == 'same_task':
                continuation['from_task_id'] = 'current'
            elif change == 'mismatched_execution':
                continuation['from_execution_id'] = 'exec_unrelated'
            else:
                continuation['from_task_id'] = None
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.command(execution)

    def test_unsupported_host_mismatch_and_invalid_uuid_do_not_fallback(self):
        for key, value in (('host', 'another-adapter'), ('host_identity', 'another-machine'), ('session_id', '--last'), ('session_id', None)):
            execution = self.execution()
            execution['agent_session']['continuation']['checkpoint'][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.command(execution)

    def test_native_receipt_must_match_for_cross_turn_and_task_continuation(self):
        for same_task in (False, True):
            execution = self.execution()
            if same_task:
                execution['recovery'] = {'decision': 'continue', 'checkpoint': execution['agent_session']['continuation']['checkpoint']}
            events = []
            bridge = codex_executor.CodexEvents(execution, events.append)
            with self.subTest(same_task=same_task), self.assertRaises(ValueError):
                bridge.consume({'type': 'thread.started', 'thread_id': OTHER})
            self.assertTrue(bridge.unknown)
            self.assertEqual([item['kind'] for item in events], ['tool_call', 'tool_result'])
            self.assertEqual(events[-1]['payload']['outcome'], 'unknown')
            self.assertFalse(any(item['kind'] == 'checkpoint' for item in events))

    def test_actual_matching_receipt_creates_new_ledger_events_without_old_sequence(self):
        events = []
        bridge = codex_executor.CodexEvents(self.execution(), events.append)
        bridge.consume({'type': 'thread.started', 'thread_id': SESSION})
        bridge.consume({'type': 'turn.completed'})
        self.assertTrue(bridge.completed)
        self.assertEqual(events[0]['kind'], 'host_session')
        self.assertTrue(all('through_seq' not in item['payload'] for item in events))
        self.assertEqual(events[-1]['payload']['session_id'], SESSION)

    def test_fresh_generation_and_legacy_envelope_start_new_native_context(self):
        execution = self.execution()
        execution['agent_session']['continuation'].update(decision='fresh', reason='team_permissions_changed')
        self.assertNotIn('resume', self.command(execution))
        del execution['agent_session']
        self.assertNotIn('resume', self.command(execution))
        self.assertIn('--ephemeral', self.command(None))


if __name__ == '__main__':
    unittest.main()
