import json
import os
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "codex.jsonl"
sys.path.insert(0, str(ROOT))

import tokenograph  # noqa: E402


def codex_entries():
    return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]


class CodexAdapterTests(unittest.TestCase):
    def test_provisional_usage_requires_changed_cumulative_totals(self):
        entries = codex_entries()
        counter = next(e for e in reversed(entries)
                       if e.get("payload", {}).get("type") == "token_count")
        usage = counter["payload"]["info"]["last_token_usage"]
        expected = tokenograph.analyze(entries)["stats"]["tokens"]["total"]
        entries.extend([
            {"timestamp": "2026-08-20T12:01:00Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "content": [
                 {"type": "output_text", "text": "Unconfirmed next response"}]}},
            {"timestamp": "2026-08-20T12:01:01Z", "type": "token_usage_record",
             "payload": {"usage": usage}},
            dict(counter, timestamp="2026-08-20T12:01:02Z"),
        ])
        self.assertEqual(tokenograph.analyze(entries)["stats"]["tokens"]["total"], expected)

    def test_format_detection(self):
        self.assertEqual(tokenograph.detect_format(codex_entries(), FIXTURE), "codex")

    def test_confirmed_counter_corrects_provisional_usage(self):
        entries = codex_entries()
        idx = next(i for i, e in enumerate(entries)
                   if e.get("payload", {}).get("type") == "token_count")
        usage = dict(entries[idx]["payload"]["info"]["last_token_usage"])
        usage["output_tokens"] += 1
        expected = tokenograph.analyze(entries)["stats"]["tokens"]["total"]
        entries.insert(idx, {"timestamp": entries[idx]["timestamp"],
                            "type": "token_usage_record", "payload": {"usage": usage}})
        self.assertEqual(tokenograph.analyze(entries)["stats"]["tokens"]["total"], expected)

    def test_first_prompt_uses_the_active_codex_turn(self):
        self.assertEqual(tokenograph.first_prompt(FIXTURE), "port the fixture adapter")

    def test_first_prompt_skips_long_legacy_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollout-legacy.jsonl"
            entries = [
                {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
                 "payload": {"id": "44444444-4444-4444-8444-444444444444",
                             "cwd": "/work/replay", "history_mode": "legacy"}},
                {"timestamp": "2026-08-20T12:00:00.100Z", "type": "turn_context",
                 "payload": {"model": "gpt-5.3-codex"}},
                {"timestamp": "2026-08-20T12:00:00.200Z", "type": "response_item",
                 "payload": {"type": "message", "role": "user",
                             "content": [{"type": "input_text", "text": "replayed prompt"}]}},
            ]
            entries.extend({"timestamp": "2026-08-20T12:00:00.300Z", "type": "response_item",
                            "payload": {"type": "reasoning", "encrypted_content": "opaque"}}
                           for _ in range(450))
            entries.extend([
                {"timestamp": "2026-08-20T12:00:01Z", "type": "event_msg",
                 "payload": {"type": "task_started"}},
                {"timestamp": "2026-08-20T12:00:01.100Z", "type": "turn_context",
                 "payload": {"model": "gpt-5.3-codex"}},
                {"timestamp": "2026-08-20T12:00:01.200Z", "type": "event_msg",
                 "payload": {"type": "user_message", "message": "active resumed prompt"}},
                {"timestamp": "2026-08-20T12:00:02.200Z", "type": "event_msg",
                 "payload": {"type": "user_message", "message": "later prompt in active task"}},
            ])
            path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")

            prompt = tokenograph.first_prompt(path)

        self.assertEqual(prompt, "active resumed prompt")

    def test_codex_fixture_populates_the_shared_analysis_model(self):
        payload = tokenograph.analyze(codex_entries(), source=str(FIXTURE))

        self.assertEqual(payload["meta"]["fmt"], "codex")
        self.assertEqual(payload["meta"]["agent"], "codex")
        self.assertEqual(payload["meta"]["agent_label"], "Codex CLI")
        self.assertEqual(payload["meta"]["session_id"], "11111111-2222-4333-8444-555555555555")
        self.assertEqual(payload["meta"]["cwd"], "/work/codex-fixture")
        self.assertEqual(payload["meta"]["git_branch"], "adapter-test")
        self.assertEqual(payload["meta"]["cli_version"], "0.150.0")
        self.assertEqual(payload["meta"]["models"], ["gpt-5.3-codex"])
        self.assertEqual(payload["meta"]["estimates"]["reasoning_tokens"], "reported")

        stats = payload["stats"]
        self.assertEqual(stats["laps"], 1)
        self.assertEqual(stats["counts"]["assistant"], 2)
        self.assertEqual(stats["counts"]["tools"], 1)
        self.assertEqual(stats["counts"]["compactions"], 1)
        self.assertEqual(stats["tokens"]["prompt_computed"], 750)
        self.assertEqual(stats["tokens"]["prompt_cache_write"], 150)
        self.assertEqual(stats["tokens"]["prompt_cached"], 1500)
        self.assertEqual(stats["tokens"]["completion"], 300)
        self.assertEqual(stats["tokens"]["reasoning"], 160)
        self.assertEqual(stats["context"], {"used": 1250, "window": 258400, "pct": 1250 / 258400})

        models = [call for call in payload["calls"] if call["k"] == "m"]
        self.assertEqual([call["stop"] for call in models], ["tool_use", "end_turn"])
        tool = next(call for call in payload["calls"] if call["k"] == "t")
        self.assertEqual(tool["n"], "exec_command")
        self.assertEqual(tool["l"], "python3 -m unittest")
        self.assertAlmostEqual(tool["t1"] - tool["t0"], 2.0, places=3)
        self.assertEqual(tool["ch"], 12)

    def test_discovery_uses_first_session_meta_identity_and_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "codex" / "sessions" / "2026" / "08" / "20" / \
                "rollout-2026-08-20T12-00-00-11111111-2222-4333-8444-555555555555.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            old = dict(os.environ)
            os.environ["CODEX_HOME"] = str(root / "codex")
            os.environ["CLAUDE_CONFIG_DIR"] = str(root / "no-claude")
            os.environ["PI_CODING_AGENT_DIR"] = str(root / "no-pi")
            try:
                sessions = tokenograph.iter_sessions()
                resolved = tokenograph.resolve_session("11111111")
            finally:
                os.environ.clear()
                os.environ.update(old)

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["fmt"], "codex")
        self.assertEqual(sessions[0]["session_id"], "11111111-2222-4333-8444-555555555555")
        self.assertEqual(sessions[0]["project"], "codex-fixture")
        self.assertEqual(resolved, rollout)

    def test_codex_wall_clock_is_additive_and_ledger_stays_bounded(self):
        payload = tokenograph.analyze(codex_entries(), source=str(FIXTURE))
        timing = payload["stats"]["time"]
        parts = sum(timing[key] for key in (
            "prefill", "reasoning", "generation", "tools", "compaction", "idle"))
        self.assertAlmostEqual(parts, timing["wall"], places=6)
        self.assertNotIn("error", payload["ledger"])
        for request in payload["ledger"]["series"]:
            self.assertLessEqual(sum(request["g"].values()), request["m"])
        self.assertNotIn("opaque-reasoning", json.dumps(payload))

    def test_empty_multiline_tool_argument_does_not_crash_labeling(self):
        self.assertEqual(tokenograph._codex_label(""), "")
        self.assertEqual(tokenograph._codex_label("\n\n"), "")

    def test_base_and_developer_instructions_both_remain_in_the_system_snapshot(self):
        entries = codex_entries()
        entries.insert(2, {"timestamp": "2026-08-20T12:00:00.150Z", "type": "response_item",
                           "payload": {"type": "message", "role": "developer", "content": [
                               {"type": "input_text", "text": "Project instructions.\nKeep tests synthetic."}]}})

        payload = tokenograph.analyze(entries, source="synthetic-instructions")
        system = next(row for row in payload["ledger"]["now"]["rows"] if row["key"] == "system")
        labels = {row["label"] for row in system["subs"]}

        self.assertIn("base instructions", labels)
        self.assertIn("Project instructions.", labels)

    def test_tool_search_output_records_loaded_schemas(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "66666666-6666-4666-8666-666666666666", "cwd": "/work/search"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.3-codex"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "load click"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "tool_search_call", "call_id": "search-1",
                         "arguments": {"query": "click"}}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "response_item",
             "payload": {"type": "tool_search_output", "call_id": "search-1", "tools": [{
                 "type": "namespace", "name": "mcp__computer_use", "description": "Computer tools",
                 "tools": [{"type": "function", "name": "click", "description": "Click a point",
                            "parameters": {"type": "object", "properties": {"x": {"type": "number"}}}}]}]}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 0,
                                      "cache_write_input_tokens": 0, "output_tokens": 10,
                                      "reasoning_output_tokens": 2},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 0,
                                       "cache_write_input_tokens": 0, "output_tokens": 10,
                                       "reasoning_output_tokens": 2},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-tool-search")
        tool = next(call for call in payload["calls"] if call["k"] == "t")

        self.assertEqual(tool["n"], "tool_search")
        self.assertGreater(tool["ch"], 0)
        self.assertIsNotNone(payload["ledger"]["tools"])
        self.assertIn("click", {row["name"] for row in payload["ledger"]["tools"]["loaded"]})
        self.assertTrue(payload["ledger"]["tools"]["loaded"][0]["solo"])
        self.assertFalse(payload["ledger"]["tools"]["catalog_known"])
        self.assertIn("full deferred catalog is not recorded", tokenograph.render_html(payload))

    def test_openai_pricing_is_sourced_and_unsupported_aliases_stay_unpriced(self):
        payload = tokenograph.analyze(codex_entries(), source=str(FIXTURE))
        cost = payload["stats"]["cost"]
        self.assertIsNotNone(cost)
        self.assertAlmostEqual(cost["input"], 600 * 1.75 / 1e6, places=12)
        self.assertAlmostEqual(cost["cache_write"], 150 * 1.75 / 1e6, places=12)
        self.assertAlmostEqual(cost["cache_read"], 1500 * 0.1 * 1.75 / 1e6, places=12)
        self.assertAlmostEqual(cost["output"], 300 * 14 / 1e6, places=12)
        self.assertEqual(cost["pricing"]["source_url"],
                         "https://developers.openai.com/api/docs/models/gpt-5.3-codex")
        self.assertIn("2026-09-04", cost["pricing"]["source"])
        self.assertIn("not an invoice", payload["meta"]["pricing_note"])

        unsupported = json.loads(json.dumps(codex_entries()))
        next(e for e in unsupported if e["type"] == "turn_context")["payload"]["model"] = \
            "gpt-5.3-codex-spark"
        unpriced = tokenograph.analyze(unsupported, source="synthetic-spark")
        self.assertIsNone(unpriced["stats"]["cost"])
        self.assertEqual(unpriced["ledger"]["read_mult"], 1.0)
        self.assertIn("gpt-5.3-codex-spark", unpriced["meta"]["pricing_note"])
        self.assertIn("cost omitted", unpriced["meta"]["pricing_note"])
        self.assertIn("D.meta.pricing_note", tokenograph.render_html(unpriced))

        arbitrary_alias = json.loads(json.dumps(codex_entries()))
        next(e for e in arbitrary_alias if e["type"] == "turn_context")["payload"]["model"] = \
            "gpt-5.3-codex-unpublished"
        self.assertIsNone(tokenograph.analyze(arbitrary_alias, source="synthetic-alias")["stats"]["cost"])

        bracket_alias = json.loads(json.dumps(codex_entries()))
        next(e for e in bracket_alias if e["type"] == "turn_context")["payload"]["model"] = \
            "gpt-5.3-codex[unpublished]"
        self.assertIsNone(tokenograph.analyze(bracket_alias, source="synthetic-bracket")["stats"]["cost"])

    def test_long_context_multiplier_reconciles_ledger_category_costs(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "55555555-5555-4555-8555-555555555555", "cwd": "/work/large"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "large request"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "phase": "final",
                         "content": [{"type": "output_text", "text": "done"}]}},
            {"timestamp": "2026-08-20T12:00:02.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 300000, "cached_input_tokens": 200000,
                                      "cache_write_input_tokens": 20000, "output_tokens": 10,
                                      "reasoning_output_tokens": 2},
                 "total_token_usage": {"input_tokens": 300000, "cached_input_tokens": 200000,
                                       "cache_write_input_tokens": 20000, "output_tokens": 10,
                                       "reasoning_output_tokens": 2},
                 "model_context_window": 400000}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-large")
        cost = payload["stats"]["cost"]
        ledger_cost = sum(row["cost"] for row in payload["ledger"]["cum"]["rows"])

        self.assertEqual(cost["pricing"]["long_context_requests"], 1)
        self.assertAlmostEqual(cost["input"], 80000 * 4.0 * 2.0 / 1e6, places=12)
        self.assertAlmostEqual(cost["cache_write"], 20000 * 1.25 * 4.0 * 2.0 / 1e6, places=12)
        self.assertAlmostEqual(cost["cache_read"], 200000 * 0.1 * 4.0 * 2.0 / 1e6, places=12)
        self.assertAlmostEqual(ledger_cost, cost["input"] + cost["cache_write"] + cost["cache_read"], places=9)

    def test_astra_cost_at_and_above_long_context_boundary(self):
        # The surcharge applies to the whole request only when input exceeds 272K.
        # Include cache writes/reads and reasoning to catch overlap or double billing.
        for input_tokens, input_mult, output_mult in [(272000, 1, 1), (272001, 2, 1.5)]:
            with self.subTest(input_tokens=input_tokens):
                usage = {"input_tokens": input_tokens, "cached_input_tokens": 200000,
                         "cache_write_input_tokens": 20000, "output_tokens": 1000,
                         "reasoning_output_tokens": 600}
                entries = [
                    {"timestamp": "2026-09-11T12:00:00Z", "type": "session_meta",
                     "payload": {"id": "astra-pricing-fixture", "cwd": "/work/fixture"}},
                    {"timestamp": "2026-09-11T12:00:01Z", "type": "turn_context",
                     "payload": {"model": "gpt-6-astra"}},
                    {"timestamp": "2026-09-11T12:00:02Z", "type": "response_item",
                     "payload": {"type": "message", "role": "assistant", "phase": "final",
                                 "content": [{"type": "output_text", "text": "fixture answer"}]}},
                    {"timestamp": "2026-09-11T12:00:03Z", "type": "event_msg",
                     "payload": {"type": "token_count", "info": {
                         "last_token_usage": usage, "total_token_usage": usage,
                         "model_context_window": 1050000}}},
                ]
                payload = tokenograph.analyze(entries)
                cost = payload["stats"]["cost"]
                self.assertAlmostEqual(cost["input"], (input_tokens - 220000) * 10 * input_mult / 1e6)
                self.assertAlmostEqual(cost["cache_write"], 20000 * 12.5 * input_mult / 1e6)
                self.assertAlmostEqual(cost["cache_read"], 200000 * input_mult / 1e6)
                self.assertAlmostEqual(cost["output"], 1000 * 50 * output_mult / 1e6)
                self.assertEqual(cost["pricing"]["long_context_requests"], int(input_tokens > 272000))
                self.assertIn("2026-09-11", cost["pricing"]["source"])
                self.assertEqual(cost["pricing"]["source_url"],
                                 "https://developers.openai.com/api/docs/models/gpt-6-astra")
                ledger_cost = sum(row["cost"] for row in payload["ledger"]["cum"]["rows"])
                self.assertAlmostEqual(ledger_cost, cost["total"] - cost["output"], places=9)
        self.assertIsNone(tokenograph.pricing_for("gpt-6-astra-unpublished"))

    def test_inner_tool_details_do_not_duplicate_accounting_or_infer_starts(self):
        entries = codex_entries()
        baseline = tokenograph.analyze(entries)
        detail = {"timestamp": "2026-08-20T12:00:07Z", "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "CommandExecution", "id": "inner-one",
                "command": ["zsh", "-c", "rg synthetic-query"], "status": "completed", "exit_code": 0,
                "duration": {"secs": 0, "nanos": 250000000}, "aggregated_output": "example result"}}}
        entries[6:6] = [detail, detail]
        payload = tokenograph.analyze(entries)
        self.assertEqual(payload["stats"], baseline["stats"])
        self.assertEqual(payload["calls"], baseline["calls"])
        self.assertEqual(payload["ledger"], baseline["ledger"])
        self.assertEqual(len(payload["tool_details"]), 1)
        tool = payload["tool_details"][0]
        self.assertEqual(tool["name"], "exec_command")
        self.assertEqual(tool["label"], "rg synthetic-query")
        self.assertEqual(tool["duration_s"], .25)
        self.assertEqual(tool["output_chars"], len("example result"))
        self.assertNotIn("started_at", tool)
        # A later completion must still fit on a static timeline. Extend the
        # observed clock once, without adding its reported duration as an interval.
        late = json.loads(json.dumps(detail))
        late["timestamp"] = "2026-08-20T12:00:13Z"
        late["payload"]["item"]["id"] = "late"
        later = tokenograph.analyze(entries + [late])
        self.assertEqual(later["end"], 13)
        self.assertEqual(later["stats"]["tokens"], baseline["stats"]["tokens"])
        self.assertEqual(later["stats"]["counts"], baseline["stats"]["counts"])
        times = later["stats"]["time"]
        self.assertAlmostEqual(times["wall"], sum(times[k] for k in
            ("prefill", "reasoning", "generation", "tools", "compaction", "idle")))

    def test_tool_details_exclude_replayed_legacy_history_and_private_reasoning(self):
        entries = codex_entries()
        entries[0]["payload"]["history_mode"] = "legacy"
        old = {"timestamp": "2026-08-20T12:00:05.500Z", "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "McpToolCall", "id": "old",
                "server": "docs", "tool": "read", "arguments": {"query": "old"}, "status": "completed"}}}
        new = json.loads(json.dumps(old))
        new["timestamp"] = "2026-08-20T12:00:07Z"
        new["payload"]["item"].update(id="new", arguments={"title": "fixture docs"}, result={"isError": True})
        reasoning = {"timestamp": "2026-08-20T12:00:07.500Z", "type": "event_msg", "payload": {
            "type": "item_completed", "item": {"type": "Reasoning", "id": "r", "raw_content": "not for tool details"}}}
        entries[6:6] = [old, {"timestamp": "2026-08-20T12:00:06.500Z", "type": "event_msg",
                             "payload": {"type": "task_started"}}, new, reasoning]
        details = tokenograph.analyze(entries)["tool_details"]
        self.assertEqual([d["id"] for d in details], ["new"])
        self.assertEqual(details[0]["name"], "docs.read")
        self.assertEqual(details[0]["label"], "fixture docs")
        self.assertEqual(details[0]["status"], "failed")
        self.assertIsNone(details[0]["duration_s"])
        self.assertNotIn("not for tool details", json.dumps(details))

    def test_mixed_model_cost_discloses_every_pricing_source(self):
        entries = codex_entries()
        entries.insert(9, {"timestamp": "2026-08-20T12:00:09Z", "type": "turn_context",
                           "payload": {"model": "gpt-5.4"}})

        payload = tokenograph.analyze(entries, source="synthetic-mixed")
        rows = payload["stats"]["cost"]["pricing"]["rows"]
        html = tokenograph.render_html(payload)

        self.assertEqual({row["model"] for row in rows}, {"gpt-5.3-codex", "gpt-5.4"})
        self.assertIn("https://developers.openai.com/api/docs/models/gpt-5.3-codex", html)
        self.assertIn("https://developers.openai.com/api/docs/models/gpt-5.4", html)

    def test_cli_accepts_forced_codex_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "codex.json"
            result = tokenograph.main(["json", str(FIXTURE), "--format", "codex", "-o", str(out)])
            payload = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(result, 0)
        self.assertEqual(payload["meta"]["fmt"], "codex")

    def test_adapter_labels_drive_panel_and_fleet_rendering(self):
        payload = tokenograph.analyze(codex_entries(), source=str(FIXTURE))
        panel = tokenograph.render_html(payload)
        self.assertIn("m.agent_label", panel)
        self.assertIn("m.cli_label", panel)
        self.assertNotIn("bits.push(`Claude Code <b>", panel)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rollout = root / "codex" / "sessions" / "2026" / "08" / "20" / \
                "rollout-2026-08-20T12-00-00-11111111-2222-4333-8444-555555555555.jsonl"
            rollout.parent.mkdir(parents=True)
            rollout.write_text(FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
            old = dict(os.environ)
            os.environ["CODEX_HOME"] = str(root / "codex")
            os.environ["CLAUDE_CONFIG_DIR"] = str(root / "no-claude")
            os.environ["PI_CODING_AGENT_DIR"] = str(root / "no-pi")
            os.environ["XDG_CONFIG_HOME"] = str(root / "no-herdr")
            os.environ.pop("HERDR_SOCKET_PATH", None)
            try:
                fleet = tokenograph.fleet_payload(limit=5)
            finally:
                os.environ.clear()
                os.environ.update(old)

        self.assertEqual(fleet["rows"][0]["agent_label"], "Codex CLI")
        self.assertEqual(next(s for s in fleet["sources"] if s["fmt"] == "codex")["label"], "Codex CLI")
        fleet_html = tokenograph.render_fleet_html(fleet)
        self.assertIn("r.agent_label", fleet_html)
        self.assertNotIn("r.fmt === 'pi' ? 'pi' : 'claude code'", fleet_html)

    def test_current_usage_record_uses_event_text_and_delayed_counter_only_for_window(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "22222222-2222-4222-8222-222222222222", "cwd": "/work/current",
                         "cli_version": "0.153.4"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "explain this"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "event_msg",
             "payload": {"type": "agent_reasoning"}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "event_msg",
             "payload": {"type": "agent_message", "message": "The answer."}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "token_usage_record",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                     "cache_write_input_tokens": 0, "output_tokens": 20,
                                     "reasoning_output_tokens": 8}}},
            {"timestamp": "2026-08-20T12:00:03.200Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                      "cache_write_input_tokens": 0, "output_tokens": 20,
                                      "reasoning_output_tokens": 8},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                       "cache_write_input_tokens": 0, "output_tokens": 20,
                                       "reasoning_output_tokens": 8},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-current")

        self.assertEqual(payload["stats"]["counts"]["calls"], 1)
        self.assertEqual(payload["stats"]["tokens"]["prompt_computed"], 60)
        self.assertEqual(payload["stats"]["tokens"]["prompt_cached"], 40)
        self.assertEqual(payload["stats"]["tokens"]["completion"], 20)
        self.assertEqual(payload["stats"]["tokens"]["reasoning"], 8)
        self.assertEqual(payload["stats"]["context"]["window"], 121600)
        self.assertEqual(payload["meta"]["title"], "explain this")

    def test_response_tail_between_usage_record_and_counter_is_not_double_counted(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "77777777-7777-4777-8777-777777777777", "cwd": "/work/tail"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "finish after usage"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "reasoning", "encrypted_content": "opaque"}},
            {"timestamp": "2026-08-20T12:00:02.500Z", "type": "token_usage_record",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                     "cache_write_input_tokens": 0, "output_tokens": 20,
                                     "reasoning_output_tokens": 8}}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "phase": "final",
                         "content": [{"type": "output_text", "text": "Finished."}]}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                      "cache_write_input_tokens": 0, "output_tokens": 20,
                                      "reasoning_output_tokens": 8},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                       "cache_write_input_tokens": 0, "output_tokens": 20,
                                       "reasoning_output_tokens": 8},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-tail")
        models = [call for call in payload["calls"] if call["k"] == "m"]

        self.assertEqual(len(models), 1)
        self.assertEqual(payload["stats"]["tokens"]["total"], 120)
        self.assertEqual(models[0]["stop"], "end_turn")
        self.assertGreater(models[0]["t1"], models[0]["t0"])

    def test_unconfirmed_internal_compaction_usage_is_not_added_to_session_counters(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cwd": "/work/compact"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "compact safely"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "phase": "final",
                         "content": [{"type": "output_text", "text": "before compact"}]}},
            {"timestamp": "2026-08-20T12:00:02.100Z", "type": "token_usage_record",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                     "cache_write_input_tokens": 0, "output_tokens": 20,
                                     "reasoning_output_tokens": 8}}},
            {"timestamp": "2026-08-20T12:00:02.200Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                      "cache_write_input_tokens": 0, "output_tokens": 20,
                                      "reasoning_output_tokens": 8},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                       "cache_write_input_tokens": 0, "output_tokens": 20,
                                       "reasoning_output_tokens": 8},
                 "model_context_window": 121600}}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "token_usage_record",
             "payload": {"usage": {"input_tokens": 200, "cached_input_tokens": 100,
                                     "cache_write_input_tokens": 0, "output_tokens": 30,
                                     "reasoning_output_tokens": 0}}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "compacted",
             "payload": {"replacement_history": [{"type": "compaction",
                                                    "encrypted_content": "opaque"}]}},
            {"timestamp": "2026-08-20T12:00:03.200Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 0, "cached_input_tokens": 0,
                                      "cache_write_input_tokens": 0, "output_tokens": 0,
                                      "reasoning_output_tokens": 0},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                       "cache_write_input_tokens": 0, "output_tokens": 20,
                                       "reasoning_output_tokens": 8},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-internal-compaction")

        self.assertEqual(payload["stats"]["counts"]["assistant"], 1)
        self.assertEqual(payload["stats"]["tokens"]["total"], 120)
        self.assertEqual(payload["stats"]["counts"]["compactions"], 1)

    def test_current_item_completed_failure_marks_the_matching_tool(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "88888888-8888-4888-8888-888888888888", "cwd": "/work/errors"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "run failure"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "call-fail",
                         "input": "await tools.exec_command({\"cmd\":\"false\"})"}},
            {"timestamp": "2026-08-20T12:00:02.100Z", "type": "response_item",
             "payload": {"type": "custom_tool_call", "name": "exec", "call_id": "mcp-fail",
                         "input": "await tools.canva.upload_asset_from_url({\"asset\":\"fixture.png\"})"}},
            {"timestamp": "2026-08-20T12:00:02.500Z", "type": "event_msg",
             "payload": {"type": "item_completed", "item": {"type": "CommandExecution",
                 "id": "internal-command-id", "command": ["/bin/zsh", "-lc", "false"],
                 "status": "failed", "exit_code": 1}}},
            {"timestamp": "2026-08-20T12:00:02.600Z", "type": "event_msg",
             "payload": {"type": "item_completed", "item": {"type": "McpToolCall",
                 "id": "internal-mcp-id", "tool": "canva.upload-asset-from-url",
                 "arguments": {"asset": "fixture.png"}, "status": "failed",
                 "error": {"message": "synthetic failure"}}}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "call-fail", "output": "failed"}},
            {"timestamp": "2026-08-20T12:00:03.050Z", "type": "response_item",
             "payload": {"type": "custom_tool_call_output", "call_id": "mcp-fail", "output": "failed"}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 0,
                                      "cache_write_input_tokens": 0, "output_tokens": 10,
                                      "reasoning_output_tokens": 2},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 0,
                                       "cache_write_input_tokens": 0, "output_tokens": 10,
                                       "reasoning_output_tokens": 2},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-item-failure")

        self.assertEqual(payload["stats"]["counts"]["tool_errors"], 2)
        self.assertTrue(all(call["err"] for call in payload["calls"] if call["k"] == "t"))

    def test_unmetered_interrupted_activity_survives_later_confirmed_calls(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", "cwd": "/work/interrupt"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "run the tool"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "function_call", "name": "exec_command", "call_id": "tool-1",
                         "arguments": "{\"cmd\":\"sleep 2\"}"}},
            {"timestamp": "2026-08-20T12:00:04Z", "type": "response_item",
             "payload": {"type": "function_call_output", "call_id": "tool-1",
                         "output": "interrupted", "is_error": True}},
            {"timestamp": "2026-08-20T12:00:05Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "reason": "interrupted"}},
        ]
        usage = {"input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20,
                 "reasoning_output_tokens": 5}
        later = [
            {"timestamp": "2026-08-20T12:00:06Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "try again"}},
            {"timestamp": "2026-08-20T12:00:07Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant", "phase": "final",
                         "content": [{"type": "output_text", "text": "Done."}]}},
            {"timestamp": "2026-08-20T12:00:07.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": usage, "total_token_usage": usage,
                 "model_context_window": 121600}}},
        ]
        confirmed = tokenograph.analyze(entries[:3] + later)
        now = tokenograph.parse_ts("2026-08-20T12:00:08Z")
        for suffix in ([], later):
            for live in (False, True):
                with self.subTest(later_confirmed=bool(suffix), live=live):
                    payload = tokenograph.analyze(entries + suffix, live=live, now=now)
                    stats = payload["stats"]
                    self.assertEqual(stats["counts"]["assistant"], 2 if suffix else 1)
                    self.assertEqual(stats["counts"]["tools"], 1)
                    self.assertEqual(stats["counts"]["tool_errors"], 1)
                    self.assertEqual(stats["counts"]["interrupts"], 1)
                    self.assertEqual(stats["time"]["tools"], 2.0)
                    self.assertEqual(stats["tokens"]["total"], 120 if suffix else 0)
                    self.assertEqual(payload["meta"]["models"], ["gpt-5.6-sol"])
                    tool = next(c for c in payload["calls"] if c["k"] == "t")
                    self.assertEqual(tool["t1"] - tool["t0"], 2.0)
                    self.assertFalse(tool["open"])
                    timing = stats["time"]
                    self.assertAlmostEqual(sum(timing[k] for k in (
                        "prefill", "reasoning", "generation", "tools", "compaction", "idle")),
                        timing["wall"], places=6)
                    if suffix:
                        self.assertEqual(stats["cost"], confirmed["stats"]["cost"])
                        self.assertEqual(stats["context"], confirmed["stats"]["context"])
                        self.assertEqual(payload["ledger"]["cum"]["requests"], 1)
                        graph = tokenograph.build_graph(payload)
                        self.assertEqual({edge["target"] for edge in graph["links"]
                                          if edge["kind"] == "present_in"}, {"request:2"})
                        for request in payload["ledger"]["series"]:
                            self.assertLessEqual(sum(request["g"].values()), request["m"])
                    else:
                        self.assertIsNone(stats["cost"])
                        self.assertIsNone(payload["ledger"])

    def test_unconfirmed_usage_preserves_activity_but_not_accounting(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "cccccccc-cccc-4ccc-8ccc-cccccccccccc", "cwd": "/work/provisional"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "reasoning", "encrypted_content": "opaque-synthetic-reasoning"}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "token_usage_record",
             "payload": {"usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                    "output_tokens": 20, "reasoning_output_tokens": 8}}},
        ]
        now = tokenograph.parse_ts("2026-08-20T12:00:04Z")
        for live in (False, True):
            with self.subTest(live=live):
                payload = tokenograph.analyze(entries, live=live, now=now)
                self.assertEqual(payload["stats"]["counts"]["assistant"], 1)
                self.assertGreater(payload["stats"]["time"]["reasoning"], 0)
                self.assertEqual(payload["stats"]["tokens"]["total"], 0)
                self.assertEqual(payload["stats"]["context"]["used"], 0)
                self.assertIsNone(payload["stats"]["cost"])
                self.assertIsNone(payload["ledger"])
                self.assertNotIn("opaque-synthetic-reasoning", json.dumps(payload))

    def test_missing_usage_is_not_inferred_as_prefill_or_measured_throughput(self):
        entries = codex_entries() + [
            {"timestamp": "2026-08-20T12:00:13Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "keep thinking"}},
            {"timestamp": "2026-08-20T12:00:14Z", "type": "response_item",
             "payload": {"type": "reasoning", "encrypted_content": "opaque-synthetic-reasoning"}},
        ]
        baseline = tokenograph.analyze(codex_entries())
        payload = tokenograph.analyze(entries)
        last = [call for call in payload["calls"] if call["k"] == "m"][-1]

        self.assertEqual(last["p"], [last["t0"], last["t1"]])
        self.assertIsNone(payload["stats"]["tg_s"])
        self.assertIsNone(payload["stats"]["pp_s"])
        self.assertEqual(payload["stats"]["counts"]["assistant_unmetered"], 1)
        self.assertIn("totals cover confirmed calls only", payload["meta"]["pricing_note"])
        self.assertEqual(payload["stats"]["tokens"], baseline["stats"]["tokens"])
        self.assertEqual(payload["stats"]["cost"], baseline["stats"]["cost"])

    def test_live_usage_pending_reasoning_is_working(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "99999999-9999-4999-8999-999999999999", "cwd": "/work/live"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "still thinking"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "reasoning", "encrypted_content": "opaque"}},
        ]
        now = tokenograph.parse_ts("2026-08-20T12:00:03Z")

        payload = tokenograph.analyze(entries, source="synthetic-live", live=True, now=now)

        self.assertIsNone(next(call for call in payload["calls"] if call["k"] == "m")["stop"])
        self.assertEqual(tokenograph.derive_state(payload, now - 1, now), "working")

    def test_live_pending_call_keeps_last_confirmed_context_and_ledger(self):
        entries = [
            {"timestamp": "2026-08-20T12:00:00Z", "type": "session_meta",
             "payload": {"id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", "cwd": "/work/live"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "first turn"}},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "done"}]}},
            {"timestamp": "2026-08-20T12:00:02.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                      "cache_write_input_tokens": 0, "output_tokens": 20,
                                      "reasoning_output_tokens": 5},
                 "total_token_usage": {"input_tokens": 100, "cached_input_tokens": 40,
                                       "cache_write_input_tokens": 0, "output_tokens": 20,
                                       "reasoning_output_tokens": 5},
                 "model_context_window": 121600}}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "turn_context",
             "payload": {"model": "gpt-5.6-sol"}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "second turn"}},
            {"timestamp": "2026-08-20T12:00:04Z", "type": "response_item",
             "payload": {"type": "reasoning", "encrypted_content": "opaque"}},
        ]
        now = tokenograph.parse_ts("2026-08-20T12:00:05Z")

        payload = tokenograph.analyze(entries, source="synthetic-live", live=True, now=now)

        self.assertEqual(payload["stats"]["context"]["used"], 100)
        self.assertEqual(payload["stats"]["counts"]["assistant"], 2)
        self.assertEqual(payload["ledger"]["cum"]["requests"], 1)
        self.assertEqual(tokenograph.derive_state(payload, now - 1, now), "working")

    def test_event_and_response_prompt_copies_collapse_and_abort_is_an_interrupt(self):
        entries = [
            {"timestamp": "2026-08-19T12:00:00Z", "type": "session_meta",
             "payload": {"id": "33333333-3333-4333-8333-333333333333", "cwd": "/work/legacy",
                         "cli_version": "0.122.0", "history_mode": "legacy"}},
            {"timestamp": "2026-08-19T12:00:01Z", "type": "response_item",
             "payload": {"type": "message", "role": "assistant",
                         "content": [{"type": "output_text", "text": "replayed"}]}},
            {"timestamp": "2026-08-20T12:00:00.900Z", "type": "event_msg",
             "payload": {"type": "task_started"}},
            {"timestamp": "2026-08-20T12:00:01Z", "type": "turn_context",
             "payload": {"model": "gpt-5.3-codex"}},
            {"timestamp": "2026-08-20T12:00:01.100Z", "type": "event_msg",
             "payload": {"type": "user_message", "message": "search the docs"}},
            {"timestamp": "2026-08-20T12:00:01.200Z", "type": "response_item",
             "payload": {"type": "message", "role": "user", "content": [
                 {"type": "input_text", "text": "search the docs\n<environment_context>synthetic</environment_context>"}] }},
            {"timestamp": "2026-08-20T12:00:02Z", "type": "response_item",
             "payload": {"type": "web_search_call", "id": "search-1", "query": "token docs"}},
            {"timestamp": "2026-08-20T12:00:02.500Z", "type": "event_msg",
             "payload": {"type": "web_search_end", "query": "token docs"}},
            {"timestamp": "2026-08-20T12:00:03Z", "type": "event_msg",
             "payload": {"type": "turn_aborted", "reason": "interrupted"}},
            {"timestamp": "2026-08-20T12:00:03.100Z", "type": "event_msg",
             "payload": {"type": "token_count", "info": {
                 "last_token_usage": {"input_tokens": 70, "cached_input_tokens": 20,
                                      "cache_write_input_tokens": 0, "output_tokens": 5,
                                      "reasoning_output_tokens": 2},
                 "total_token_usage": {"input_tokens": 70, "cached_input_tokens": 20,
                                       "cache_write_input_tokens": 0, "output_tokens": 5,
                                       "reasoning_output_tokens": 2},
                 "model_context_window": 121600}}},
        ]

        payload = tokenograph.analyze(entries, source="synthetic-legacy")

        self.assertEqual(payload["stats"]["laps"], 1)
        self.assertEqual(payload["stats"]["counts"]["interrupts"], 1)
        self.assertEqual(payload["stats"]["counts"]["errors"], 0)
        self.assertLess(payload["stats"]["time"]["wall"], 10)
        web = next(call for call in payload["calls"] if call["k"] == "t")
        self.assertEqual(web["n"], "web_search")
        self.assertEqual(web["t0"], web["t1"])
        self.assertFalse(web.get("open"))


if __name__ == "__main__":
    unittest.main()
