import json
import unittest
from pathlib import Path

from tokenograph import TranscriptReader, analyze
from tokenograph.live import observed_tools, snapshot


class LiveTests(unittest.TestCase):
    def test_same_accounting_as_panel_and_no_duplicate_inner_tokens(self):
        reader = TranscriptReader(Path(__file__).parent / 'fixtures/codex.jsonl')
        reader.refresh()
        expected = analyze(reader.entries, live=True, now=1789129000)
        actual = snapshot(reader.entries, reader.path, now=1789129000)
        self.assertEqual(actual['stats'], expected['stats'])
        self.assertEqual(actual['stats']['tokens']['total'], 2550)
        # A provisional tail must never replace the authoritative counters.
        reader.entries.append({'type': 'token_usage_record', 'payload': {
            'input_tokens': 900000, 'output_tokens': 99999}})
        self.assertEqual(snapshot(reader.entries, reader.path)['stats']['tokens']['total'], 2550)

    def test_only_observed_tools_no_code_or_reasoning_inference(self):
        def entry(item):
            return {'type': 'event_msg', 'timestamp': '2026-09-11T12:00:00Z',
                    'payload': {'type': 'item_completed', 'item': item}}
        command = entry({'type': 'CommandExecution', 'id': 'cmd1', 'command': ['sh', '-c', 'false'],
                         'status': 'failed', 'exit_code': 1, 'duration': {'secs': 1, 'nanos': 500000000}})
        entries = [command, command, entry({'type': 'Reasoning', 'id': 'r', 'raw_content': 'private'}),
                   entry({'type': 'McpToolCall', 'id': 'm', 'server': 'docs', 'tool': 'read',
                          'arguments': {'query': 'help'}, 'status': 'completed'})]
        result = observed_tools(entries)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]['name'], 'docs.read')
        self.assertIsNone(result[0]['duration_s'])
        self.assertEqual(result[1]['duration_s'], 1.5)
        self.assertEqual(result[1]['status'], 'failed')
        self.assertNotIn('private', json.dumps(result))

    def test_abandoned_calls_do_not_look_running_in_later_turn(self):
        reader = TranscriptReader(Path(__file__).parent / 'fixtures/codex.jsonl')
        reader.refresh()
        reader.entries.extend([
            {'timestamp': '2026-09-11T13:00:00Z', 'type': 'response_item', 'payload': {
                'type': 'function_call', 'name': 'old_tool', 'call_id': 'lost', 'arguments': '{}'}},
            {'timestamp': '2026-09-11T14:00:00Z', 'type': 'event_msg', 'payload': {'type': 'task_started'}},
        ])
        self.assertEqual(snapshot(reader.entries, reader.path)['running'], [])


if __name__ == '__main__':
    unittest.main()
