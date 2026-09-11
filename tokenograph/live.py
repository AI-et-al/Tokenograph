"""Read-only, opt-in telemetry stream for a terminal viewer. Standard library only.

Usage: python3 -m tokenograph.live SESSION --once
Without --once, emit a small JSON snapshot when the transcript changes. The viewer
can animate clocks independently; this collector never calls a model or writes a
transcript. Accounting comes from analyze(), including its confirmation rules.
"""
import argparse
import json
import time

from . import TranscriptReader, analyze, parse_ts, resolve_session, observed_tools


def snapshot(entries, source, now=None):
    now = time.time() if now is None else now
    data = analyze(entries, source=str(source), live=True, now=now)
    base = data["base"]
    models = [c for c in data["calls"] if c["k"] == "m" and c.get("ledger_k") is not None]
    usage_at = base + models[-1]["t1"] if models else None
    turn_start, active, limits = None, None, None
    for entry in entries:
        payload = entry.get("payload") or {}
        if entry.get("type") != "event_msg":
            continue
        kind = payload.get("type")
        if kind == "task_started":
            turn_start, active = parse_ts(entry.get("timestamp")), True
        elif kind in ("task_complete", "turn_aborted"):
            active = False
        elif kind == "token_count" and payload.get("rate_limits"):
            limits = {key: payload["rate_limits"].get(key) for key in ("primary", "secondary")}
    tools = [dict(c, started_at=base+c["t0"], ended_at=base+c["t1"])
             for c in data["calls"] if c["k"] == "t" and not c.get("sub")]
    running = [c for c in tools if c.get("open") and active is not False
               and (turn_start is None or c["started_at"] >= turn_start)]
    ledger = data.get("ledger") or {}
    return {"session_id": data["meta"]["session_id"], "format": data["meta"]["fmt"],
            "models": data["meta"]["models"], "generated_at": now, "base": base,
            "usage_at": usage_at, "active": active, "turn_start": turn_start,
            "stats": data["stats"], "limits": limits, "running": running[-8:],
            "recent": [c for c in tools if not c.get("open")][-4:][::-1],
            "observed_tools": data["tool_details"][-12:][::-1],
            "window_rows": (ledger.get("now") or {}).get("rows", []),
            "rebuilds": len(ledger.get("events") or []),
            "pricing_note": data["meta"]["pricing_note"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session", help="explicit session ID or transcript path; latest is resolved once")
    parser.add_argument("--once", action="store_true", help="emit one snapshot and exit")
    parser.add_argument("--interval", type=float, default=1.0)
    args = parser.parse_args(argv)
    if args.interval < 0.25:
        parser.error("--interval must be at least 0.25 seconds")
    reader = TranscriptReader(resolve_session(args.session))
    try:
        while True:
            changed = reader.refresh()
            if changed or args.once:
                print(json.dumps(snapshot(reader.entries, reader.path), ensure_ascii=False), flush=True)
            if args.once:
                return
            time.sleep(args.interval)
    except (KeyboardInterrupt, BrokenPipeError):
        pass


if __name__ == "__main__":
    main()
