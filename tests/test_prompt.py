import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tokenograph import TranscriptReader
from tokenograph.live import snapshot
from tokenograph.prompt import Collector, compact_snapshot, read_summary, summary, write_cache


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(__file__).parent / "fixtures/codex.jsonl"
        reader = TranscriptReader(self.fixture)
        reader.refresh()
        self.data = compact_snapshot(snapshot(reader.entries, self.fixture, now=1789129000))

    def test_summary_uses_confirmed_counters_and_keeps_cost_provenance(self):
        self.assertEqual(self.data["tokens"], 2550)
        text = summary(self.data, now=1789129000)
        self.assertIn("tok 2.5k", text)
        self.data.update(cost=1.25, cost_reported=None, partial=True)
        self.assertIn("~$1.25", summary(self.data))
        self.assertIn("partial usage", summary(self.data))
        self.data["cost_reported"] = 0
        self.assertIn("$0.00", summary(self.data))
        self.assertNotIn("~$", summary(self.data))
        self.data.update(cost=None, cost_reported=None)
        self.assertIn("cost —", summary(self.data))

    def test_unknown_usage_is_not_measured_zero_and_stale_is_not_live(self):
        self.data["usage_at"] = None
        self.assertIn("usage pending", summary(self.data, now=1789129001))
        self.assertNotIn("tok 2.5k", summary(self.data))
        self.assertIn("stale 30s", summary(self.data, now=1789129030))

    def test_cache_allowlist_excludes_prompt_paths_commands_and_controls(self):
        reader = TranscriptReader(self.fixture)
        reader.refresh()
        data = snapshot(reader.entries, self.fixture, now=1789129000)
        data["observed_tools"] = [{"name": "tool\n$(bad)`bad`\x1b", "label": "secret command",
                                   "detail": "private arguments", "id": "private id"}]
        result = compact_snapshot(data)
        text = summary(result)
        self.assertNotIn("secret", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))
        for control in ("\n", "\x1b", "$(`", "`", "$("):
            self.assertNotIn(control, text)

    def test_atomic_cache_is_private_and_bad_cache_is_silent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "summary.json"
            self.assertEqual(read_summary(path), "")
            write_cache(path, self.data)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertIn("tok 2.5k", read_summary(path))
            for broken in ("{", "[]", "x" * 8193, '{"version": 1}',
                           json.dumps(dict(self.data, tokens="$(bad)"))):
                path.write_text(broken)
                self.assertEqual(read_summary(path), "")

    def test_unchanged_transcript_only_updates_heartbeat_and_does_not_reanalyze(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "session.jsonl"
            source.write_bytes(self.fixture.read_bytes())
            cache = Path(directory) / "summary.json"
            collector = Collector(source, cache)
            with patch("tokenograph.prompt.snapshot", wraps=snapshot) as analyze:
                collector.refresh(now=1789129000)
                first = json.loads(cache.read_text())
                collector.refresh(now=1789129005)
                second = json.loads(cache.read_text())
                self.assertEqual(analyze.call_count, 1)
                self.assertEqual(first["tokens"], second["tokens"])
                self.assertEqual(second["checked_at"], 1789129005)
                with source.open("a") as handle:
                    handle.write('{"type":"token_usage_record","payload":{"input_tokens":999999}}\n')
                collector.refresh(now=1789129006)
                self.assertEqual(analyze.call_count, 2)
                self.assertEqual(json.loads(cache.read_text())["tokens"], 2550)
            source.unlink()
            with self.assertRaises(FileNotFoundError):
                collector.refresh()


if __name__ == "__main__":
    unittest.main()
