import argparse
import contextlib
import io
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch

from studio import codex_executor, hub, hub_execution, hub_http, worker


class DirectClient:
    deadline = None

    def __init__(self, delay=0):
        self.delay, self.heartbeats = delay, 0

    def post(self, path, data):
        if path.endswith('/heartbeat'):
            self.heartbeats += 1
        if path.endswith('/execution/events'):
            time.sleep(self.delay)
        return hub_http.dispatch('POST', '/api/hub' + path, data)[1]


class ExecutorEventTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.environment = patch.dict(os.environ, {'STUDIO_DATA_DIR': self.folder.name})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def stream(self, delay=0):
        room = hub.create_room('执行记录验证', agent_id='writer')
        config = {'worker_id': 'stream-test', 'command': ['fixture'], 'output_protocol': 'jsonl'}
        claim = hub.claim_task('stream-test', room_id=room['id'], host_identity=worker.host_identity(config))
        client = DirectClient(delay)
        client.deadline = time.monotonic() + 10
        pulse = worker.LeasePulse(config, claim, client, 0.015)
        return worker.ExecutionStream(io.BytesIO(), config, claim, client, pulse), claim, client

    @staticmethod
    def record(stream, event_id, kind, payload):
        stream.record(json.dumps({'type': 'execution_event', 'event_id': event_id, 'kind': kind, 'payload': payload}).encode())

    def test_checkpoint_duplicate_after_other_events_keeps_original_fingerprint(self):
        stream, claim, client = self.stream()
        point = {'host': 'fixture', 'session_id': 'session-one', 'checkpoint_id': 'saved-one'}
        self.record(stream, 'host', 'host_session', {key: point[key] for key in ('host', 'session_id')})
        self.record(stream, 'point', 'checkpoint', point)
        self.record(stream, 'progress', 'progress', {'message': '下一步'})
        self.record(stream, 'point', 'checkpoint', point)
        events = hub_execution.list_events(claim['execution']['id'])['events']
        checkpoints = [item for item in events if item['kind'] == 'checkpoint']
        self.assertEqual(len(checkpoints), 1)
        self.assertLess(checkpoints[0]['payload']['through_seq'], checkpoints[0]['seq'])
        with self.assertRaises(ValueError):
            self.record(stream, 'point', 'checkpoint', dict(point, checkpoint_id='changed'))

    def test_event_batch_renews_lease_inside_drain(self):
        stream, claim, client = self.stream(delay=0.02)
        records = [{'type': 'execution_event', 'event_id': f'event-{n}', 'kind': 'progress', 'payload': {'message': f'步骤 {n}'}} for n in range(8)]
        stream.reader = io.BytesIO(('\n'.join(json.dumps(item) for item in records) + '\n').encode())
        stream.drain(final=True)
        self.assertGreaterEqual(client.heartbeats, 4)
        self.assertTrue(hub_execution.get_execution(claim['task']['id'])['active'])

    def bridge(self):
        events = []
        bridge = codex_executor.CodexEvents({'attempt': 1, 'host_identity': 'test-host', 'recovery': {'decision': 'start'}}, events.append)
        return bridge, events

    def test_private_model_and_tool_fields_do_not_enter_public_tape(self):
        bridge, events = self.bridge()
        bridge.consume({'type': 'item.completed', 'item': {'id': 'r', 'type': 'reasoning', 'text': 'PRIVATE_TEST_REASONING'}})
        bridge.consume({'type': 'item.started', 'item': {'id': 'c', 'type': 'command_execution', 'command': 'PRIVATE_TEST_COMMAND', 'arguments': 'PRIVATE_TEST_ARGS'}})
        bridge.consume({'type': 'item.completed', 'item': {'id': 'c', 'type': 'command_execution', 'exit_code': 0, 'status': 'completed', 'aggregated_output': 'PRIVATE_TEST_OUTPUT'}})
        self.assertNotIn('PRIVATE_TEST_', json.dumps(events))
        self.assertEqual([e['kind'] for e in events], ['tool_call', 'tool_result'])
        self.assertEqual(events[-1]['payload']['outcome'], 'succeeded')

    def test_unknown_protocol_and_missing_call_id_leave_review_receipt(self):
        for incoming in ({'type': 'future.tool.started'}, {'type': 'item.started', 'item': {'type': 'command_execution'}}):
            bridge, events = self.bridge()
            with self.assertRaises(ValueError):
                bridge.consume(incoming)
            self.assertTrue(bridge.unknown)
            self.assertFalse(events[0]['payload']['retry_safe'])
            self.assertEqual(events[-1]['payload']['outcome'], 'unknown')

    def test_failed_command_does_not_assert_no_side_effect(self):
        bridge, events = self.bridge()
        bridge.consume({'type': 'item.completed', 'item': {'id': 'c', 'type': 'command_execution', 'exit_code': 1, 'status': 'failed'}})
        self.assertTrue(bridge.unknown)
        self.assertEqual(events[-1]['payload']['outcome'], 'unknown')

    def test_resume_is_bound_to_exact_host_session_with_no_silent_fallback(self):
        args = argparse.Namespace(sandbox='read-only', profile=None, model=None)
        session = 'beea6ee2-ed5f-458c-835a-85c0309290ce'
        execution = {'host_identity': 'same-host', 'recovery': {'decision': 'continue', 'checkpoint': {'host': 'codex', 'host_identity': 'same-host', 'session_id': session}}}
        command = codex_executor.build_command('codex', args, 'schema', 'result', execution)
        self.assertEqual(command[command.index('resume') + 1], session)
        self.assertNotIn('--last', command)
        self.assertNotIn('--ephemeral', command)
        self.assertIn('read-only', command)
        execution['host_identity'] = 'another-host'
        with self.assertRaises(ValueError):
            codex_executor.build_command('codex', args, 'schema', 'result', execution)


if __name__ == '__main__':
    unittest.main()
