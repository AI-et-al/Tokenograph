"""Opt-in Starship summary. Prompt draws read a small cache; only watch analyzes logs."""
import argparse
import json
import math
import os
from pathlib import Path
import re
import signal
import sys
import tempfile
import time

from . import TranscriptReader, resolve_session
from .live import snapshot


def safe_name(value):
    # Names only, never transcript commands or shell/terminal control sequences.
    return re.sub(r"[^a-zA-Z0-9_.:/+-]", "", str(value or ""))[:64]


def compact_snapshot(data):
    stats = data["stats"]
    recent = data.get("observed_tools") or data.get("recent") or []
    return {"version": 1, "checked_at": data["generated_at"],
            "usage_at": data.get("usage_at"),
            "session": safe_name(data["session_id"])[:8],
            "model": safe_name((data["models"] or ["unknown"])[-1]),
            "context": {k: stats["context"][k] for k in ("used", "window")},
            "tokens": stats["tokens"]["total"],
            "cache_ratio": stats["tokens"]["cache_hit_ratio"],
            "cost": stats["cost"]["total"] if stats.get("cost") is not None else None,
            "cost_reported": stats.get("cost_reported"),
            "tools": stats["counts"]["tools"], "errors": stats["counts"]["tool_errors"],
            "partial": bool(stats["counts"].get("assistant_unmetered")),
            "last_tool": safe_name(recent[0].get("name", recent[0].get("n"))) if recent else ""}


def write_cache(path, data):
    """Atomic, private numeric summary. Readers never see a partially written JSON file."""
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".tokenograph-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=True, allow_nan=False)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def number(value):
    if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError("invalid counter")
    if value >= 1_000_000:
        return ("%.2f" % (value / 1_000_000)).rstrip("0").rstrip(".") + "M"
    if value >= 1000:
        return ("%.1f" % (value / 1000)).rstrip("0").rstrip(".") + "k"
    return str(round(value))


def age(value):
    value = max(0, int(value))
    return "%ds" % value if value < 60 else "%dm" % (value // 60) if value < 3600 else "%dh" % (value // 3600)


def summary(data, now=None):
    now = time.time() if now is None else now
    if data.get("version") != 1:
        return ""
    parts = [safe_name(data["model"]), safe_name(data["session"])]
    metered = data.get("usage_at") is not None
    if metered:
        ctx = data["context"]
        parts += ["ctx %s/%s" % (number(ctx["used"]), number(ctx["window"])),
                  "tok " + number(data["tokens"])]
        if data.get("cache_ratio") is not None:
            ratio = data["cache_ratio"]
            if not isinstance(ratio, (int, float)) or not math.isfinite(ratio) or not 0 <= ratio <= 1:
                raise ValueError("invalid cache ratio")
            parts.append("cache %.0f%%" % (100 * ratio))
        cost = data.get("cost_reported")
        prefix = "$"
        if cost is None:
            cost, prefix = data.get("cost"), "~$"
        if cost is None:
            parts.append("cost —")
        else:
            number(cost)  # validate before formatting
            parts.append(prefix + "%.2f" % cost)
        if data.get("partial"):
            parts.append("partial usage")
    else:
        parts.append("usage pending")
    parts.append("tools " + number(data["tools"]) + (" / %s errors" % number(data["errors"]) if data["errors"] else ""))
    if data.get("last_tool"):
        parts.append("last " + safe_name(data["last_tool"]))
    checked_age = now - data["checked_at"]
    if checked_age > 10:
        parts.append("stale " + age(checked_age))
    elif metered:
        parts.append("usage " + age(now - data["usage_at"]) + " ago")
    return " · ".join(parts)


def read_summary(path, now=None):
    if not path:
        return ""
    try:
        with open(path, encoding="utf-8") as handle:
            content = handle.read(8193)
        if len(content) > 8192:
            return ""
        return summary(json.loads(content), now)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError):
        return ""


def parent_alive(pid):
    if pid is None:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


class Collector:
    def __init__(self, path, cache):
        self.reader = TranscriptReader(path)
        self.cache = cache
        self.data = None

    def refresh(self, now=None):
        now = time.time() if now is None else now
        if not self.reader.path.is_file():
            raise FileNotFoundError("transcript is unavailable")
        changed = self.reader.refresh()
        if changed or self.data is None:
            self.data = compact_snapshot(snapshot(self.reader.entries, self.reader.path, now=now))
        self.data["checked_at"] = now
        write_cache(self.cache, self.data)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    show = commands.add_parser("show", help="read the cache only; silent when unavailable")
    show.add_argument("--cache", default=os.environ.get("TOKENOGRAPH_PROMPT_CACHE"))
    for command in ("prepare", "watch"):
        sub = commands.add_parser(command, help="pin an explicit session and write its summary cache")
        sub.add_argument("session")
        sub.add_argument("--cache", required=True)
        if command == "watch":
            sub.add_argument("--parent-pid", type=int)
            sub.add_argument("--interval", type=float, default=1)
    args = parser.parse_args(argv)
    if args.command == "show":
        text = read_summary(args.cache)
        if text:
            print(text)
        return 0
    if args.command == "watch" and (not math.isfinite(args.interval) or args.interval < .25):
        parser.error("interval must be finite and at least 0.25 seconds")
    if args.command == "watch" and args.parent_pid is not None and args.parent_pid <= 1:
        parser.error("parent-pid must be greater than one")
    path = resolve_session(args.session).resolve()
    collector = Collector(path, args.cache)
    try:
        if args.command == "prepare":
            collector.refresh()
            print(path)
            return 0
        stopped = False

        def stop(*_):
            nonlocal stopped
            stopped = True

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        while not stopped and parent_alive(args.parent_pid):
            collector.refresh()
            time.sleep(args.interval)
    except (OSError, ValueError):
        # Do not print transcript content or a traceback into the user's shell.
        print("Tokenograph could not update the prompt cache.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
