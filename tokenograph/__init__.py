#!/usr/bin/env python3
"""tokenograph: tokenometrics for long-horizon coding-agent sessions.

Telemetry, a context ledger and cost accounting for Claude Code, Codex CLI and pi sessions.

Reads Claude Code transcripts (~/.claude/projects/<project>/<session>.jsonl) and
Codex CLI rollouts (~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl) and pi sessions
(~/.pi/agent/sessions/<cwd>/<stamp>_<id>.jsonl) and renders one page:

  stats      tg/s, pp/s, laps, an additive wall-clock split (prefill / reasoning /
             generation / tools / compaction / idle), token and cost accounting
  activity   a per-row timeline of every call: laps, assistant phases, each tool,
             compactions, idle stretches
  context    what is in the context window, request by request: system prompt,
             tool schemas, injected skill / MCP / agent listings, loaded skills,
             prompts, tool results by tool, images; what each category has cost
             over the session (computed vs. served from cache); cache rebuilds and
             their likely cause; deferred vs. direct tool loading

Only the Python standard library is required (3.8+).

    python3 -m tokenograph list
    python3 -m tokenograph build latest -o panel.html
    python3 -m tokenograph serve latest --open
    python3 -m tokenograph fleet --serve            # every session, herdr-style states
    python3 -m tokenograph graph latest -o s.graphml # the session as a property graph
    python3 -m tokenograph json  <session-id-or-path>

Measured vs. estimated: timestamps and usage counts are measured. The prefill/
decode split, token counts derived from characters, image tokens, and dollar cost
are estimates; the panel marks them with ~ and the footer states each method.
"""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import glob
import hashlib
import http.server
import json
import math
import os
import re
import socket
import struct
import sys
import threading
import time
import webbrowser
from collections import defaultdict
from pathlib import Path

__version__ = "0.3.0"
HERE = Path(__file__).resolve().parent
TEMPLATE_PATH = HERE / "panel.html"
FLEET_TEMPLATE_PATH = HERE / "fleet.html"
DATA_MARKER = "__TOKENOGRAPH_DATA__"
DEFAULT_WINDOW = 200_000
LARGE_WINDOW = 1_000_000
INTERRUPT_PREFIX = "[Request interrupted by user"
DEFAULT_CPT = 3.8          # characters per token when the session cannot be calibrated
PRICING_DATE = "2026-06"   # existing Anthropic table snapshot; override with --price
OPENAI_PRICING_DATE = "2026-09-04"
OPENAI_PRICE_DATES = {"gpt-6-astra": "2026-09-11"}

# $/M input, $/M output, cache-read multiplier, cache-write 5m multiplier, cache-write 1h multiplier
PRICING = {
    "claude-fable-5-1": (10.0, 50.0, 0.025, 1.25, 2.0),
    "claude-mythos-5-1": (10.0, 50.0, 0.025, 1.25, 2.0),
    "claude-fable-5": (10.0, 50.0, 0.1, 1.25, 2.0),
    "claude-opus-5": (5.0, 25.0, 0.1, 1.25, 2.0),
    "claude-opus-4-8": (5.0, 25.0, 0.1, 1.25, 2.0),
    "claude-opus-4-7": (5.0, 25.0, 0.1, 1.25, 2.0),
    "claude-opus-4-6": (5.0, 25.0, 0.1, 1.25, 2.0),
    "claude-opus-4-5": (5.0, 25.0, 0.1, 1.25, 2.0),
    "claude-opus-4-1": (15.0, 75.0, 0.1, 1.25, 2.0),
    "claude-opus-4": (15.0, 75.0, 0.1, 1.25, 2.0),
    "claude-sonnet-5": (2.0, 10.0, 0.1, 1.25, 2.0),
    "claude-sonnet-4-6": (3.0, 15.0, 0.1, 1.25, 2.0),
    "claude-sonnet-4-5": (3.0, 15.0, 0.1, 1.25, 2.0),
    "claude-sonnet-4": (3.0, 15.0, 0.1, 1.25, 2.0),
    "claude-3-7-sonnet": (3.0, 15.0, 0.1, 1.25, 2.0),
    "claude-haiku-4-5": (1.0, 5.0, 0.1, 1.25, 2.0),
    "claude-3-5-haiku": (0.8, 4.0, 0.1, 1.25, 2.0),
    # Standard API list prices retrieved on each model's OPENAI_PRICE_DATES entry,
    # falling back to OPENAI_PRICING_DATE. These are estimates for
    # Codex rollouts, not evidence of the user's subscription invoice. Each row's public
    # source is carried into the panel through OPENAI_PRICE_URLS.
    "gpt-6-astra": (10.0, 50.0, 0.1, 1.25, 1.25),
    "gpt-5.6-sol": (4.0, 20.0, 0.1, 1.25, 1.25),
    "gpt-5.6-terra": (2.0, 12.0, 0.1, 1.25, 1.25),
    "gpt-5.5": (5.0, 30.0, 0.1, 1.0, 1.0),
    "gpt-5.4": (2.5, 15.0, 0.1, 1.0, 1.0),
    "gpt-5.4-mini": (0.75, 4.5, 0.1, 1.0, 1.0),
    "gpt-5.3-codex": (1.75, 14.0, 0.1, 1.0, 1.0),
    "gpt-5.2-codex": (1.75, 14.0, 0.1, 1.0, 1.0),
}

OPENAI_PRICE_URLS = {
    name: f"https://developers.openai.com/api/docs/models/{name}" for name in (
        "gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4", "gpt-5.4-mini",
        "gpt-5.3-codex", "gpt-5.2-codex",
    )
}
OPENAI_LONG_CONTEXT_MODELS = {"gpt-6-astra", "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.5", "gpt-5.4"}
OPENAI_LONG_CONTEXT_THRESHOLD = 272_000
UNPRICED_MODELS = {"gpt-5.3-codex-spark", "codex-auto-review"}

# --------------------------------------------------------------------------- utils

_TS_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?(Z|[+-]\d{2}:?\d{2})?$"
)


def parse_ts(value):
    """ISO-8601 timestamp (or epoch milliseconds) -> epoch seconds, or None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
        return v / 1000.0 if v > 1e11 else v
    if not value or not isinstance(value, str):
        return None
    m = _TS_RE.match(value.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, tz = m.groups()
    micro = int((frac or "0")[:6].ljust(6, "0"))
    try:
        base = dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    offset = 0
    if tz and tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        digits = tz[1:].replace(":", "")
        offset = sign * (int(digits[:2]) * 3600 + int(digits[2:4]) * 60)
    return base.timestamp() - offset


def claude_dir() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or (Path.home() / ".claude"))


def projects_dir() -> Path:
    return claude_dir() / "projects"


def pi_sessions_dir() -> Path:
    return Path(os.environ.get("PI_CODING_AGENT_DIR") or (Path.home() / ".pi" / "agent")) / "sessions"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or (Path.home() / ".codex"))


def codex_sessions_dir() -> Path:
    return codex_home() / "sessions"


def text_of(content) -> str:
    """Flatten message content (string or block list) to the text it carries."""
    if isinstance(content, str):
        return content
    out = []
    if isinstance(content, list):
        for block in content:
            if isinstance(block, str):
                out.append(block)
            elif isinstance(block, dict):
                kind = block.get("type")
                if kind in ("text", "input_text", "output_text"):
                    out.append(block.get("text") or "")
                elif kind == "tool_result":
                    out.append(text_of(block.get("content")))
    return "\n".join(out)


def image_blocks(content):
    if not isinstance(content, list):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == "image"]


def _b64_head(data: str, n: int = 6000) -> bytes:
    chunk = data[:n]
    chunk = chunk[: len(chunk) - len(chunk) % 4]
    try:
        return base64.b64decode(chunk, validate=False)
    except Exception:
        return b""


def image_dims(media_type, data) -> tuple:
    """(width, height) from the first bytes of a base64 PNG/JPEG/GIF/WebP, or (0, 0)."""
    raw = _b64_head(data or "")
    try:
        if raw[:8] == b"\x89PNG\r\n\x1a\n" and len(raw) >= 24:
            return struct.unpack(">II", raw[16:24])
        if raw[:6] in (b"GIF87a", b"GIF89a") and len(raw) >= 10:
            return struct.unpack("<HH", raw[6:10])
        if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP" and len(raw) >= 30:
            if raw[12:16] == b"VP8 ":
                return struct.unpack("<HH", raw[26:30])[0] & 0x3FFF, struct.unpack("<HH", raw[26:30])[1] & 0x3FFF
            if raw[12:16] == b"VP8L":
                b = raw[21:25]
                return 1 + (((b[1] & 0x3F) << 8) | b[0]), 1 + (((b[3] & 0xF) << 10) | (b[2] << 2) | ((b[1] & 0xC0) >> 6))
        if raw[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(raw):
                if raw[i] != 0xFF:
                    i += 1
                    continue
                marker = raw[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", raw[i + 2:i + 4])[0]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", raw[i + 5:i + 9])
                    return w, h
                i += 2 + seg
    except Exception:
        pass
    return 0, 0


HI_RES_MODELS = re.compile(r"opus-4-[78]|opus-5|fable|mythos|sonnet-5")


def image_tokens(media_type, data, dims=None, model=None) -> int:
    """Vision token estimate: the image is scaled to the model's limit (1568 px on the long
    edge and ~1.15 MP; 2576 px and ~3.6 MP on hi-res models), then costs about (w*h)/750
    tokens. Unknown dimensions -> 1000."""
    w, h = dims or image_dims(media_type, data)
    if not w or not h:
        return 1000
    hi = bool(model and HI_RES_MODELS.search(str(model)))
    edge, mp = (2576.0, 3_590_000.0) if hi else (1568.0, 1_150_000.0)
    scale = min(1.0, edge / max(w, h), math.sqrt(mp / (w * h)))
    return int(math.ceil((w * scale) * (h * scale) / 750.0))


_TAG_RE = re.compile(r"<[^>]{1,80}>")
_WS_RE = re.compile(r"\s+")


def clean_prompt(text: str, limit: int = 140) -> str:
    text = _TAG_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:limit]


_LABEL_KEYS = ("command", "cmd", "file_path", "path", "pattern", "query", "url", "notebook_path",
               "skill", "subagent_type", "prompt", "description", "message", "text", "content")


def tool_label(inp) -> str:
    if not isinstance(inp, dict):
        return ""
    for key in _LABEL_KEYS:
        value = inp.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip().splitlines()[0][:140]
    return ""


def fmt_tokens(n):
    n = float(n or 0)
    if n >= 1e6:
        return f"{n / 1e6:.1f}M"
    if n >= 1e5:
        return f"{n / 1e3:.0f}k"
    if n >= 1e3:
        return f"{n / 1e3:.1f}k"
    return str(int(n))


def fmt_duration(s):
    s = float(s or 0)
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{int(s // 60)}m{int(s % 60):02d}s"
    return f"{int(s // 3600)}h{int((s % 3600) // 60):02d}m"


def _title_from(text: str, limit: int = 64) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0].rstrip(" ,.;:")
    return (cut if len(cut) >= limit // 2 else text[:limit].rstrip()) + "…"


# --------------------------------------------------------------------------- pricing

def pricing_for(model, override=None):
    """Rates plus a dated source label, URL and matched key, or None."""
    if override:
        return tuple(override) + ("--price", None, "--price")
    if not model:
        return None
    model_id = str(model).strip().lower()
    # OpenAI rows are closed-world: an unpublished suffix may be a materially different
    # product, so only the exact identifier on the cited public model page is priced.
    if model_id in OPENAI_PRICE_URLS:
        row = PRICING[model_id]
        date = OPENAI_PRICE_DATES.get(model_id, OPENAI_PRICING_DATE)
        return row + (f"OpenAI model page · retrieved {date}",
                      OPENAI_PRICE_URLS[model_id], model_id)
    if model_id.startswith("gpt-") or model_id.startswith("codex-") \
            or any(model_id == name or model_id.startswith(name + "-") for name in UNPRICED_MODELS):
        return None
    raw_key = re.sub(r"\[.*?\]", "", model_id)
    key = re.sub(r"-\d{8}$", "", raw_key)
    best = None
    for name, row in PRICING.items():
        if name in OPENAI_PRICE_URLS:
            continue
        if (key == name or key.startswith(name + "-")) and (best is None or len(name) > len(best[0])):
            best = (name, row)
    if not best:
        return None
    name, row = best
    return row + (f"table {PRICING_DATE}", None, name)


def parse_price(text):
    if not text:
        return None
    parts = [float(x) for x in text.split(",")]
    if len(parts) < 2:
        raise SystemExit("--price expects IN,OUT[,READ_MULT,WRITE5M_MULT,WRITE1H_MULT] in $/M tokens")
    defaults = [0.1, 1.25, 2.0]
    return tuple(parts[:2] + parts[2:5] + defaults[len(parts) - 2:])


# --------------------------------------------------------------------------- discovery

def iter_sessions():
    """Claude Code, Codex CLI and pi sessions on this machine, newest first."""
    found = []
    root = projects_dir()
    if root.is_dir():
        for proj in sorted(root.iterdir()):
            if not proj.is_dir():
                continue
            for f in proj.glob("*.jsonl"):
                try:
                    st = f.stat()
                except OSError:
                    continue
                found.append({"path": f, "fmt": "claude", "project": proj.name, "session_id": f.stem,
                              "size": st.st_size, "mtime": st.st_mtime})
    proot = pi_sessions_dir()
    if proot.is_dir():
        for f in proot.rglob("*.jsonl"):
            try:
                st = f.stat()
            except OSError:
                continue
            sid = f.stem.split("_", 1)[1] if "_" in f.stem else f.stem
            found.append({"path": f, "fmt": "pi", "project": f.parent.name, "session_id": sid,
                          "size": st.st_size, "mtime": st.st_mtime})
    croot = codex_sessions_dir()
    if croot.is_dir():
        for f in croot.glob("*/*/*/rollout-*.jsonl"):
            try:
                st = f.stat()
                with open(f, encoding="utf-8", errors="replace") as fh:
                    first = json.loads(fh.readline())
            except (OSError, ValueError):
                continue
            payload = first.get("payload") if isinstance(first, dict) and first.get("type") == "session_meta" \
                and isinstance(first.get("payload"), dict) else {}
            sid = payload.get("id") or f.stem
            cwd = payload.get("cwd")
            project = Path(cwd).name if isinstance(cwd, str) and cwd else f.parent.name
            found.append({"path": f, "fmt": "codex", "project": project, "session_id": sid,
                          "size": st.st_size, "mtime": st.st_mtime})
    found.sort(key=lambda s: s["mtime"], reverse=True)
    return found


def detect_format(entries, path=None) -> str:
    for e in entries[:5]:
        if isinstance(e, dict) and e.get("type") == "session_meta" and isinstance(e.get("payload"), dict) \
                and e["payload"].get("id"):
            return "codex"
    if path and ".pi" in str(path).split(os.sep):
        return "pi"
    for e in entries[:5]:
        if isinstance(e, dict) and e.get("type") == "session" and "cwd" in e and "id" in e:
            return "pi"
    for e in entries[:50]:
        if isinstance(e, dict) and e.get("type") == "message" and isinstance(e.get("message"), dict) \
                and e["message"].get("role") in ("user", "assistant", "toolResult"):
            return "pi"
    return "claude"


def _first_codex_prompt(path: Path, legacy: bool, max_lines: int) -> str:
    """Prefer the UI user_message while retaining response-item text as fallback."""
    seen_turn = False
    active = not legacy
    fallback = ""
    preferred = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for i, line in enumerate(fh):
            if not legacy and i >= max_lines:
                break
            try:
                e = json.loads(line)
            except ValueError:
                continue
            payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
            if e.get("type") == "event_msg" and payload.get("type") == "task_started" and legacy:
                active, seen_turn, fallback, preferred = True, False, "", ""
                continue
            if not active:
                continue
            if e.get("type") == "turn_context":
                seen_turn = True
                continue
            if e.get("type") == "event_msg" and payload.get("type") == "user_message":
                text = clean_prompt(payload.get("message") or "", 90)
                if text and not preferred:
                    preferred = text
                    if not legacy:
                        return preferred
                continue
            if e.get("type") == "response_item" and payload.get("type") == "message" \
                    and payload.get("role") == "user" and seen_turn:
                text = clean_prompt(text_of(payload.get("content")), 90)
                if text and not fallback:
                    fallback = text
    return preferred or fallback


def first_prompt(path: Path, max_lines: int = 400) -> str:
    codex_turn = False
    codex_fallback = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            try:
                first = json.loads(fh.readline())
            except ValueError:
                first = None
        if isinstance(first, dict) and first.get("type") == "session_meta" \
                and isinstance(first.get("payload"), dict) and first["payload"].get("id"):
            return _first_codex_prompt(path, first["payload"].get("history_mode") == "legacy", max_lines)
        with open(path, encoding="utf-8", errors="replace") as fh:
            for i, line in enumerate(fh):
                if i >= max_lines:
                    break
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("type") == "turn_context":
                    codex_turn = True
                    continue
                if e.get("type") == "event_msg" and isinstance(e.get("payload"), dict) \
                        and e["payload"].get("type") == "user_message":
                    txt = clean_prompt(e["payload"].get("message") or "", 90)
                    if txt:
                        return txt
                if e.get("type") == "response_item" and isinstance(e.get("payload"), dict):
                    item = e["payload"]
                    if item.get("type") == "message" and item.get("role") == "user":
                        txt = clean_prompt(text_of(item.get("content")), 90)
                        if txt and codex_turn:
                            return txt
                        codex_fallback = codex_fallback or txt
                if e.get("type") == "message" and isinstance(e.get("message"), dict) and e["message"].get("role") == "user":
                    txt = clean_prompt(text_of(e["message"].get("content")), 90)
                    if txt:
                        return txt
                if e.get("type") != "user" or e.get("isMeta") or e.get("isCompactSummary"):
                    continue
                content = (e.get("message") or {}).get("content")
                if isinstance(content, list) and any(
                        isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
                    continue
                txt = clean_prompt(text_of(content), 90)
                if txt and not txt.startswith(INTERRUPT_PREFIX):
                    return txt
    except OSError:
        pass
    return codex_fallback


def resolve_session(arg: str) -> Path:
    p = Path(arg).expanduser()
    if p.is_file():
        return p
    if p.is_dir():
        files = sorted(p.rglob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            return files[0]
        raise SystemExit(f"no .jsonl transcripts in {p}")
    sessions = iter_sessions()
    if arg == "latest":
        if not sessions:
            raise SystemExit(f"no sessions found under {projects_dir()}, {codex_sessions_dir()} or {pi_sessions_dir()}")
        return sessions[0]["path"]
    matches = [s for s in sessions if s["session_id"].startswith(arg)]
    if len(matches) == 1:
        return matches[0]["path"]
    if not matches:
        raise SystemExit(f"no session matching {arg!r} (try: tokenograph list)")
    raise SystemExit("ambiguous session id, matches:\n  " + "\n  ".join(str(m["path"]) for m in matches))


def subagent_files(session_path: Path):
    side = session_path.parent / session_path.stem
    if not side.is_dir():
        return []
    return sorted(f for f in side.rglob("*.jsonl") if f.is_file())


# --------------------------------------------------------------------------- reader

class TranscriptReader:
    """Incremental JSONL reader that tolerates a file still being appended to."""

    def __init__(self, path):
        self.path = Path(path)
        self.offset = 0
        self.buf = b""
        self.entries = []
        self.size = 0
        self.mtime = 0.0

    def refresh(self) -> bool:
        try:
            st = self.path.stat()
        except OSError:
            return False
        if st.st_size < self.offset:
            self.offset, self.buf, self.entries = 0, b"", []
        if st.st_size == self.offset:
            return False
        with open(self.path, "rb") as fh:
            fh.seek(self.offset)
            chunk = fh.read()
            self.offset = fh.tell()
        data = self.buf + chunk
        lines = data.split(b"\n")
        self.buf = lines.pop()
        for raw in lines:
            raw = raw.strip()
            if not raw:
                continue
            try:
                self.entries.append(json.loads(raw))
            except ValueError:
                continue
        self.size, self.mtime = st.st_size, st.st_mtime
        return True


# --------------------------------------------------------------------------- scanning

def _new_ctx():
    return {"models": [], "tools": [], "prompts": [], "interrupts": [], "errors": [],
            "compact_boundaries": [], "compact_summaries": [], "compact_events": [], "meta": {},
            "open_tools": [], "tool_chars": 0, "items": [], "snapshots": [], "tool_records": [],
            "deferred_listing": [], "model_changes": [], "reported_cost": 0.0, "n_entries": 0,
            "active_start_ts": None, "tool_details": []}


def _item(ctx, idx, ts, group, sub, chars=0, img=0, extra=None):
    it = {"i": idx, "t": ts, "g": group, "s": sub, "ch": int(chars), "img": int(img)}
    if extra:
        it.update(extra)
    ctx["items"].append(it)
    return it


def _bump(ctx, key, ts):
    cur = ctx.get(key)
    if ts is not None:
        ctx[key] = ts if cur is None else max(cur, ts)


ADAPTER_META = {
    "claude": {"agent": "claude-code", "agent_label": "Claude Code", "cli_label": "Claude Code"},
    "codex": {"agent": "codex", "agent_label": "Codex CLI", "cli_label": "Codex CLI"},
    "pi": {"agent": "pi", "agent_label": "pi", "cli_label": "pi"},
}


def _set_adapter_meta(meta, fmt):
    meta.setdefault("fmt", fmt)
    for key, value in ADAPTER_META[fmt].items():
        meta.setdefault(key, value)


def _scan_claude(entries, sub: bool, ctx: dict, stream: str = "main"):
    """One pass over a Claude Code transcript stream, in file order."""
    last_input_ts = None
    last_any_ts = None
    requests = {}
    pending_tools = {}
    meta = ctx["meta"]
    last_skill = None
    cur_model = None
    ctx["n_entries"] += len(entries)

    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        kind = e.get("type")
        ts = parse_ts(e.get("timestamp"))
        # Metadata can arrive over several entries; adapter labels are not a signal
        # that session identity, project and CLI fields have all been collected.
        if not sub:
            for key, src in (("session_id", "sessionId"), ("cwd", "cwd"), ("git_branch", "gitBranch"),
                             ("cli_version", "version")):
                if key not in meta and e.get(src):
                    meta[key] = e[src]
            _set_adapter_meta(meta, "claude")

        if kind == "assistant":
            msg = e.get("message") or {}
            if e.get("isApiErrorMessage"):
                ctx["errors"].append({"k": "e", "t0": ts, "t1": ts, "sub": int(sub),
                                      "l": clean_prompt(text_of(msg.get("content")), 160)})
                if ts is not None:
                    last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
                continue
            rid = e.get("requestId") or msg.get("id") or e.get("uuid") or f"{stream}:{idx}"
            call = requests.get(rid)
            if call is None:
                call = {"k": "m", "rid": rid, "t0": last_input_ts if last_input_ts is not None else ts,
                        "t1": ts, "blocks": [], "usage": None, "model": msg.get("model"),
                        "sub": int(sub), "err": 0, "stop": None, "think_chars": 0, "text_chars": 0,
                        "tools": [], "stream": stream, "idx": idx, "fmt": "claude"}
                requests[rid] = call
                ctx["models"].append(call)
            if ts is not None:
                call["t1"] = ts if call["t1"] is None else max(call["t1"], ts)
                last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
            if msg.get("stop_reason"):
                call["stop"] = msg["stop_reason"]
            if isinstance(msg.get("usage"), dict):
                call["usage"] = msg["usage"]
            if msg.get("model"):
                call["model"] = msg["model"]
                cur_model = msg["model"]
            content = msg.get("content")
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content or ""}]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt in ("thinking", "redacted_thinking"):
                    call["think_chars"] += len(b.get("thinking") or "")
                    call["blocks"].append((ts, "thinking"))
                elif bt == "text":
                    n = len(b.get("text") or "")
                    call["text_chars"] += n
                    call["blocks"].append((ts, "text"))
                    if not sub and n:
                        _item(ctx, idx, ts, "assistant", None, n)
                elif bt == "tool_use":
                    name = b.get("name") or "tool"
                    call["blocks"].append((ts, "tool_use"))
                    call["tools"].append(name)
                    inp = b.get("input")
                    tc = {"k": "t", "n": name, "t0": ts, "t1": None, "l": tool_label(inp),
                          "sub": int(sub), "err": 0, "ch": 0, "id": b.get("id"), "open": 0, "idx": idx,
                          "in_ch": len(json.dumps(inp, ensure_ascii=False)) if inp is not None else 0}
                    if name == "Skill" and isinstance(inp, dict):
                        last_skill = inp.get("skill") or "skill"
                    if b.get("id"):
                        pending_tools[b["id"]] = tc
                    ctx["tools"].append(tc)
                    if not sub:
                        _item(ctx, idx, ts, "tool_input", name, tc["in_ch"])
            continue

        prev_any_ts = last_any_ts
        if ts is not None:
            last_input_ts = ts if last_input_ts is None else max(last_input_ts, ts)
            last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)

        if kind == "user":
            msg = e.get("message") or {}
            content = msg.get("content")
            results = [b for b in content if isinstance(b, dict) and b.get("type") == "tool_result"] \
                if isinstance(content, list) else []
            if results:
                for b in results:
                    tc = pending_tools.pop(b.get("tool_use_id"), None)
                    chars = len(text_of(b.get("content")))
                    imgs = image_blocks(b.get("content"))
                    img_tok = 0
                    for im in imgs:
                        src = im.get("source") or {}
                        dims = None
                        tr = e.get("toolUseResult")
                        if isinstance(tr, dict) and isinstance(tr.get("file"), dict):
                            d = tr["file"].get("dimensions") or {}
                            if d.get("displayWidth") and d.get("displayHeight"):
                                dims = (d["displayWidth"], d["displayHeight"])
                        img_tok += image_tokens(src.get("media_type"), src.get("data"), dims, cur_model)
                    ctx["tool_chars"] += chars
                    name = tc["n"] if tc else "tool"
                    if not sub:
                        _item(ctx, idx, ts, "tool_result", name, chars, 0)
                        if img_tok:
                            _item(ctx, idx, ts, "image", name, 0, img_tok)
                    if tc is None:
                        continue
                    tc["t1"] = ts
                    tc["err"] = int(bool(b.get("is_error")))
                    tc["ch"] = chars
                    tc["img"] = img_tok
                continue
            if e.get("isCompactSummary"):
                ctx["compact_summaries"].append({"ts": ts, "idx": idx, "stream": stream})
                if not sub:
                    _item(ctx, idx, ts, "compaction", None, len(text_of(content)))
                continue
            txt = text_of(content)
            if e.get("isMeta"):
                if not sub and txt.strip():
                    _item(ctx, idx, ts, "skill", last_skill or "meta", len(txt))
                continue
            stripped = txt.strip()
            if stripped.startswith(INTERRUPT_PREFIX):
                ctx["interrupts"].append({"k": "x", "t0": ts, "t1": ts, "sub": int(sub), "l": stripped[:80]})
                continue
            imgs = image_blocks(content)
            if not stripped and not imgs:
                continue
            if not sub:
                if stripped:
                    ctx["prompts"].append({"k": "p", "t0": ts, "t1": ts, "l": clean_prompt(stripped), "ch": len(txt)})
                    _item(ctx, idx, ts, "prompt", None, len(txt))
                for im in imgs:
                    src = im.get("source") or {}
                    _item(ctx, idx, ts, "image", "user", 0, image_tokens(src.get("media_type"), src.get("data"), None, cur_model))
        elif kind == "attachment":
            if sub:
                continue
            att = e.get("attachment") or {}
            atype = att.get("type") or "attachment"
            rendered = e.get("rendered")
            chars = 0
            if isinstance(rendered, list):
                for r in rendered:
                    chars += len(r.get("content") or "") if isinstance(r, dict) else len(str(r))
            elif isinstance(rendered, str):
                chars = len(rendered)
            if atype == "prompt_snapshot":
                sections = []
                sp = att.get("systemPrompt")
                if isinstance(sp, list):
                    for s in sp:
                        s = s if isinstance(s, str) else json.dumps(s, ensure_ascii=False)
                        first = (s.strip().splitlines() or [""])[0].strip()
                        sections.append((clean_prompt(first, 60) or "(section)", len(s)))
                elif isinstance(sp, str):
                    sections.append(("system prompt", len(sp)))
                digest = hashlib.sha1(json.dumps(sections).encode()).hexdigest()[:12]
                ctx["snapshots"].append({"idx": idx, "ts": ts, "sections": sections, "hash": digest})
                continue
            if atype == "deferred_tools_record":
                # the ToolSearch result is a tool_reference block; the API expands it in place to the
                # tool's definition, so the schema lands in the messages (and in cache_creation)
                for ent in att.get("entries") or []:
                    if isinstance(ent, dict) and ent.get("name"):
                        n = len(json.dumps(ent, ensure_ascii=False))
                        ctx["tool_records"].append({"idx": idx, "ts": ts, "name": ent["name"], "ch": n})
                        _item(ctx, idx, ts, "tool_schema", ent["name"], n)
                continue
            if not chars:
                for key in ("text", "content"):
                    if isinstance(att.get(key), str):
                        chars = len(att[key])
                        break
            if atype == "deferred_tools_delta":
                ctx["deferred_listing"].append({"idx": idx, "ts": ts, "ch": chars,
                                                "added": list(att.get("addedNames") or []),
                                                "removed": list(att.get("removedNames") or [])})
            if chars:
                _item(ctx, idx, ts, "attach", atype, chars)
        elif kind == "system" and e.get("subtype") == "compact_boundary":
            cm = e.get("compactMetadata") or {}
            ctx["compact_boundaries"].append({"ts": ts, "idx": idx, "prev": prev_any_ts, "stream": stream,
                                              "pre": cm.get("preTokens"), "trigger": cm.get("trigger")})

    ctx["open_tools"].extend(pending_tools.values())


def _scan_pi(entries, sub: bool, ctx: dict, stream: str = "main"):
    """One pass over a pi session file (github.com/badlogic/pi-mono), in file order."""
    meta = ctx["meta"]
    last_any_ts = None
    last_assistant = None
    model = None
    ctx["n_entries"] += len(entries)
    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        kind = e.get("type")
        ts = parse_ts(e.get("timestamp"))
        if kind == "session":
            if not sub:
                _set_adapter_meta(meta, "pi")
                meta.setdefault("session_id", e.get("id"))
                meta.setdefault("cwd", e.get("cwd"))
                if e.get("modelId"):
                    model = e["modelId"]
            continue
        if kind == "session_info" and e.get("name") and not sub:
            meta["name"] = e["name"]
            continue
        if kind == "model_change":
            model = e.get("modelId") or model
            ctx["model_changes"].append({"ts": ts, "idx": idx, "model": model, "provider": e.get("provider")})
            continue
        if kind == "compaction":
            summary = e.get("summary") or ""
            t0 = last_any_ts if last_any_ts is not None else ts
            ctx["compact_events"].append({"t0": t0, "t1": ts, "idx": idx, "pre": e.get("tokensBefore"),
                                          "label": "compaction" + (f" · {fmt_tokens(e['tokensBefore'])} tokens before" if e.get("tokensBefore") else "")})
            if not sub:
                _item(ctx, idx, ts, "compaction", None, len(summary))
            _bump_last = ts
            if ts is not None:
                last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
            continue
        if kind != "message" or not isinstance(e.get("message"), dict):
            if ts is not None:
                last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
            continue
        msg = e["message"]
        role = msg.get("role")
        mts = parse_ts(msg.get("timestamp"))
        if role == "user":
            content = msg.get("content")
            txt = text_of(content)
            if not sub:
                if txt.strip():
                    ctx["prompts"].append({"k": "p", "t0": ts, "t1": ts, "l": clean_prompt(txt.strip()), "ch": len(txt)})
                    _item(ctx, idx, ts, "prompt", None, len(txt))
                for im in image_blocks(content):
                    _item(ctx, idx, ts, "image", "user", 0, image_tokens(im.get("mimeType"), im.get("data")))
        elif role == "assistant":
            usage = msg.get("usage") or {}
            t0 = mts if mts is not None else (last_any_ts if last_any_ts is not None else ts)
            if ts is not None and t0 is not None and t0 > ts:
                t0 = ts
            call = {"k": "m", "rid": e.get("id") or f"{stream}:{idx}", "t0": t0, "t1": ts, "blocks": [],
                    "usage": usage, "model": msg.get("model") or model, "sub": int(sub),
                    "err": int(msg.get("stopReason") == "error"),
                    "stop": {"stop": "end_turn", "toolUse": "tool_use"}.get(msg.get("stopReason"), msg.get("stopReason")),
                    "think_chars": 0, "text_chars": 0, "tools": [], "stream": stream, "idx": idx, "fmt": "pi",
                    "cost": ((usage.get("cost") or {}).get("total") or 0.0)}
            ctx["reported_cost"] += call["cost"] or 0.0
            if msg.get("stopReason") == "error" or msg.get("errorMessage"):
                ctx["errors"].append({"k": "e", "t0": ts, "t1": ts, "sub": int(sub),
                                      "l": clean_prompt(str(msg.get("errorMessage") or "error"), 160)})
            pending = []
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                bt = b.get("type")
                if bt == "thinking":
                    call["think_chars"] += len(b.get("thinking") or "")
                    call["blocks"].append((None, "thinking"))
                elif bt == "text":
                    n = len(b.get("text") or "")
                    call["text_chars"] += n
                    call["blocks"].append((None, "text"))
                    if not sub and n:
                        _item(ctx, idx, ts, "assistant", None, n)
                elif bt == "toolCall":
                    name = b.get("name") or "tool"
                    args = b.get("arguments")
                    call["tools"].append(name)
                    call["blocks"].append((None, "tool_use"))
                    tc = {"k": "t", "n": name, "t0": ts, "t1": None, "l": tool_label(args), "sub": int(sub),
                          "err": 0, "ch": 0, "id": b.get("id"), "open": 0, "idx": idx,
                          "in_ch": len(json.dumps(args, ensure_ascii=False)) if args is not None else 0}
                    pending.append(tc)
                    ctx["tools"].append(tc)
                    if not sub:
                        _item(ctx, idx, ts, "tool_input", name, tc["in_ch"])
            ctx["models"].append(call)
            last_assistant = {"call": call, "pending": {t["id"]: t for t in pending if t.get("id")},
                              "cursor": ts}
        elif role == "toolResult":
            tc = None
            if last_assistant:
                tc = last_assistant["pending"].pop(msg.get("toolCallId"), None)
            content = msg.get("content")
            chars = len(text_of(content))
            img_tok = sum(image_tokens(im.get("mimeType"), im.get("data")) for im in image_blocks(content))
            ctx["tool_chars"] += chars
            name = tc["n"] if tc else (msg.get("toolName") or "tool")
            if not sub:
                _item(ctx, idx, ts, "tool_result", name, chars)
                if img_tok:
                    _item(ctx, idx, ts, "image", name, 0, img_tok)
            if tc is not None:
                start = last_assistant["cursor"] if last_assistant else ts
                end = mts if mts is not None else ts
                if start is not None and end is not None and end < start:
                    start = end
                tc["t0"], tc["t1"] = start, end
                tc["err"] = int(bool(msg.get("isError")))
                tc["ch"], tc["img"] = chars, img_tok
                if last_assistant:
                    last_assistant["cursor"] = end
        elif role == "bashExecution":
            n = len(msg.get("command") or "") + len(msg.get("output") or "")
            if not sub:
                _item(ctx, idx, ts, "bash_user", None, n)
                ctx["interrupts"].append({"k": "b", "t0": ts, "t1": ts, "sub": 0,
                                          "l": "bash: " + (msg.get("command") or "")[:70]})
        elif role in ("custom", "branchSummary", "compactionSummary"):
            n = len(text_of(msg.get("content"))) if msg.get("content") is not None else 0
            if not sub and n:
                _item(ctx, idx, ts, "custom" if role == "custom" else "compaction", msg.get("customType") if role == "custom" else None, n)
        if ts is not None:
            last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
    if last_assistant:
        ctx["open_tools"].extend(last_assistant["pending"].values())


def _codex_arg(raw):
    if isinstance(raw, (dict, list)):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _codex_label(raw):
    value = _codex_arg(raw)
    if isinstance(value, dict):
        return tool_label(value)
    if isinstance(value, list):
        return clean_prompt(json.dumps(value, ensure_ascii=False), 140)
    return clean_prompt((str(value or "").splitlines() or [""])[0], 140)


def _codex_chars(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, list):
        text = text_of(value)
        return len(text) if text else sum(_codex_chars(v) for v in value)
    if value is None:
        return 0
    if isinstance(value, dict) and value.get("type") in ("image", "input_image"):
        return 0
    if isinstance(value, dict):
        safe = {k: v for k, v in value.items() if k not in ("data", "image_url")}
        return len(json.dumps(safe, ensure_ascii=False))
    return len(json.dumps(value, ensure_ascii=False))


def _codex_tool_detail(item, ts):
    """A recorded tool completion, not another billable call or timing interval."""
    if not isinstance(item, dict) or not item.get("id") or ts is None:
        return None
    kind, output = item.get("type"), None
    if kind == "CommandExecution":
        name = "exec_command"
        command = item.get("command") or []
        detail = command[-1] if isinstance(command, list) and command else command
        label = str(detail or "").splitlines()[0] if detail else ""
        output = item.get("aggregated_output")
        if output is None and ("stdout" in item or "stderr" in item):
            output = str(item.get("stdout") or "") + str(item.get("stderr") or "")
    elif kind == "McpToolCall":
        name = ".".join(str(item.get(k) or "") for k in ("server", "tool")).strip(".")
        args = item.get("arguments")
        label = _codex_label(args)
        if not label and isinstance(args, dict):
            label = str(args.get("title") or args.get("description") or "")
        detail = json.dumps(args, ensure_ascii=False, indent=2) if args is not None else ""
        output = item.get("result")
    elif kind == "FileChange":
        name = "apply_patch"
        changes = item.get("changes") or {}
        paths = list(changes) if isinstance(changes, dict) else [
            str(c.get("path", "")) for c in changes if isinstance(c, dict)]
        label, detail = ", ".join(paths), "\n".join(paths)
    elif kind == "Extension" and str(item.get("kind", "")).startswith("web."):
        name = str(item["kind"])
        args = item.get("query") or item.get("action")
        label = _codex_label(args)
        detail = json.dumps(args, ensure_ascii=False, indent=2) if not isinstance(args, str) else args
    else:
        return None
    duration = item.get("duration")
    seconds = None
    if isinstance(duration, dict):
        secs, nanos = duration.get("secs", 0), duration.get("nanos", 0)
        if isinstance(secs, (int, float)) and isinstance(nanos, (int, float)):
            seconds = max(0, secs + nanos / 1e9)
    result = item.get("result") if isinstance(item.get("result"), dict) else {}
    failed = bool(item.get("error") or result.get("isError") or
                  item.get("status") in ("failed", "error") or item.get("exit_code") not in (None, 0))
    detail = str(detail or "")
    return {"id": str(item["id"]), "name": name, "label": str(label or "")[:240],
            "detail": detail[:2000], "truncated": len(detail) > 2000,
            "completed_at": ts, "duration_s": seconds,
            "status": "failed" if failed else item.get("status", "observed"),
            "exit_code": item.get("exit_code"),
            "output_chars": _codex_chars(output) if output is not None else None,
            "source": "completed item"}


def observed_tools(entries, limit=12):
    """Latest recorded completions, without guessing execution from script text."""
    result, seen = [], set()
    for entry in reversed(entries):
        if not isinstance(entry, dict):
            continue
        payload = entry.get("payload") or {}
        if entry.get("type") != "event_msg" or payload.get("type") != "item_completed":
            continue
        detail = _codex_tool_detail(payload.get("item"), parse_ts(entry.get("timestamp")))
        if detail and detail["id"] not in seen:
            seen.add(detail["id"])
            result.append(detail)
            if limit is not None and len(result) >= limit:
                break
    return result


def _scan_codex(entries, sub: bool, ctx: dict, stream: str = "main"):
    """One pass over a Codex CLI rollout, across the legacy and current envelopes.

    `response_item` timestamps delimit model output and tools. Per-request usage comes
    from changed `token_count` totals; early `token_usage_record` values remain
    provisional until a changed counter confirms or corrects them.
    Reasoning bytes are encrypted: only their timestamp and reported token count are used.
    """
    meta = ctx["meta"]
    ctx["n_entries"] += len(entries)
    current_model = None
    last_input_ts = None
    last_any_ts = None
    pending_model = None
    last_model = None
    last_usage_idx = -1
    unassigned_models = []
    pending_tools = {}
    tools_by_id = {}
    detail_ids = set()
    prompt_seen = {}
    last_prompt = None
    seen_turn = False
    seen_identity = bool(meta.get("session_id"))
    provisional_sections = []
    system_sections = []
    have_developer_items = False
    last_total_sig = None
    event_agent = None
    event_reasoning_ts = None
    fast_model = None
    first_meta_payload = next((e.get("payload") for e in entries if isinstance(e, dict)
                               and e.get("type") == "session_meta" and isinstance(e.get("payload"), dict)), {})
    legacy = first_meta_payload.get("history_mode") == "legacy"
    legacy_starts = [i for i, e in enumerate(entries) if isinstance(e, dict) and e.get("type") == "event_msg"
                     and isinstance(e.get("payload"), dict) and e["payload"].get("type") == "task_started"]
    activity_start = max(legacy_starts) if legacy and legacy_starts else 0
    if legacy and legacy_starts:
        ctx["active_start_ts"] = parse_ts(entries[activity_start].get("timestamp"))

    def record_snapshot(idx, ts, sections):
        if sub or not sections:
            return
        digest = hashlib.sha1(json.dumps(sections).encode()).hexdigest()[:12]
        if ctx["snapshots"] and ctx["snapshots"][-1]["hash"] == digest:
            return
        ctx["snapshots"].append({"idx": idx, "ts": ts, "sections": list(sections), "hash": digest})

    def content_sections(content, prefix):
        blocks = content if isinstance(content, list) else [{"type": "input_text", "text": content or ""}]
        out = []
        for n, block in enumerate(blocks, 1):
            if not isinstance(block, dict):
                continue
            text = block.get("text") if block.get("type") in ("text", "input_text", "output_text") else ""
            if not isinstance(text, str) or not text:
                continue
            first = (text.strip().splitlines() or [""])[0]
            out.append((clean_prompt(first, 60) or f"{prefix} {n}", len(text)))
        return out

    def add_prompt(idx, ts, text, source="response"):
        nonlocal last_input_ts, last_prompt
        text = text if isinstance(text, str) else text_of(text)
        stripped = text.strip()
        if not stripped:
            return
        if last_prompt is not None and (ts is None or last_prompt["ts"] is None
                                        or abs(ts - last_prompt["ts"]) <= 2.0):
            previous = last_prompt["text"]
            if stripped.startswith(previous) or previous.startswith(stripped):
                n = max(last_prompt["record"]["ch"], len(text))
                last_prompt["record"]["ch"] = n
                last_prompt["item"]["ch"] = n
                if source == "event":
                    last_prompt["record"]["l"] = clean_prompt(stripped)
                if len(stripped) > len(previous):
                    last_prompt["text"] = stripped
                last_prompt["ts"] = ts
                last_input_ts = ts if ts is not None else last_input_ts
                return
        sig = hashlib.sha1(stripped.encode()).hexdigest()
        prev = prompt_seen.get(sig)
        if prev is not None and (ts is None or prev is None or abs(ts - prev) <= 2.0):
            last_input_ts = ts if ts is not None else last_input_ts
            return
        prompt_seen[sig] = ts
        if not sub:
            record = {"k": "p", "t0": ts, "t1": ts, "l": clean_prompt(stripped), "ch": len(text)}
            ctx["prompts"].append(record)
            item = _item(ctx, idx, ts, "prompt", None, len(text))
            last_prompt = {"text": stripped, "ts": ts, "record": record, "item": item}
        if ts is not None:
            last_input_ts = ts

    def ensure_model(idx, ts):
        nonlocal pending_model
        if pending_model is None:
            t0 = last_input_ts if last_input_ts is not None else (last_any_ts if last_any_ts is not None else ts)
            pending_model = {
                "k": "m", "rid": f"{stream}:{idx}", "t0": t0, "t1": ts, "blocks": [],
                "usage": None, "model": current_model, "sub": int(sub), "err": 0, "stop": None,
                "think_chars": 0, "text_chars": 0, "tools": [], "stream": stream, "idx": idx,
                "fmt": "codex", "context_window": None,
            }
            ctx["models"].append(pending_model)
        if ts is not None:
            pending_model["t1"] = ts if pending_model["t1"] is None else max(pending_model["t1"], ts)
        if current_model:
            pending_model["model"] = current_model
        return pending_model

    def close_model():
        nonlocal pending_model
        if pending_model is not None:
            unassigned_models.append(pending_model)
            pending_model = None

    def usage_sig(usage):
        if not isinstance(usage, dict):
            return None
        return tuple(int(usage.get(k) or 0) for k in (
            "input_tokens", "cached_input_tokens", "cache_write_input_tokens",
            "output_tokens", "reasoning_output_tokens"))

    def finish_model(usage, window=None, usage_idx=None, provisional=False):
        nonlocal pending_model, last_model, last_usage_idx, event_agent, event_reasoning_ts
        if not isinstance(usage, dict):
            return None
        target = pending_model if pending_model is not None and pending_model["idx"] > last_usage_idx else None
        if target is None:
            target = next((call for call in reversed(unassigned_models)
                           if call.get("usage") is None and call["idx"] > last_usage_idx), None)
        if target is None and event_agent is not None:
            idx, ts, text = event_agent
            target = ensure_model(idx, ts)
            if event_reasoning_ts is not None:
                target["blocks"].append((event_reasoning_ts, "thinking"))
            target["text_chars"] += len(text)
            target["blocks"].append((ts, "text"))
            if not sub and text:
                _item(ctx, idx, ts, "assistant", None, len(text))
        if target is None:
            if last_model is not None and window:
                last_model["context_window"] = int(window)
            return None
        if provisional:
            target["_fast_pending"] = True
            target["_fast_prior_stop"] = target.get("stop")
        target["usage"] = usage
        if window:
            target["context_window"] = int(window)
        if target["stop"] is None:
            target["stop"] = "tool_use" if target["tools"] else "end_turn"
        last_model = target
        if pending_model is target:
            pending_model = None
        if usage_idx is not None:
            last_usage_idx = max(last_usage_idx, usage_idx)
        event_agent = None
        event_reasoning_ts = None
        return target

    def merge_fast_tail(window=None):
        """Fold output emitted after an early 0.153+ usage record into that response."""
        nonlocal pending_model
        if fast_model is None:
            return
        tails = [c for c in list(ctx["models"])
                 if c is not fast_model and c.get("usage") is None and c["idx"] > fast_model["idx"]]
        for tail in tails:
            if tail.get("t1") is not None:
                fast_model["t1"] = max(fast_model["t1"], tail["t1"])
            fast_model["blocks"].extend(tail["blocks"])
            fast_model["think_chars"] += tail["think_chars"]
            fast_model["text_chars"] += tail["text_chars"]
            fast_model["tools"].extend(tail["tools"])
            fast_model["err"] = int(bool(fast_model["err"] or tail["err"]))
            if tail.get("stop") is not None:
                fast_model["stop"] = tail["stop"]
            for tool in ctx["tools"]:
                if tool.get("_rid") == tail["rid"]:
                    tool["_rid"] = fast_model["rid"]
            ctx["models"].remove(tail)
            if tail in unassigned_models:
                unassigned_models.remove(tail)
            if pending_model is tail:
                pending_model = None
        if window:
            fast_model["context_window"] = int(window)

    def start_tool(idx, ts, payload, ptype):
        call = ensure_model(idx, ts)
        raw = payload.get("arguments") if ptype == "function_call" else payload.get("input")
        if ptype == "tool_search_call":
            raw = payload.get("arguments") or payload.get("input") or payload.get("query")
        elif ptype == "web_search_call":
            raw = payload.get("action") or payload.get("query")
        name = payload.get("name") or {
            "tool_search_call": "tool_search", "web_search_call": "web_search",
        }.get(ptype, "tool")
        call_id = payload.get("call_id") or payload.get("id")
        in_ch = _codex_chars(raw)
        tc = {"k": "t", "n": name, "t0": ts, "t1": None, "l": _codex_label(raw),
              "sub": int(sub), "err": int(payload.get("status") in ("failed", "error")),
              "ch": 0, "id": call_id, "open": 0, "idx": idx, "in_ch": in_ch,
              "_rid": call["rid"], "_raw": raw}
        ctx["tools"].append(tc)
        if call_id:
            tools_by_id[call_id] = tc
        if ptype == "web_search_call":
            # The corresponding event has no stable call id in several CLI versions.
            # Keep the activity marker without inventing a wall-clock interval.
            tc["t1"] = ts
        elif call_id:
            pending_tools[call_id] = tc
        call["blocks"].append((ts, "tool_use"))
        call["tools"].append(name)
        call["stop"] = "tool_use"
        if not sub:
            _item(ctx, idx, ts, "tool_input", name, in_ch)

    def finish_tool(idx, ts, payload):
        nonlocal last_input_ts
        call_id = payload.get("call_id") or payload.get("id")
        tc = pending_tools.pop(call_id, None)
        is_search = payload.get("type") == "tool_search_output"
        output = payload.get("tools") if is_search else payload.get("output")
        chars = _codex_chars(output)
        ctx["tool_chars"] += chars
        name = tc["n"] if tc else "tool"
        if not sub and not is_search:
            _item(ctx, idx, ts, "tool_result", name, chars)
        if is_search and isinstance(output, list):
            for group in output:
                if not isinstance(group, dict):
                    continue
                schemas = group.get("tools") if isinstance(group.get("tools"), list) else [group]
                for schema in schemas:
                    if not isinstance(schema, dict):
                        continue
                    schema_name = schema.get("name") or group.get("name") or "tool"
                    schema_chars = len(json.dumps(schema, ensure_ascii=False))
                    ctx["tool_records"].append({"name": schema_name, "ch": schema_chars,
                                                "idx": idx, "ts": ts})
                    if not sub:
                        _item(ctx, idx, ts, "tool_schema", schema_name, schema_chars)
        if tc is not None:
            tc["t1"] = ts
            tc["ch"] = chars
            tc["err"] = int(bool(tc["err"] or payload.get("is_error") or payload.get("error")
                                   or payload.get("status") in ("failed", "error")))
        if ts is not None:
            last_input_ts = ts

    def mark_item_failure(payload):
        """Join current structured completion failures without parsing result text."""
        item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
        failed = bool(item.get("error") or item.get("status") in ("failed", "error")
                      or item.get("exit_code") not in (None, 0))
        if not failed:
            return
        open_tools = list(pending_tools.values())
        matches = []
        if item.get("type") == "CommandExecution":
            command = item.get("command") if isinstance(item.get("command"), list) else []
            needle = str(command[-1]) if command else ""
            compatible = [t for t in open_tools if t["n"] in ("exec", "exec_command")]
            for tool in compatible:
                raw = _codex_arg(tool.get("_raw"))
                direct = raw.get("cmd") or raw.get("command") if isinstance(raw, dict) else None
                if (direct is not None and str(direct) == needle) \
                        or (isinstance(raw, str) and needle and needle in raw):
                    matches.append(tool)
            if not matches and len(compatible) == 1:
                matches = compatible
        elif item.get("type") == "McpToolCall":
            tool_name = item.get("tool")
            arguments = item.get("arguments")
            compatible = [t for t in open_tools if t["n"] == tool_name or t["n"] == "exec"]
            for tool in compatible:
                raw = _codex_arg(tool.get("_raw"))
                if tool["n"] == tool_name and raw == arguments:
                    matches.append(tool)
                    continue
                if tool["n"] == "exec" and isinstance(raw, str):
                    leaf = str(tool_name or "").split(".")[-1].replace("-", "_")
                    scalar_args = [str(v) for v in (arguments or {}).values()
                                   if isinstance(v, (str, int, float, bool))]
                    if leaf and leaf in raw and all(value in raw for value in scalar_args):
                        matches.append(tool)
            if not matches and len(compatible) == 1:
                matches = compatible
        if len(matches) == 1:
            matches[0]["err"] = 1

    for idx, e in enumerate(entries):
        if not isinstance(e, dict):
            continue
        kind = e.get("type")
        payload = e.get("payload") if isinstance(e.get("payload"), dict) else {}
        ts = parse_ts(e.get("timestamp"))
        prev_any_ts = last_any_ts

        if kind == "session_meta":
            if not sub and not seen_identity:
                _set_adapter_meta(meta, "codex")
                meta["session_id"] = payload.get("id")
                meta["cwd"] = payload.get("cwd")
                meta["cli_version"] = payload.get("cli_version")
                git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
                meta["git_branch"] = git.get("branch")
                meta["originator"] = payload.get("originator")
                seen_identity = True
                base = payload.get("base_instructions")
                base_text = base.get("text") if isinstance(base, dict) else base
                if isinstance(base_text, str) and base_text:
                    provisional_sections = [("base instructions", len(base_text))]
                    record_snapshot(idx, ts, provisional_sections)
            if ts is not None:
                last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
            continue

        # Legacy resumed rollouts embed an entire old conversation before the last
        # task_started marker. None of those replay records belongs to this file's
        # measured turn or ledger.
        if idx < activity_start:
            if ts is not None:
                last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)
            continue

        if kind == "turn_context":
            close_model()
            model = payload.get("model")
            if model and model != current_model:
                current_model = model
                ctx["model_changes"].append({"ts": ts, "idx": idx, "model": model, "provider": "openai"})
            if not sub and payload.get("cwd"):
                meta.setdefault("cwd", payload["cwd"])
            seen_turn = True
            if ts is not None:
                last_input_ts = ts if last_input_ts is None else max(last_input_ts, ts)

        elif kind == "response_item":
            ptype = payload.get("type")
            if ptype == "message":
                role = payload.get("role")
                content = payload.get("content")
                if role == "developer":
                    close_model()
                    if not have_developer_items:
                        system_sections = list(provisional_sections)
                        have_developer_items = True
                    system_sections.extend(content_sections(content, "developer instructions"))
                    record_snapshot(idx, ts, system_sections)
                    if ts is not None:
                        last_input_ts = ts if last_input_ts is None else max(last_input_ts, ts)
                elif role == "user":
                    close_model()
                    text = text_of(content)
                    if seen_turn and idx >= activity_start:
                        add_prompt(idx, ts, text)
                    elif not sub and text:
                        _item(ctx, idx, ts, "attach", "session_context", len(text))
                    if ts is not None:
                        last_input_ts = ts if last_input_ts is None else max(last_input_ts, ts)
                elif role == "assistant":
                    call = ensure_model(idx, ts)
                    n = len(text_of(content))
                    if n:
                        call["text_chars"] += n
                        call["blocks"].append((ts, "text"))
                        if not sub:
                            _item(ctx, idx, ts, "assistant", None, n)
                    if payload.get("phase") == "final":
                        call["stop"] = "end_turn"
            elif ptype == "reasoning":
                call = ensure_model(idx, ts)
                call["blocks"].append((ts, "thinking"))
            elif ptype in ("function_call", "custom_tool_call", "tool_search_call", "web_search_call"):
                start_tool(idx, ts, payload, ptype)
            elif ptype in ("function_call_output", "custom_tool_call_output", "tool_search_output"):
                close_model()
                finish_tool(idx, ts, payload)
            elif ptype == "agent_message":
                # Multi-agent handoff/replay input, not assistant model output.
                close_model()
                n = _codex_chars(payload.get("content"))
                if not sub and n:
                    _item(ctx, idx, ts, "custom", "agent message", n)
                if ts is not None:
                    last_input_ts = ts

        elif kind == "token_usage_record":
            usage = payload.get("usage")
            if idx >= activity_start and isinstance(usage, dict) and int(usage.get("output_tokens") or 0) > 0:
                target = finish_model(usage, usage_idx=idx, provisional=True)
                fast_model = target

        elif kind == "event_msg":
            etype = payload.get("type")
            if etype == "user_message":
                close_model()
                if idx >= activity_start:
                    add_prompt(idx, ts, payload.get("message") or "", source="event")
            elif etype == "token_count":
                info = payload.get("info") if isinstance(payload.get("info"), dict) else {}
                usage = info.get("last_token_usage")
                total = info.get("total_token_usage")
                total_sig = usage_sig(total)
                changed = total_sig is not None and total_sig != last_total_sig
                if total_sig is not None:
                    last_total_sig = total_sig
                window = info.get("model_context_window")
                fast_same_request = fast_model is not None and not any(
                    entry.get("type") in ("turn_context", "compacted")
                    or (entry.get("type") == "event_msg" and
                        (entry.get("payload") or {}).get("type") in
                        ("user_message", "task_started", "turn_aborted"))
                    or (entry.get("type") == "response_item" and
                        (entry.get("payload") or {}).get("role") in ("user", "developer"))
                    for entry in entries[fast_model["idx"] + 1:idx]
                    if isinstance(entry, dict))
                if idx >= activity_start and changed and isinstance(usage, dict) \
                        and int(usage.get("output_tokens") or 0) > 0 and fast_same_request:
                    merge_fast_tail(window)
                    if fast_model is not None:
                        # The delayed counter is authoritative, including corrections
                        # to an earlier provisional value for this same request.
                        fast_model["usage"] = usage
                        fast_model["_fast_pending"] = False
                        fast_model.pop("_fast_prior_stop", None)
                    last_usage_idx = max(last_usage_idx, idx)
                    fast_model = None
                elif idx >= activity_start and changed and isinstance(usage, dict) and int(usage.get("output_tokens") or 0) > 0:
                    before = last_model
                    finish_model(usage, window, usage_idx=idx)
                    if last_model is before and last_model is not None \
                            and usage_sig(last_model.get("usage")) == usage_sig(usage) and window:
                        last_model["context_window"] = int(window)
                elif last_model is not None and window:
                    last_model["context_window"] = int(window)
            elif etype == "agent_message":
                text = payload.get("message") or ""
                if isinstance(text, str):
                    event_agent = (idx, ts, text)
            elif etype == "agent_reasoning":
                event_reasoning_ts = ts
            elif etype == "item_completed":
                mark_item_failure(payload)
                detail = _codex_tool_detail(payload.get("item"), ts) if idx >= activity_start else None
                if detail and detail["id"] not in detail_ids:
                    detail_ids.add(detail["id"])
                    ctx["tool_details"].append({**detail, "sub": int(sub), "stream": stream})
            elif etype == "turn_aborted":
                label = clean_prompt(str(payload.get("message") or payload.get("reason") or "interrupted"), 160)
                ctx["interrupts"].append({"k": "x", "t0": ts, "t1": ts, "sub": int(sub), "l": label})
                if pending_model is not None:
                    pending_model["stop"] = "interrupted"
            elif etype == "error":
                label = clean_prompt(str(payload.get("message") or payload.get("reason") or etype), 160)
                ctx["errors"].append({"k": "e", "t0": ts, "t1": ts, "sub": int(sub), "l": label})
                if pending_model is not None:
                    pending_model["err"] = 1
                    pending_model["stop"] = "error"
            elif etype == "web_search_end":
                pass
            elif etype in ("exec_command_end", "patch_apply_end", "mcp_tool_call_end",
                           "dynamic_tool_call_response"):
                call_id = payload.get("call_id")
                tc = tools_by_id.get(call_id)
                if tc is not None:
                    code = payload.get("exit_code")
                    failed = bool(payload.get("error") or (code not in (None, 0)))
                    if etype == "patch_apply_end":
                        failed = failed or payload.get("success") is False
                    elif etype == "dynamic_tool_call_response":
                        failed = failed or payload.get("success") is False
                    elif etype == "mcp_tool_call_end":
                        result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
                        ok = result.get("Ok") if isinstance(result.get("Ok"), dict) else {}
                        failed = failed or "Err" in result or bool(ok.get("isError"))
                    tc["err"] = int(bool(tc["err"] or failed))

        elif kind == "compacted":
            close_model()
            ctx["compact_events"].append({"t0": ts, "t1": ts, "idx": idx, "pre": None,
                                          "label": "compaction (encrypted summary)"})
            if not sub:
                _item(ctx, idx, ts, "compaction", None, 0)
            if ts is not None:
                last_input_ts = ts

        if ts is not None:
            last_any_ts = ts if last_any_ts is None else max(last_any_ts, ts)

    if pending_model is not None:
        unassigned_models.append(pending_model)
    for call in ctx["models"]:
        if call.pop("_fast_pending", False):
            call["_unconfirmed_usage"] = call.get("usage")
            call["usage"] = None
            call["stop"] = call.pop("_fast_prior_stop", call.get("stop"))
    ctx["open_tools"].extend(pending_tools.values())


def _usage_numbers(call):
    u = call.get("usage") or {}
    if call.get("fmt") == "codex":
        total_in = int(u.get("input_tokens") or 0)
        cr = min(total_in, int(u.get("cached_input_tokens") or 0))
        cw = min(max(0, total_in - cr), int(u.get("cache_write_input_tokens") or 0))
        inp = max(0, total_in - cr - cw)
        out = int(u.get("output_tokens") or 0)
        think = u.get("reasoning_output_tokens")
        think_reported = isinstance(think, (int, float))
        think = int(think) if think_reported else 0
        return inp, cw, cr, out, max(0, min(out, think)), think_reported, 0
    if call.get("fmt") == "pi":
        inp = int(u.get("input") or 0)
        cw = int(u.get("cacheWrite") or 0)
        cr = int(u.get("cacheRead") or 0)
        out = int(u.get("output") or 0)
        think = u.get("reasoning")
        think_reported = isinstance(think, (int, float))
        think = int(think) if think_reported else 0
        cw1h = int(u.get("cacheWrite1h") or 0)
        return inp, cw, cr, out, max(0, min(out, think)), think_reported, cw1h
    inp = int(u.get("input_tokens") or 0)
    cc = int(u.get("cache_creation_input_tokens") or 0)
    cr = int(u.get("cache_read_input_tokens") or 0)
    out = int(u.get("output_tokens") or 0)
    details = u.get("output_tokens_details") or {}
    think = details.get("thinking_tokens")
    think_reported = isinstance(think, (int, float))
    if think_reported:
        think = int(think)
    else:
        total_chars = call["think_chars"] + call["text_chars"]
        think = int(round(out * call["think_chars"] / total_chars)) if total_chars else 0
    cc1h = int(((u.get("cache_creation") or {}).get("ephemeral_1h_input_tokens")) or 0)
    return inp, cc, cr, out, max(0, min(out, think)), think_reported, cc1h


# --------------------------------------------------------------------------- phases

def _phase_split(models):
    """Assign each model call (prefill, reasoning, generation) durations.

    With per-block timestamps (Claude Code): decode speed is observed on visible text
    that follows a thinking block, then separates prefill from thinking decode.
    Without them (pi): a least-squares fit latency ~ a + b*computed + c*output over the
    session's requests estimates prefill; decode is the remainder, split by tokens.
    Returns (decode tok/s or None, method).
    """
    vis_tok = vis_dur = 0.0
    timed = [c for c in models if c["_think_end"] is not None]
    for c in timed:
        if c["t1"] > c["_think_end"] + 0.05 and c["_out"] > c["_think"]:
            vis_tok += c["_out"] - c["_think"]
            vis_dur += c["t1"] - c["_think_end"]
    tg = vis_tok / vis_dur if (vis_dur >= 2.0 and vis_tok >= 50) else None
    method = "blocks" if tg else None

    if not tg:
        fit = _fit_latency(models)
        if fit:
            a, b, c_ = fit
            for c in models:
                lat = max(0.0, c["t1"] - c["t0"])
                prefill = min(lat, max(0.0, a + b * (c["_in"] + c["_cc"])))
                dec = lat - prefill
                share = (c["_think"] / c["_out"]) if c["_out"] else 0.0
                c["ph"] = (prefill, dec * share, dec * (1 - share))
            return (1.0 / c_ if c_ > 0 else None), "regression"
        for c in models:
            lat = max(0.0, c["t1"] - c["t0"])
            think_end = c["_think_end"]
            if think_end is not None:
                first = min(lat, max(0.0, think_end - c["t0"]))
                c["ph"] = (0.0, first, max(0.0, lat - first))
            else:
                share = (c["_think"] / c["_out"]) if c["_out"] else 0.0
                c["ph"] = (0.0, lat * share, lat * (1 - share))
        return None, "none"

    for c in models:
        lat = max(0.0, c["t1"] - c["t0"])
        think_end = c["_think_end"]
        if think_end is not None:
            first = min(lat, max(0.0, think_end - c["t0"]))
            tokens = c["_think"] if c["_think"] > 0 else max(0, c["_out"] - c["text_chars"] // 4)
            think_dec = min(first, tokens / tg)
            c["ph"] = (first - think_dec, think_dec, max(0.0, lat - first))
        else:
            dec = min(lat, c["_out"] / tg)
            c["ph"] = (lat - dec, 0.0, dec)
    return tg, method


def _fit_latency(models):
    """Least squares for latency = a + b*computed_in + c*out, coefficients clamped >= 0."""
    rows = [(1.0, float(c["_in"] + c["_cc"]), float(c["_out"]), max(0.0, c["t1"] - c["t0"]))
            for c in models if c["t1"] > c["t0"] and c["_out"] > 0]
    if len(rows) < 8:
        return None

    def solve(cols):
        n = len(cols)
        A = [[sum(r[i] * r[j] for r in rows) for j in cols] for i in cols]
        y = [sum(r[i] * r[3] for r in rows) for i in cols]
        for i in range(n):  # gaussian elimination with partial pivoting
            p = max(range(i, n), key=lambda k: abs(A[k][i]))
            if abs(A[p][i]) < 1e-12:
                return None
            A[i], A[p] = A[p], A[i]
            y[i], y[p] = y[p], y[i]
            for k in range(i + 1, n):
                f = A[k][i] / A[i][i]
                for j in range(i, n):
                    A[k][j] -= f * A[i][j]
                y[k] -= f * y[i]
        x = [0.0] * n
        for i in range(n - 1, -1, -1):
            x[i] = (y[i] - sum(A[i][j] * x[j] for j in range(i + 1, n))) / A[i][i]
        return dict(zip(cols, x))

    sol = solve([0, 1, 2])
    if sol is None:
        return None
    if sol[2] <= 0:
        return None
    if sol[1] < 0:
        sol = solve([0, 2]) or {}
        sol[1] = 0.0
    if sol.get(0, 0) < 0:
        sol[0] = 0.0
    return sol.get(0, 0.0), sol.get(1, 0.0), sol[2]


def _partition(intervals, t_start, t_end):
    """Additive wall-clock split. intervals: (a, b, kind, priority)."""
    events = []
    for a, b, kind, pr in intervals:
        if a is None or b is None or b <= a:
            continue
        events.append((max(a, t_start), 1, pr, kind))
        events.append((min(b, t_end), -1, pr, kind))
    events.sort(key=lambda x: (x[0], x[1]))
    active = {}
    totals = defaultdict(float)
    idle = []
    cur = t_start
    for t, d, pr, kind in events:
        if t > cur:
            if active:
                totals[max(active)[1]] += t - cur
            else:
                idle.append([cur, t])
                totals["idle"] += t - cur
            cur = t
        key = (pr, kind)
        active[key] = active.get(key, 0) + d
        if active[key] <= 0:
            del active[key]
    if t_end > cur:
        idle.append([cur, t_end])
        totals["idle"] += t_end - cur
    return totals, idle


def auto_window(models, override):
    if override:
        return int(override)
    for call in reversed(models):
        reported = call.get("context_window")
        if isinstance(reported, (int, float)) and reported > 0:
            return int(reported)
    mx = 0
    for c in models:
        mx = max(mx, c["_in"] + c["_cc"] + c["_cr"])
        if "[1m]" in (c.get("model") or ""):
            return LARGE_WINDOW
    return LARGE_WINDOW if mx > DEFAULT_WINDOW else DEFAULT_WINDOW


# --------------------------------------------------------------------------- ledger

ATTACH_LABELS = {
    "skill_listing": "skills listing", "mcp_instructions_delta": "MCP instructions",
    "deferred_tools_delta": "deferred tool names", "agent_listing_delta": "agent listing",
    "environment": "environment", "session_context": "session context", "task_reminder": "task reminders",
    "total_tokens_reminder": "budget reminders", "batching_reminder_sent": "batching reminders",
    "silent_turn_reminder": "silent-turn reminders", "queued_command": "queued commands",
    "remote_session_change": "remote session notes", "inlined_image_paths": "image path notes",
}
GROUP_LABELS = {
    "system": "system prompt", "residual": "tool schemas & unmeasured", "attach": "injected reminders",
    "thinking": "re-sent thinking (this turn)", "tool_schema": "tool schemas loaded on demand",
    "skill": "loaded skills", "prompt": "user prompts", "image": "images", "assistant": "assistant text",
    "tool_input": "tool inputs", "tool_result": "tool results", "compaction": "compaction summaries",
    "bash_user": "user shell output", "custom": "extension messages",
}


def _label_for(group, sub):
    if group == "attach":
        return ATTACH_LABELS.get(sub, (sub or "attachment").replace("_", " "))
    if group in ("tool_result", "tool_input", "image", "skill", "custom", "tool_schema") and sub:
        return f"{GROUP_LABELS[group]} · {sub}"
    return GROUP_LABELS.get(group, group)


def build_ledger(ctx, models, price, window, base, request_prices=None):
    """Reconstruct the context window at every main-agent request and cost it out."""
    main = sorted([c for c in models if not c["sub"]], key=lambda c: c["idx"])
    items = sorted(ctx["items"], key=lambda it: it["i"])
    snaps = sorted(ctx["snapshots"], key=lambda s: s["idx"])
    comp_idx = sorted(set([b["idx"] for b in ctx["compact_boundaries"]] +
                          [s["idx"] for s in ctx["compact_summaries"]] +
                          [c["idx"] for c in ctx["compact_events"]]))
    if not main:
        return None

    def measured(c):
        return c["_in"] + c["_cc"] + c["_cr"]

    # ---- the tool block: when a system-prompt change rebuilds the cache, what stays cached
    # is the prefix before the system prompt, i.e. the tool definitions
    def is_rebuild(prev, cur):
        pm = measured(prev)
        return cur["_cr"] < 0.85 * pm and (cur["_in"] + cur["_cc"]) > max(2_000, 0.25 * pm)

    def bounded_int_groups(groups, limit):
        """Largest-remainder rounding that cannot exceed the measured integer window."""
        values = [(key, value) for key, value in groups.items() if value >= 1]
        rounded = {key: int(math.floor(value)) for key, value in values}
        target = min(int(limit), int(round(sum(value for _, value in values))))
        extra = max(0, target - sum(rounded.values()))
        for key, _ in sorted(values, key=lambda kv: kv[1] - math.floor(kv[1]), reverse=True)[:extra]:
            rounded[key] += 1
        return rounded

    hint = 0.0
    snap_at = {}
    for c in main:
        snap_at[c["idx"]] = next((s["hash"] for s in reversed(snaps) if s["idx"] < c["idx"]), None)
    for k in range(1, len(main)):
        prev, cur = main[k - 1], main[k]
        if snap_at[cur["idx"]] != snap_at[prev["idx"]] and is_rebuild(prev, cur) and cur["_cr"] > 1000 \
                and not any(prev["idx"] < ci < cur["idx"] for ci in comp_idx):
            hint = max(hint, float(cur["_cr"]))
    reconciled = 0

    # ---- calibrate characters per token on requests served from a warm cache. Two rates:
    # tool traffic (shell output, JSON, code) and prose (prompts, listings, assistant text).
    TOOLISH = ("tool_result", "tool_input", "tool_schema", "bash_user")
    pairs = []
    for k in range(1, len(main)):
        prev, cur = main[k - 1], main[k]
        if cur["_cr"] < 0.9 * measured(prev) or (cur["_in"] + cur["_cc"]) < 200:
            continue
        if any(prev["idx"] < ci < cur["idx"] for ci in comp_idx):
            continue
        new = [it for it in items if prev["idx"] < it["i"] < cur["idx"]]
        if any(it["g"] == "image" for it in new):
            continue  # image token costs are themselves estimates; keep them out of the fit
        exact = sum(it["img"] for it in new)  # re-sent thinking carries an exact token count
        ct = sum(it["ch"] for it in new if it["g"] in TOOLISH)
        cp = sum(it["ch"] for it in new if it["g"] not in TOOLISH)
        toks = cur["_in"] + cur["_cc"] - exact
        if ct + cp >= 800 and toks > 50:
            pairs.append((ct, cp, toks))
    # the first request is nearly all prose (system prompt, listings, the prompt); when the
    # tool block is known from cache retention (below), it measures the prose rate directly
    prose_prior = None
    if hint and main:
        first = main[0]
        first_items = [it for it in items if it["i"] < first["idx"]]
        first_snap = next((s for s in reversed(snaps) if s["idx"] < first["idx"]), None)
        pchars = sum(it["ch"] for it in first_items if it["g"] not in TOOLISH) + \
            (sum(n for _, n in first_snap["sections"]) if first_snap else 0)
        tchars = sum(it["ch"] for it in first_items if it["g"] in TOOLISH)
        exact = sum(it["img"] for it in first_items)
        left = measured(first) - hint - exact - tchars / DEFAULT_CPT
        if pchars > 5000 and left > 500 and tchars < 0.2 * pchars:
            prose_prior = min(6.0, max(2.0, pchars / left))
    cpt_tool, cpt_prose = _fit_cpt(pairs, prose_prior)
    calibrated = bool(pairs)
    cpt = cpt_tool

    def tok(it):
        return it["img"] + it["ch"] / (cpt_tool if it["g"] in TOOLISH else cpt_prose)

    # Without a cited price there is no defensible economic cache weight; use raw tokens.
    # Per-request rates keep category dollars reconciled when the model changes or an
    # OpenAI request crosses the documented long-context threshold.
    request_prices = request_prices or {}
    priced_main = [request_prices.get(id(c), (price, 1.0))[0] for c in main]
    read_multipliers = [p[2] for p in priced_main if p]
    read_mult = read_multipliers[0] if read_multipliers \
        and all(x == read_multipliers[0] for x in read_multipliers) else 1.0


    # ---- walk the requests
    cum = defaultdict(lambda: {"computed": 0.0, "cached": 0.0, "present": 0, "cost": 0.0, "peak": 0.0})
    series = []
    events = []
    prev = None
    prev_snap = None
    last_detail = None
    HISTORY_K = 20   # content that entered the window more than this many requests ago
    hist_tokens = hist_cost = 0.0
    hist_now = 0.0
    for k, c in enumerate(main):
        snap = None
        for s in snaps:
            if s["idx"] < c["idx"]:
                snap = s
        last_comp = max([ci for ci in comp_idx if ci < c["idx"]], default=-1)
        last_prompt = max([it["i"] for it in items if it["g"] == "prompt" and it["i"] < c["idx"]], default=-1)
        present = [it for it in items if (last_comp < it["i"] < c["idx"] or (it["g"] == "compaction" and last_comp <= it["i"] < c["idx"]))
                   and (not it.get("transient") or it["i"] > last_prompt)]
        sys_tokens = sum(n for _, n in snap["sections"]) / cpt_prose if snap else 0.0
        est_raw = sys_tokens + sum(tok(it) for it in present)
        meas = measured(c)
        # a rebuild caused by a system-prompt change leaves exactly the tool block cached:
        # that retained size is a floor for what the estimates cannot see
        floor = hint if hint and meas > hint else 0.0
        f = min(1.0, (meas - floor) / est_raw) if est_raw > 0 else 1.0
        if f < 1.0:
            reconciled += 1
        est = est_raw * f
        residual = max(0.0, meas - est)

        # composition by key
        comp = defaultdict(float)
        detail = defaultdict(lambda: defaultdict(float))
        if snap:
            comp["system"] += sys_tokens * f
            for label, n in snap["sections"]:
                detail["system"][label] += n / cpt_prose * f
        comp["residual"] += residual
        for it in present:
            key = it["g"]
            comp[key] += tok(it) * f
            detail[key][it["s"] or ""] += tok(it) * f

        # cost attribution: new content is computed first, the rest is served from cache
        computed_budget = float(c["_in"] + c["_cc"])
        new_ids = set()
        if prev is None:
            new_ids = {id(it) for it in present}
            sys_new = True
            res_new = True
        else:
            new_ids = {id(it) for it in present if it["i"] > prev["idx"]}
            sys_new = bool(snap and prev_snap and snap["hash"] != prev_snap["hash"]) or (snap is not None and prev_snap is None)
            res_new = False
        parts = []  # (key, sub, tokens, is_new)
        if snap:
            for label, n in snap["sections"]:
                parts.append(("system", label, n / cpt_prose * f, sys_new))
        parts.append(("residual", "", residual, res_new))
        for it in present:
            parts.append((it["g"], it["s"] or "", tok(it) * f, id(it) in new_ids))
        new_total = sum(p[2] for p in parts if p[3])
        old_total = sum(p[2] for p in parts if not p[3])
        if computed_budget >= new_total:
            new_share, old_share = 1.0, ((computed_budget - new_total) / old_total if old_total > 0 else 0.0)
        else:
            new_share, old_share = (computed_budget / new_total if new_total > 0 else 0.0), 0.0
        old_share = min(1.0, old_share)
        # actual $ for this request's input, split by computed vs cached at the API's own prices
        call_price, input_mult = request_prices.get(id(c), (price, 1.0))
        call_p_in = call_price[0] * input_mult / 1e6 if call_price else 0.0
        call_read_mult = call_price[2] if call_price else 1.0
        cc1h = c["_cc1h"]
        cc5m = max(0, c["_cc"] - cc1h)
        write_cost = (c["_in"] + cc5m * (call_price[3] if call_price else 1.25)
                      + cc1h * (call_price[4] if call_price else 2.0)) * call_p_in
        read_cost = c["_cr"] * call_read_mult * call_p_in
        comp_rate = write_cost / computed_budget if computed_budget > 0 else 0.0
        cache_rate = read_cost / c["_cr"] if c["_cr"] > 0 else 0.0
        for key, sub_, t, is_new in parts:
            share = new_share if is_new else old_share
            comp_part = t * share
            cache_part = max(0.0, t - comp_part)
            ck = (key, sub_)
            d = cum[ck]
            d["computed"] += comp_part
            d["cached"] += cache_part
            d["present"] += 1 if t > 0 else 0
            d["cost"] += comp_part * comp_rate + cache_part * cache_rate
            d["peak"] = max(d["peak"], t)

        # cache rebuilds
        if prev is not None:
            prev_meas = measured(prev)
            recomputed = c["_in"] + c["_cc"]
            if is_rebuild(prev, c):
                cause = "unknown (prefix changed: tool set, system prompt, or history edit)"
                if any(prev["idx"] < ci < c["idx"] for ci in comp_idx):
                    cause = "compaction (expected)"
                elif sys_new and snap and prev_snap:
                    changed = []
                    a = dict(prev_snap["sections"])
                    for label, n in snap["sections"]:
                        if a.get(label) != n:
                            changed.append(f"{label} ({n - a.get(label, 0):+d} chars)")
                    for label in a:
                        if label not in dict(snap["sections"]):
                            changed.append(f"{label} (removed)")
                    cause = "system prompt changed: " + ("; ".join(changed[:3]) if changed else "sections differ")
                else:
                    gap = c["t0"] - prev["t1"]
                    ttl = 3600.0 if prev["_cc1h"] > 0 else 300.0
                    if gap > ttl:
                        cause = f"cache expired after {fmt_duration(gap)} idle (TTL {fmt_duration(ttl)})"
                    elif (c.get("model") or "") != (prev.get("model") or ""):
                        cause = f"model changed to {c.get('model')}"
                extra = recomputed * (comp_rate - cache_rate) if call_price else None
                events.append({"t": round(c["t0"] - base, 3), "recomputed": int(recomputed),
                               "expected": int(prev_meas), "cause": cause,
                               "extra_cost": extra, "k": k})
        # re-sent history: conversation content older than HISTORY_K requests, still in the window
        cutoff = main[k - HISTORY_K]["idx"] if k >= HISTORY_K else -1
        old_tok = sum(tok(it) * f for it in present if it["i"] < cutoff and it["g"] not in ("compaction",))
        hist_now = old_tok
        hist_tokens += old_tok
        hist_cost += old_tok * (cache_rate if cache_rate else comp_rate)
        series.append({"t": round(c["t0"] - base, 3), "m": int(meas), "in": c["_in"], "cc": c["_cc"],
                       "cr": c["_cr"], "old": int(round(old_tok)),
                       "g": bounded_int_groups(comp, meas)})
        last_detail = (c, comp, detail, present, snap, meas)
        prev, prev_snap = c, (snap or prev_snap)

    # ---- table for the latest request
    c, comp, detail, present, snap, meas = last_detail
    now_rows = []
    for key in sorted(comp, key=lambda k: -comp[k]):
        subs = sorted(detail.get(key, {}).items(), key=lambda kv: -kv[1])
        now_rows.append({"key": key, "label": GROUP_LABELS.get(key, key), "tokens": int(round(comp[key])),
                         "pct": (comp[key] / window) if window else None,
                         "subs": [{"label": (s or "(unnamed)") if key != "system" else s, "tokens": int(round(v))}
                                  for s, v in subs[:12] if v >= 1]})

    # ---- cumulative table (finer keys)
    cum_rows = []
    for (key, sub_), d in cum.items():
        cum_rows.append({"key": key, "sub": sub_, "label": _label_for(key, sub_) if key != "system" else f"system prompt · {sub_}",
                         "computed": int(round(d["computed"])), "cached": int(round(d["cached"])),
                         "present": d["present"], "cost": d["cost"] if price else None, "peak": int(round(d["peak"]))})
    # collapse system sections and reminders into one row each, keep the big categories per sub
    def collapse(rows, key, label):
        grp = [r for r in rows if r["key"] == key]
        if len(grp) <= 1:
            return rows
        merged = {"key": key, "sub": "", "label": label, "computed": sum(r["computed"] for r in grp),
                  "cached": sum(r["cached"] for r in grp), "present": max(r["present"] for r in grp),
                  "cost": (sum(r["cost"] for r in grp) if price else None), "peak": sum(r["peak"] for r in grp),
                  "subs": sorted([{"label": r["sub"] or r["label"], "computed": r["computed"], "cached": r["cached"],
                                   "cost": r["cost"], "peak": r["peak"]} for r in grp],
                                 key=lambda r: -(r["computed"] + 0.1 * r["cached"]))[:12]}
        return [r for r in rows if r["key"] != key] + [merged]
    for key in sorted({r["key"] for r in cum_rows}):
        cum_rows = collapse(cum_rows, key, "injected reminders & listings" if key == "attach" else GROUP_LABELS.get(key, key))
    for r in cum_rows:
        if not r.get("subs"):
            r["label"] = GROUP_LABELS.get(r["key"], r["key"]) if r["key"] != "attach" else "injected reminders & listings"
            if r["key"] in ("tool_result", "tool_input", "image", "skill", "custom", "tool_schema") and r.get("sub"):
                r["label"] = f"{GROUP_LABELS[r['key']]} · {r['sub']}"
    cum_rows.sort(key=lambda r: -(r["computed"] + read_mult * r["cached"]))
    total_computed = sum(r["computed"] for r in cum_rows)
    total_cached = sum(r["cached"] for r in cum_rows)

    return {
        "cpt": round(cpt_tool, 2), "cpt_prose": round(cpt_prose, 2), "calibrated": calibrated, "window": window,
        "tool_block_hint": int(hint), "reconciled": reconciled,
        "history": {"k": HISTORY_K, "now": int(round(hist_now)), "now_share": (hist_now / meas) if meas else None,
                    "resent_tokens": int(round(hist_tokens)), "cost": hist_cost if price else None},
        "read_mult": read_mult, "series": series, "events": events,
        "now": {"k": len(main) - 1, "measured": int(meas), "estimated": int(round(sum(comp.values()) - comp["residual"])),
                "rows": now_rows},
        "cum": {"rows": cum_rows, "computed": int(total_computed), "cached": int(total_cached),
                "requests": len(main)},
        "tools": _tool_loading(ctx, main, cpt, read_mult, base),
    }


PROSE_CPT = 3.5   # prior for prompts, listings and assistant text (current Claude tokenizers)


def _fit_cpt(pairs, prose_prior=None):
    """Characters per token for tool traffic and for prose, from warm-cache request pairs.

    tokens_k = a * tool_chars_k + b * prose_chars_k. Prose is usually a small share of the
    new content, so b is only fitted when prose carries enough weight; otherwise it stays
    at the prior (measured on the first request when possible) and a is fitted alone.
    """
    prose = prose_prior or PROSE_CPT
    if not pairs:
        return DEFAULT_CPT, prose
    tot_t = sum(ct for ct, _, _ in pairs)
    tot_p = sum(cp for _, cp, _ in pairs)
    b = 1.0 / prose
    if prose_prior is None and tot_p >= 30_000 and tot_p >= 0.25 * (tot_t + tot_p):
        sxx = sum(ct * ct for ct, _, _ in pairs)
        syy = sum(cp * cp for _, cp, _ in pairs)
        sxy = sum(ct * cp for ct, cp, _ in pairs)
        sxt = sum(ct * t for ct, _, t in pairs)
        syt = sum(cp * t for _, cp, t in pairs)
        det = sxx * syy - sxy * sxy
        if det > 1e-9:
            bb = (syt * sxx - sxt * sxy) / det
            if bb > 0:
                b = min(1 / 2.8, max(1 / 6.0, bb))
    num = sum(ct * (t - b * cp) for ct, cp, t in pairs)
    den = sum(ct * ct for ct, _, _ in pairs)
    a = num / den if den > 0 else 1.0 / DEFAULT_CPT
    cpt_tool = min(6.5, max(1.6, 1.0 / a)) if a > 0 else DEFAULT_CPT
    return cpt_tool, 1.0 / b


def _tool_loading(ctx, main, cpt, read_mult, base):
    """Deferred (ToolSearch) vs direct tool loading, from what the transcript shows."""
    if not ctx["deferred_listing"] and not ctx["tool_records"]:
        return None
    R = len(main)
    listing = ctx["deferred_listing"]
    listing_tokens = (listing[-1]["ch"] / cpt) if listing else 0.0
    deferred_names = set()
    for d in listing:
        deferred_names.update(d["added"])
        deferred_names.difference_update(d["removed"])
    seen = {}
    for rec in sorted(ctx["tool_records"], key=lambda r: r["idx"]):
        if rec["name"] not in seen:
            seen[rec["name"]] = rec
    searches = [t for t in ctx["tools"] if t["n"] in ("ToolSearch", "tool_search") and not t["sub"]]
    by_idx = {c["idx"]: k for k, c in enumerate(main)}

    def request_index_after(idx):
        return sum(1 for c in main if c["idx"] > idx)

    def request_of(idx):
        ks = [k for k, c in enumerate(main) if c["idx"] <= idx]
        return ks[-1] if ks else 0

    loaded = []
    per_tool_listing = listing_tokens / len(deferred_names) if deferred_names else 0.0
    for name, rec in seen.items():
        schema = rec["ch"] / cpt
        after = request_index_after(rec["idx"])
        k = request_of(rec["idx"])
        # the search that loaded it: the nearest preceding ToolSearch call
        srch = max([t for t in searches if t["idx"] <= rec["idx"]], key=lambda t: t["idx"], default=None)
        overhead_tok = overhead_s = 0.0
        solo = False
        if srch is not None:
            req = main[request_of(srch["idx"])]
            solo = all(t in ("ToolSearch", "tool_search") for t in req["tools"]) \
                and req["stop"] == "tool_use"
            if solo:  # the whole request existed only to fetch schemas: one extra round trip
                k_req = request_of(srch["idx"])
                nxt = main[k_req + 1] if k_req + 1 < len(main) else None
                overhead_tok = req["_out"] + ((nxt["_in"] + nxt["_cc"]) if nxt else 0)
                overhead_s = max(0.0, req["t1"] - req["t0"]) + max(0.0, (srch["t1"] or srch["t0"]) - srch["t0"])
            else:     # batched with real work: only the search block and its result were extra
                overhead_tok = 40 + srch["ch"] / cpt
                overhead_s = max(0.0, (srch["t1"] or srch["t0"]) - srch["t0"])
        deferred_eff = per_tool_listing * (1 + read_mult * (R - 1)) + schema * (1 + read_mult * max(0, after - 1))
        direct_eff = schema * (1 + read_mult * (R - 1))
        loaded.append({"name": name, "schema": int(round(schema)), "loaded_at": k + 1, "requests_after": after,
                       "overhead_tokens": int(overhead_tok), "overhead_s": round(overhead_s, 1), "solo": solo,
                       "deferred_eff": int(round(deferred_eff + overhead_tok)), "direct_eff": int(round(direct_eff)),
                       "t": round(rec["ts"] - base, 3) if rec["ts"] else None})
    loaded.sort(key=lambda d: d["loaded_at"])
    n_unloaded = max(0, len(deferred_names) - len(seen))
    avg_schema = (sum(d["schema"] for d in loaded) / len(loaded)) if loaded else 600.0
    unloaded_direct = n_unloaded * avg_schema * (1 + read_mult * (R - 1))
    unloaded_deferred = per_tool_listing * n_unloaded * (1 + read_mult * (R - 1))
    return {
        "requests": R, "listing_tokens": int(round(listing_tokens)),
        "catalog_known": bool(ctx["deferred_listing"]), "deferred_count": len(deferred_names),
        "loaded": loaded, "searches": len(searches), "avg_schema": int(round(avg_schema)),
        "unloaded_count": n_unloaded, "unloaded_direct_eff": int(round(unloaded_direct)),
        "unloaded_deferred_eff": int(round(unloaded_deferred)),
        "loaded_deferred_eff": int(sum(d["deferred_eff"] for d in loaded)),
        "loaded_direct_eff": int(sum(d["direct_eff"] for d in loaded)),
    }


# --------------------------------------------------------------------------- analysis

def analyze(main_entries, sub_streams=(), *, title=None, context_window=None, live=False,
            source="", now=None, poll_ms=2500, fmt=None, price=None):
    now = now or time.time()
    fmt = fmt or detect_format(main_entries, source)
    scanners = {"claude": _scan_claude, "codex": _scan_codex, "pi": _scan_pi}
    if fmt not in scanners:
        raise SystemExit(f"unknown transcript format: {fmt}")
    scan = scanners[fmt]
    ctx = _new_ctx()
    scan(main_entries, False, ctx, "main")
    for name, entries in sub_streams:
        scan(entries, True, ctx, name)

    # Timestamped output is observed activity even if an interrupted request never
    # receives confirmed usage. Retain it in static, live and fleet timelines alike.
    models = [c for c in ctx["models"] if c["t1"] is not None]
    for c in models:
        if c["t0"] is None or c["t0"] > c["t1"]:
            c["t0"] = c["t1"]
        c["_in"], c["_cc"], c["_cr"], c["_out"], c["_think"], c["_think_rep"], c["_cc1h"] = _usage_numbers(c)
        tks = [b[0] for b in c["blocks"] if b[1] == "thinking" and b[0] is not None]
        c["_think_end"] = max(tks) if tks else None
    # Missing/provisional Codex usage must not replace the last measured context window
    # or enter token, cost and ledger accounting, whether or not the session is live.
    metered_models = [c for c in models if isinstance(c.get("usage"), dict)] \
        if fmt == "codex" else models
    for c in models:
        if not c["sub"] and c["_think"] > 0:
            _item(ctx, c["idx"], c["t1"], "thinking", None, 0, c["_think"], {"transient": True})
    tools = [t for t in ctx["tools"] if t["t0"] is not None]
    prompts = sorted((p for p in ctx["prompts"] if p["t0"] is not None), key=lambda p: p["t0"])
    interrupts = [x for x in ctx["interrupts"] if x["t0"] is not None]
    errors = [x for x in ctx["errors"] if x["t0"] is not None]

    stamps = [c["t0"] for c in models] + [c["t1"] for c in models] + [p["t0"] for p in prompts] + \
             [t["t0"] for t in tools] + [t["t1"] for t in tools if t["t1"] is not None] + \
             [x["t0"] for x in interrupts + errors] + \
             [d["completed_at"] for d in ctx["tool_details"]]
    if ctx.get("active_start_ts") is not None:
        stamps.append(ctx["active_start_ts"])
    else:
        for e in main_entries:
            if isinstance(e, dict):
                ts = parse_ts(e.get("timestamp"))
                if ts is not None:
                    stamps.append(ts)
                    break
    if not stamps:
        raise SystemExit("no timestamped entries found; is this a Claude Code, Codex CLI or pi transcript?")
    t_start = min(stamps)
    t_end = max(stamps)
    if live:
        t_end = max(t_end, now)

    for t in tools:
        if t["t1"] is None:
            t["open"] = 1
            t["t1"] = t_end if live else t["t0"]

    compactions = []
    used = set()
    for b in ctx["compact_boundaries"]:
        t0 = b["prev"] if b["prev"] is not None else b["ts"]
        t1 = b["ts"]
        for i, s in enumerate(ctx["compact_summaries"]):
            if s["stream"] == b["stream"] and s["idx"] > b["idx"] and s["idx"] - b["idx"] <= 12 and s["ts"]:
                t1 = max(t1 or s["ts"], s["ts"])
                used.add(i)
                break
        if t0 is None or t1 is None:
            continue
        label = (b.get("trigger") or "compaction")
        if b.get("pre"):
            label += f" · {fmt_tokens(b['pre'])} tokens before"
        compactions.append({"k": "c", "t0": min(t0, t1), "t1": max(t0, t1), "l": label, "sub": 0})
    for i, s in enumerate(ctx["compact_summaries"]):
        if i in used or s["ts"] is None:
            continue
        prev = max([t for t in stamps if t < s["ts"]] or [s["ts"]])
        compactions.append({"k": "c", "t0": prev, "t1": s["ts"], "l": "compaction", "sub": 0})
    for ce in ctx["compact_events"]:
        if ce["t1"] is None:
            continue
        t0 = ce["t0"] if ce["t0"] is not None else ce["t1"]
        compactions.append({"k": "c", "t0": min(t0, ce["t1"]), "t1": ce["t1"], "l": ce["label"], "sub": 0})
    compactions.sort(key=lambda c: c["t0"])

    tg, phase_method = _phase_split(metered_models)
    unmetered_models = [c for c in models if fmt == "codex" and not isinstance(c.get("usage"), dict)]
    if unmetered_models:
        # With no token counts, fall back to observed block boundaries. Applying a
        # fitted decode rate to placeholder zero tokens would invent all-prefill time.
        _phase_split(unmetered_models)

    intervals = []
    for c in models:
        pf, rs, gn = c["ph"]
        pr = 6 if not c["sub"] else 5
        a = c["t0"]
        intervals.append((a, a + pf, "prefill", pr))
        intervals.append((a + pf, a + pf + rs, "reasoning", pr))
        intervals.append((a + pf + rs, c["t1"], "generation", pr))
    for c in compactions:
        intervals.append((c["t0"], c["t1"], "compaction", 4))
    for t in tools:
        intervals.append((t["t0"], t["t1"], "tools", 3))
    totals, idle_spans = _partition(intervals, t_start, t_end)

    prompt_ts = sorted(p["t0"] for p in prompts) + sorted(x["t0"] for x in interrupts)
    idle_out = []
    idle_wait = idle_over = 0.0
    for a, b in idle_spans:
        waiting = any(abs(b - pt) <= 1.0 for pt in prompt_ts) or (live and b >= t_end - 0.001)
        if waiting:
            idle_wait += b - a
        else:
            idle_over += b - a
        if b - a >= 0.5:
            idle_out.append([a, b, "w" if waiting else "o"])

    laps = []
    for i, p in enumerate(prompts):
        t1 = prompts[i + 1]["t0"] if i + 1 < len(prompts) else t_end
        n_calls = sum(1 for c in models if not c["sub"] and p["t0"] <= c["t0"] < t1)
        n_tools = sum(1 for t in tools if not t["sub"] and p["t0"] <= t["t0"] < t1)
        laps.append({"n": i + 1, "t0": p["t0"], "t1": max(t1, p["t0"]), "calls": n_calls, "tools": n_tools, "l": p["l"]})
    lap_of = lambda t: next((lp["n"] for lp in reversed(laps) if lp["t0"] <= t), None)

    tok_computed = sum(c["_in"] + c["_cc"] for c in metered_models)
    tok_cache_write = sum(c["_cc"] for c in metered_models)
    tok_cached = sum(c["_cr"] for c in metered_models)
    tok_out = sum(c["_out"] for c in metered_models)
    tok_think = sum(c["_think"] for c in metered_models)
    think_rep = [c["_think_rep"] for c in metered_models]
    tok_tool_est = int(round((sum(t.get("ch") or 0 for t in tools)
                              if fmt == "codex" else ctx["tool_chars"]) / 4.0))

    decode_time = totals["reasoning"] + totals["generation"]
    prefill_time = totals["prefill"]
    main_models = [c for c in metered_models if not c["sub"]]
    last = max(main_models, key=lambda c: c["t1"]) if main_models else None
    window = auto_window(metered_models or models, context_window)
    ctx_used = (last["_in"] + last["_cc"] + last["_cr"]) if last else 0
    model_names = sorted({c["model"] for c in models if c.get("model")})
    wall = max(0.0, t_end - t_start)

    # ---- cost
    primary_model = None
    if metered_models:
        counts = defaultdict(int)
        for c in metered_models:
            counts[c.get("model") or ""] += c["_in"] + c["_cc"] + c["_cr"] + c["_out"]
        primary_model = max(counts, key=counts.get)
    pricing = pricing_for(primary_model, price)
    per_call_pricing = [pricing_for(c.get("model"), price) for c in metered_models]
    if pricing and any(p is None for p in per_call_pricing):
        pricing = None
    cost = None
    ledger_request_prices = {}
    if pricing:
        p_in, p_out, rd, w5, w1 = pricing[:5]
        pricing_rows = []
        seen_prices = set()
        for pr_ in per_call_pricing:
            key = (pr_[7],) + tuple(pr_[:7])
            if key in seen_prices:
                continue
            seen_prices.add(key)
            pricing_rows.append({"model": pr_[7], "in": pr_[0], "out": pr_[1],
                                 "read_mult": pr_[2], "write_5m_mult": pr_[3],
                                 "write_1h_mult": pr_[4], "source": pr_[5],
                                 "source_url": pr_[6]})
        c_in = c_out = c_read = c_write = 0.0
        long_context_requests = 0
        for c, pr_ in zip(metered_models, per_call_pricing):
            pi_, po_, rd_, w5_, w1_ = pr_[:5]
            long_context = pr_[7] in OPENAI_LONG_CONTEXT_MODELS \
                and c["_in"] + c["_cc"] + c["_cr"] > OPENAI_LONG_CONTEXT_THRESHOLD
            in_mult = 2.0 if long_context else 1.0
            out_mult = 1.5 if long_context else 1.0
            ledger_request_prices[id(c)] = (pr_, in_mult)
            long_context_requests += int(long_context)
            cc1h = c["_cc1h"]
            cc5m = max(0, c["_cc"] - cc1h)
            c_in += c["_in"] * pi_ * in_mult / 1e6
            c_write += (cc5m * w5_ + cc1h * w1_) * pi_ * in_mult / 1e6
            c_read += c["_cr"] * rd_ * pi_ * in_mult / 1e6
            c_out += c["_out"] * po_ * out_mult / 1e6
        cost = {"total": c_in + c_out + c_read + c_write, "input": c_in, "output": c_out,
                "cache_read": c_read, "cache_write": c_write,
                "pricing": {"model": primary_model, "in": p_in, "out": p_out, "read_mult": rd,
                            "write_5m_mult": w5, "write_1h_mult": w1, "source": pricing[5],
                            "source_url": pricing[6], "rows": pricing_rows,
                            "long_context_requests": long_context_requests}}
    reported = ctx["reported_cost"] if fmt == "pi" and ctx["reported_cost"] else None
    if cost and pricing[5] == "--price":
        pricing_note = "Dollar cost uses the explicit --price override; it is an estimate, not an invoice."
    elif cost:
        source_note = pricing[5] if len(cost["pricing"]["rows"]) == 1 else \
            f"{len(cost['pricing']['rows'])} cited per-model price rows"
        pricing_note = (f"Dollar cost is an estimated Standard API list-price equivalent using {source_note}; "
                        "it is not an invoice.")
        if cost["pricing"]["long_context_requests"]:
            pricing_note += (f" The documented >{OPENAI_LONG_CONTEXT_THRESHOLD // 1000}K input surcharge was "
                             f"applied to {cost['pricing']['long_context_requests']} request(s).")
    elif fmt == "codex" and not metered_models:
        pricing_note = "No confirmed token usage yet; dollar cost omitted."
    else:
        names = ", ".join(model_names) or "this model"
        pricing_note = f"No cited public API price is configured for {names}; dollar cost omitted."

    if unmetered_models:
        pricing_note += (f" {len(unmetered_models)} observed Codex call(s) have no confirmed usage; "
                         "token and cost totals cover confirmed calls only. Their prefill/decode "
                         "split is not separable; throughput is unavailable with partial usage.")

    stats = {
        "tg_s": (tok_out / decode_time) if decode_time > 0 and not unmetered_models else None,
        "pp_s": (tok_computed / prefill_time) if prefill_time > 0 and not unmetered_models else None,
        "laps": len(laps),
        "avg_lap_s": (sum(lp["t1"] - lp["t0"] for lp in laps) / len(laps)) if laps else None,
        "time": {"wall": wall, "prefill": prefill_time, "reasoning": totals["reasoning"],
                 "generation": totals["generation"], "decode": decode_time, "tools": totals["tools"],
                 "compaction": totals["compaction"], "idle": totals["idle"], "idle_wait": idle_wait,
                 "idle_overhead": idle_over, "tools_sum": sum(t["t1"] - t["t0"] for t in tools)},
        "tokens": {"total": tok_computed + tok_cached + tok_out, "prompt_computed": tok_computed,
                   "prompt_cache_write": tok_cache_write, "prompt_cached": tok_cached,
                   "completion": tok_out, "reasoning": tok_think, "tool_results_est": tok_tool_est,
                   "cache_hit_ratio": (tok_cached / (tok_cached + tok_computed)) if (tok_cached + tok_computed) else None},
        "context": {"used": ctx_used, "window": window, "pct": (ctx_used / window) if window else None},
        "counts": {"calls": len(models) + len(tools) + len(compactions), "assistant": len(models),
                   "assistant_unmetered": len(unmetered_models),
                   "assistant_sub": sum(1 for c in models if c["sub"]), "tools": len(tools),
                   "tools_sub": sum(1 for t in tools if t["sub"]), "tool_errors": sum(t["err"] for t in tools),
                   "compactions": len(compactions), "interrupts": len([x for x in interrupts if x["k"] == "x"]),
                   "errors": len(errors), "prompts": len(prompts)},
        "cost": cost, "cost_reported": reported,
    }

    base = t_start
    rel = lambda t: round(t - base, 3)
    calls = []
    # Ledger rows follow confirmed main requests in file order, not all activity rows.
    ledger_indices = {id(c): k for k, c in enumerate(sorted(main_models, key=lambda c: c["idx"]))}
    for c in sorted(models, key=lambda c: c["t0"]):
        pf, rs, _ = c["ph"]
        calls.append({"k": "m", "t0": rel(c["t0"]), "t1": rel(c["t1"]),
                      "p": [rel(c["t0"] + pf), rel(c["t0"] + pf + rs)],
                      "tok": [c["_in"] + c["_cc"], c["_cr"], c["_out"], c["_think"]],
                      "lap": lap_of(c["t0"]), "sub": c["sub"], "err": c["err"], "stop": c["stop"],
                      "tools": c["tools"][:8], "m": c.get("model"),
                      "ledger_k": ledger_indices.get(id(c))})
    for t in sorted(tools, key=lambda t: t["t0"]):
        calls.append({"k": "t", "t0": rel(t["t0"]), "t1": rel(t["t1"]), "n": t["n"], "l": t["l"],
                      "lap": lap_of(t["t0"]), "sub": t["sub"], "err": t["err"], "ch": t["ch"], "open": t["open"]})
    for c in compactions:
        calls.append({"k": "c", "t0": rel(c["t0"]), "t1": rel(c["t1"]), "l": c["l"], "lap": lap_of(c["t0"])})
    for x in interrupts:
        calls.append({"k": x["k"], "t0": rel(x["t0"]), "t1": rel(x["t0"]), "l": x["l"], "lap": lap_of(x["t0"])})
    for x in errors:
        calls.append({"k": "e", "t0": rel(x["t0"]), "t1": rel(x["t0"]), "l": x["l"], "lap": lap_of(x["t0"])})

    per_tool = defaultdict(lambda: {"count": 0, "total_s": 0.0, "errors": 0})
    for t in tools:
        d = per_tool[t["n"]]
        d["count"] += 1
        d["total_s"] += t["t1"] - t["t0"]
        d["errors"] += t["err"]
    tools_out = [{"name": k, **v} for k, v in per_tool.items()]
    tools_out.sort(key=lambda d: (-d["count"], d["name"]))

    ledger = None
    try:
        ledger = build_ledger(ctx, metered_models, pricing, window, base,
                              request_prices=ledger_request_prices)
    except Exception as exc:  # the ledger is an add-on: never take the panel down with it
        ledger = {"error": f"{type(exc).__name__}: {exc}"}

    default_title = ctx["meta"].get("name") or (_title_from(prompts[0]["l"]) if prompts else (ctx["meta"].get("session_id") or "session"))
    meta = {
        "title": title or default_title,
        "source": source,
        "fmt": fmt,
        "agent": ctx["meta"].get("agent") or ADAPTER_META[fmt]["agent"],
        "agent_label": ctx["meta"].get("agent_label") or ADAPTER_META[fmt]["agent_label"],
        "cli_label": ctx["meta"].get("cli_label") or ADAPTER_META[fmt]["cli_label"],
        "session_id": ctx["meta"].get("session_id"),
        "cwd": ctx["meta"].get("cwd"),
        "git_branch": ctx["meta"].get("git_branch"),
        "cli_version": ctx["meta"].get("cli_version"),
        "models": model_names,
        "pricing_note": pricing_note,
        "generated_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
        "live": bool(live),
        "poll_ms": int(poll_ms),
        "subagent_streams": len(sub_streams),
        "estimates": {
            "decode_tok_s": tg,
            "phase_method": phase_method,
            "prefill": {"blocks": "estimated from the visible-text decode speed", "regression": "estimated by a latency fit",
                        "none": "not separable"}[phase_method],
            "reasoning_tokens": ("reported" if think_rep and all(think_rep) else
                                 "estimated" if not any(think_rep) else "mixed"),
        },
        "tokenograph": __version__,
    }
    return {"meta": meta, "base": base, "end": rel(t_end), "stats": stats, "calls": calls,
            "laps": [{**lp, "t0": rel(lp["t0"]), "t1": rel(lp["t1"])} for lp in laps],
            "tools": tools_out, "idle": [[rel(a), rel(b), w] for a, b, w in idle_out],
            "ledger": ledger,
            "tool_details": sorted(ctx["tool_details"], key=lambda d: d["completed_at"])}


# --------------------------------------------------------------------------- state (fleet)

STATE_ORDER = {"blocked": 0, "done": 1, "working": 2, "idle": 3, "unknown": 4}


def derive_state(payload, mtime, now=None):
    """herdr-style state from the transcript tail: working / blocked / done / idle."""
    now = now or time.time()
    age = now - mtime
    if age > 6 * 3600:
        return "idle"
    calls = payload["calls"]
    open_tools = [c for c in calls if c["k"] == "t" and c.get("open")]
    models = [c for c in calls if c["k"] == "m"]
    laps = payload["laps"]
    if open_tools:
        return "blocked" if age > 20 else "working"
    last_model = max(models, key=lambda c: c["t1"]) if models else None
    last_lap = laps[-1] if laps else None
    if last_lap and (not last_model or last_model["t0"] < last_lap["t0"]):
        return "working" if age < 600 else "idle"
    if last_model and last_model.get("stop") in ("end_turn", "stop"):
        return "done" if age < 1800 else "idle"
    if age < 120:
        return "working"
    return "idle"


# --------------------------------------------------------------------------- herdr

def herdr_config_dir() -> Path:
    if os.environ.get("XDG_CONFIG_HOME"):
        return Path(os.environ["XDG_CONFIG_HOME"]) / "herdr"
    return Path.home() / ".config" / "herdr"


def herdr_socket_paths():
    out = []
    env = os.environ.get("HERDR_SOCKET_PATH")
    if env:
        out.append(Path(env))
    root = herdr_config_dir()
    out.append(root / "herdr.sock")
    out.extend(Path(p) for p in glob.glob(str(root / "sessions" / "*" / "herdr.sock")))
    seen, uniq = set(), []
    for p in out:
        if p not in seen and p.exists():
            seen.add(p)
            uniq.append(p)
    return uniq


def herdr_request(sock_path, method, params=None, timeout=1.5):
    """One call on herdr's newline-delimited JSON socket API."""
    if not hasattr(socket, "AF_UNIX"):
        return None
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect(str(sock_path))
        s.sendall((json.dumps({"id": "tokenograph-1", "method": method, "params": params or {}}) + "\n").encode())
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        line = buf.split(b"\n", 1)[0]
        return json.loads(line) if line else None
    except (OSError, ValueError):
        return None
    finally:
        s.close()


def herdr_agents():
    """Agents known to running herdr servers, keyed by agent session id / path and by cwd."""
    by_session, by_cwd, servers = {}, defaultdict(list), []
    for sp in herdr_socket_paths():
        resp = herdr_request(sp, "agent.list")
        if not resp or "result" not in resp:
            continue
        res = resp["result"]
        agents = res.get("agents") if isinstance(res, dict) else res
        if not isinstance(agents, list):
            continue
        servers.append(str(sp))
        for a in agents:
            if not isinstance(a, dict):
                continue
            rec = {"status": a.get("agent_status") or "unknown", "agent": a.get("display_agent") or a.get("agent"),
                   "pane_id": a.get("pane_id"), "tab_id": a.get("tab_id"), "workspace_id": a.get("workspace_id"),
                   "name": a.get("name") or a.get("title"), "cwd": a.get("cwd") or a.get("foreground_cwd"),
                   "server": sp.parent.name if sp.parent.name != "herdr" else "default"}
            sess = a.get("agent_session") or {}
            if isinstance(sess, dict) and sess.get("value"):
                by_session[str(sess["value"])] = rec
            if rec["cwd"]:
                by_cwd[rec["cwd"]].append(rec)
    return {"by_session": by_session, "by_cwd": by_cwd, "servers": servers}


def fleet_payload(limit=30, price=None, context_window=None, now=None, cache=None):
    now = now or time.time()
    sessions = iter_sessions()[:limit]
    herdr = herdr_agents()
    rows = []
    for s in sessions:
        key = str(s["path"])
        entry = cache.get(key) if cache is not None else None
        if entry is None or entry["mtime"] != s["mtime"]:
            reader = TranscriptReader(s["path"])
            reader.refresh()
            try:
                p = analyze(reader.entries, [], source=key, fmt=s["fmt"], price=price,
                            context_window=context_window, live=False, now=now)
            except SystemExit:
                continue
            entry = {"mtime": s["mtime"], "payload": p}
            if cache is not None:
                cache[key] = entry
        p = entry["payload"]
        st = p["stats"]
        h = herdr["by_session"].get(p["meta"].get("session_id") or "") or herdr["by_session"].get(key)
        if h is None and p["meta"].get("cwd") and len(herdr["by_cwd"].get(p["meta"]["cwd"], [])) == 1:
            h = herdr["by_cwd"][p["meta"]["cwd"]][0]
        state = derive_state(p, s["mtime"], now)
        rows.append({
            "path": key, "fmt": s["fmt"], "session_id": p["meta"].get("session_id") or s["session_id"],
            "agent": p["meta"].get("agent"), "agent_label": p["meta"].get("agent_label"),
            "title": p["meta"]["title"], "cwd": p["meta"].get("cwd"), "branch": p["meta"].get("git_branch"),
            "models": p["meta"].get("models"), "state": state, "herdr": h,
            "last_activity": s["mtime"], "age_s": now - s["mtime"], "wall_s": st["time"]["wall"],
            "laps": st["laps"], "calls": st["counts"]["calls"], "tools": st["counts"]["tools"],
            "computed": st["tokens"]["prompt_computed"], "cached": st["tokens"]["prompt_cached"],
            "completion": st["tokens"]["completion"], "context_pct": st["context"]["pct"],
            "context_used": st["context"]["used"], "cost": (st["cost"] or {}).get("total") if st.get("cost") else None,
            "cost_reported": st.get("cost_reported"), "idle_wait_s": st["time"]["idle_wait"],
            "blocked_hint": next((c["n"] for c in p["calls"] if c["k"] == "t" and c.get("open")), None),
        })
    rows.sort(key=lambda r: (STATE_ORDER.get(r["herdr"]["status"] if r["herdr"] else r["state"], 9), -r["last_activity"]))
    return {"generated_at": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(timespec="seconds"),
            "now": now, "rows": rows, "herdr_servers": herdr["servers"], "tokenograph": __version__,
            "sources": [
                {"fmt": "claude", "label": ADAPTER_META["claude"]["agent_label"], "path": str(projects_dir())},
                {"fmt": "codex", "label": ADAPTER_META["codex"]["agent_label"], "path": str(codex_sessions_dir())},
                {"fmt": "pi", "label": ADAPTER_META["pi"]["agent_label"], "path": str(pi_sessions_dir())},
            ]}


# --------------------------------------------------------------------------- rendering

def _inline(template_path, payload, fragment=False, title=None):
    if not template_path.is_file():
        raise SystemExit(f"template not found: {template_path}")
    tpl = template_path.read_text(encoding="utf-8")
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
    title = title or "tokenograph"
    safe = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    page = tpl.replace(DATA_MARKER, data).replace("<title>tokenograph</title>", f"<title>{safe}</title>", 1)
    if not fragment:
        return page
    head = re.search(r"<!--head-->(.*?)<!--/head-->", page, re.S)
    body = re.search(r"<!--body-->(.*?)<!--/body-->", page, re.S)
    return (head.group(1) if head else "") + (body.group(1) if body else "")


def render_html(payload, fragment=False):
    return _inline(TEMPLATE_PATH, payload, fragment, (payload.get("meta") or {}).get("title"))


def render_fleet_html(payload, fragment=False):
    return _inline(FLEET_TEMPLATE_PATH, payload, fragment, "tokenograph fleet")


def load_streams(session_path: Path, include_subagents: bool):
    reader = TranscriptReader(session_path)
    reader.refresh()
    subs = []
    if include_subagents and detect_format(reader.entries, session_path) == "claude":
        for f in subagent_files(session_path):
            r = TranscriptReader(f)
            r.refresh()
            subs.append((f.stem, r))
    return reader, subs


def build_payload(reader, subs, args, live=False, now=None):
    return analyze(reader.entries, [(name, r.entries) for name, r in subs],
                   title=args.title, context_window=args.context_window, live=live,
                   source=str(reader.path), now=now, poll_ms=int(getattr(args, "interval", 2.5) * 1000),
                   fmt=(None if getattr(args, "format", "auto") == "auto" else args.format),
                   price=parse_price(getattr(args, "price", None)))


# --------------------------------------------------------------------------- commands

def cmd_list(args):
    sessions = iter_sessions()
    if not sessions:
        print(f"no sessions under {projects_dir()}, {codex_sessions_dir()} or {pi_sessions_dir()}")
        return 1
    print(f"{len(sessions)} session(s), newest first\n")
    for s in sessions[: args.n]:
        when = dt.datetime.fromtimestamp(s["mtime"]).strftime("%Y-%m-%d %H:%M")
        size = f"{s['size'] / 1e6:5.1f} MB"
        print(f"  {when}  {size}  {s['fmt']:<6}  {s['session_id'][:8]}  {s['project']:<28.28}  {first_prompt(s['path'])}")
    print("\nuse a session id prefix, 'latest', or a path with build / serve / json")
    return 0


def cmd_json(args):
    path = resolve_session(args.session)
    reader, subs = load_streams(path, not args.no_subagents)
    payload = build_payload(reader, subs, args)
    text = json.dumps(payload, indent=1 if args.pretty else None, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def cmd_build(args):
    path = resolve_session(args.session)
    reader, subs = load_streams(path, not args.no_subagents)
    payload = build_payload(reader, subs, args)
    out = Path(args.output)
    out.write_text(render_html(payload, fragment=args.fragment), encoding="utf-8")
    st = payload["stats"]
    cost = f" · ~${st['cost']['total']:.2f}" if st.get("cost") else ""
    print(f"wrote {out}  ({out.stat().st_size / 1e3:.0f} kB)  {payload['meta']['agent']} · "
          f"wall {fmt_duration(st['time']['wall'])} · {st['counts']['assistant']} assistant calls · "
          f"{st['counts']['tools']} tool calls · {st['laps']} laps{cost}")
    return 0


class _LiveState:
    def __init__(self, reader, subs, args):
        self.reader, self.subs, self.args = reader, subs, args
        self.lock = threading.Lock()
        self.payload = None
        self.last = 0.0

    def snapshot(self):
        with self.lock:
            now = time.time()
            if self.payload is None or now - self.last >= 1.0:
                changed = self.reader.refresh()
                for _, r in self.subs:
                    changed = r.refresh() or changed
                if self.payload is None or changed or now - self.last >= 10.0:
                    self.payload = build_payload(self.reader, self.subs, self.args, live=True, now=now)
                self.last = now
            return self.payload


def _serve(handler_fn, host, port, banner, open_browser):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            route = self.path.split("?", 1)[0]
            out = handler_fn(route)
            if out is None:
                self.send_response(404)
                self.end_headers()
                return
            body, ctype = out
            body = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *a):
            pass

    server = http.server.ThreadingHTTPServer((host, port), Handler)
    url = f"http://{host}:{port}/"
    print(f"{banner}\n          live at {url}  (Ctrl-C to stop)")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print()
    finally:
        server.server_close()


def cmd_serve(args):
    path = resolve_session(args.session)
    reader, subs = load_streams(path, not args.no_subagents)
    state = _LiveState(reader, subs, args)
    state.snapshot()

    def handle(route):
        if route in ("/", "/index.html"):
            return render_html(state.snapshot()), "text/html; charset=utf-8"
        if route == "/data.json":
            return json.dumps(state.snapshot(), separators=(",", ":")), "application/json"
        return None

    _serve(handle, args.host, args.port, f"tokenograph: {path}", args.open)
    return 0


def cmd_fleet(args):
    price = parse_price(args.price)
    if not args.serve:
        payload = fleet_payload(args.limit, price, args.context_window)
        out = Path(args.output)
        out.write_text(render_fleet_html(payload), encoding="utf-8")
        print(f"wrote {out}: {len(payload['rows'])} sessions" +
              (f" · herdr: {', '.join(payload['herdr_servers'])}" if payload["herdr_servers"] else " · herdr: not reachable"))
        return 0
    cache = {}
    lock = threading.Lock()
    holder = {"payload": None, "at": 0.0, "sessions": {}}

    def fleet():
        with lock:
            now = time.time()
            if holder["payload"] is None or now - holder["at"] >= 3.0:
                holder["payload"] = fleet_payload(args.limit, price, args.context_window, now, cache)
                holder["payload"]["live"] = True
                holder["payload"]["poll_ms"] = 5000
                holder["at"] = now
            return holder["payload"]

    def session_state(i):
        rows = fleet()["rows"]
        if i < 0 or i >= len(rows):
            return None
        path = rows[i]["path"]
        st = holder["sessions"].get(path)
        if st is None:
            ns = argparse.Namespace(title=None, context_window=args.context_window, interval=2.5,
                                    format=rows[i]["fmt"], price=args.price)
            reader, subs = load_streams(Path(path), True)
            st = _LiveState(reader, subs, ns)
            holder["sessions"][path] = st
        return st

    def handle(route):
        if route in ("/", "/index.html"):
            return render_fleet_html(fleet()), "text/html; charset=utf-8"
        if route == "/fleet.json":
            return json.dumps(fleet(), separators=(",", ":")), "application/json"
        m = re.match(r"^/s/(\d+)/(index\.html|data\.json)?$", route)
        if m:
            st = session_state(int(m.group(1)))
            if st is None:
                return None
            if m.group(2) == "data.json":
                return json.dumps(st.snapshot(), separators=(",", ":")), "application/json"
            return render_html(st.snapshot()), "text/html; charset=utf-8"
        return None

    _serve(handle, args.host, args.port, "tokenograph fleet", args.open)
    return 0



# --------------------------------------------------------------------------- graph export

def build_graph(payload):
    """The session as a property graph (node-link form).

    Nodes: session, laps, requests, tool calls, compactions, cache rebuilds, context
    categories. Edges carry a kind and a weight in tokens or seconds:
      session -has_lap-> lap -next-> lap
      lap -contains-> request -follows-> request         (weight: tokens reused from cache)
      request -invokes-> tool -feeds-> request           (the result enters the next request)
      request -compacted_into-> compaction -resumes-> request
      category -present_in-> request                     (weight: tokens in that request's window)
      rebuild -hits-> request                            (weight: tokens recomputed)
    Loads with networkx.node_link_graph(data, edges="links"), Gephi (GraphML) or Neo4j (CSV).
    """
    meta, calls, laps = payload["meta"], payload["calls"], payload["laps"]
    base = payload["base"]
    nodes, links = [], []

    def node(nid, kind, **attrs):
        nodes.append({"id": nid, "kind": kind, **attrs})
        return nid

    def link(a, b, kind, **attrs):
        links.append({"source": a, "target": b, "kind": kind, **attrs})

    sid = node("session", "session", title=meta.get("title"), agent=meta.get("agent"),
               session_id=meta.get("session_id"), cwd=meta.get("cwd"), wall_s=payload["stats"]["time"]["wall"])
    lap_ids = []
    for lp in laps:
        lid = node(f"lap:{lp['n']}", "lap", n=lp["n"], t0=lp["t0"], t1=lp["t1"], prompt=lp["l"], calls=lp["calls"])
        link(sid, lid, "has_lap")
        if lap_ids:
            link(lap_ids[-1], lid, "next", weight=lp["t0"] - laps[lp["n"] - 2]["t0"])
        lap_ids.append(lid)

    models = sorted([c for c in calls if c["k"] == "m" and not c.get("sub")], key=lambda c: c["t0"])
    req_ids = []
    for k, c in enumerate(models):
        rid = node(f"request:{k + 1}", "request", n=k + 1, t0=c["t0"], t1=c["t1"], latency_s=round(c["t1"] - c["t0"], 3),
                   prefill_s=round(c["p"][0] - c["t0"], 3), reasoning_s=round(c["p"][1] - c["p"][0], 3),
                   generation_s=round(c["t1"] - c["p"][1], 3), computed=c["tok"][0], cached=c["tok"][1],
                   output=c["tok"][2], thinking=c["tok"][3], stop=c.get("stop"), model=c.get("m"))
        if c.get("lap"):
            link(f"lap:{c['lap']}", rid, "contains")
        if req_ids:
            link(req_ids[-1], rid, "follows", weight=c["tok"][1])
        req_ids.append(rid)

    def request_at(t):
        best = None
        for k, c in enumerate(models):
            if c["t0"] <= t:
                best = k
            else:
                break
        return best

    tools = sorted([c for c in calls if c["k"] == "t" and not c.get("sub")], key=lambda c: c["t0"])
    for i, t in enumerate(tools):
        tid = node(f"tool:{i + 1}", "tool", name=t["n"], t0=t["t0"], t1=t["t1"], duration_s=round(t["t1"] - t["t0"], 3),
                   error=bool(t.get("err")), label=t.get("l"), result_chars=t.get("ch", 0))
        k = request_at(t["t0"])
        if k is not None:
            link(req_ids[k], tid, "invokes")
            if k + 1 < len(req_ids):
                link(tid, req_ids[k + 1], "feeds", weight=round((t.get("ch") or 0) / 4))
    comps = sorted([c for c in calls if c["k"] == "c"], key=lambda c: c["t0"])
    for i, c in enumerate(comps):
        cid = node(f"compaction:{i + 1}", "compaction", t0=c["t0"], t1=c["t1"], label=c.get("l"))
        k = request_at(c["t0"])
        if k is not None:
            link(req_ids[k], cid, "compacted_into")
            if k + 1 < len(req_ids):
                link(cid, req_ids[k + 1], "resumes")

    ledger = payload.get("ledger") or {}
    if ledger and not ledger.get("error"):
        # Older payloads lack ledger_k and retain their positional mapping. New ones
        # explicitly exclude unmetered activity and preserve the ledger's file order.
        ledger_req_ids = {c.get("ledger_k", k): req_ids[k] for k, c in enumerate(models)
                          if c.get("ledger_k", k) is not None}
        cats = set()
        for k, s in enumerate(ledger.get("series", [])):
            if k not in ledger_req_ids:
                continue
            for cat, tokens in s["g"].items():
                if cat not in cats:
                    node(f"category:{cat}", "category", label=GROUP_LABELS.get(cat, cat))
                    cats.add(cat)
                link(f"category:{cat}", ledger_req_ids[k], "present_in", weight=tokens)
        for i, e in enumerate(ledger.get("events", [])):
            eid = node(f"rebuild:{i + 1}", "cache_rebuild", t=e["t"], recomputed=e["recomputed"], cause=e["cause"],
                       extra_cost=e.get("extra_cost"))
            if e["k"] in ledger_req_ids:
                link(eid, ledger_req_ids[e["k"]], "hits", weight=e["recomputed"])
    return {"directed": True, "multigraph": False, "graph": {"session": meta.get("session_id"), "base": base,
            "tokenograph": __version__}, "nodes": nodes, "links": links}


def write_graph(graph, path):
    """node-link JSON, GraphML, or a CSV pair (<stem>.nodes.csv / <stem>.edges.csv) by extension."""
    path = Path(path)
    ext = path.suffix.lower()
    if ext == ".graphml":
        import xml.etree.ElementTree as ET
        NS = "http://graphml.graphdrawing.org/xmlns"
        root = ET.Element("graphml", xmlns=NS)
        keys = {}

        def key_for(scope, name, value):
            kid = f"{scope[0]}_{name}"
            if kid not in keys:
                typ = "boolean" if isinstance(value, bool) else "long" if isinstance(value, int) else \
                    "double" if isinstance(value, float) else "string"
                el = ET.SubElement(root, "key", id=kid)
                el.set("for", scope)
                el.set("attr.name", name)
                el.set("attr.type", typ)
                keys[kid] = typ
            return kid

        g = ET.SubElement(root, "graph", id="session", edgedefault="directed")
        for n in graph["nodes"]:
            el = ET.SubElement(g, "node", id=n["id"])
            for a, v in n.items():
                if a == "id" or v is None:
                    continue
                d = ET.SubElement(el, "data", key=key_for("node", a, v))
                d.text = str(v).lower() if isinstance(v, bool) else str(v)
        for i, e in enumerate(graph["links"]):
            el = ET.SubElement(g, "edge", id=f"e{i}", source=e["source"], target=e["target"])
            for a, v in e.items():
                if a in ("source", "target") or v is None:
                    continue
                d = ET.SubElement(el, "data", key=key_for("edge", a, v))
                d.text = str(v)
        # keys must precede the graph element
        root[:] = [c for c in root if c.tag == "key"] + [g]
        ET.ElementTree(root).write(str(path), encoding="utf-8", xml_declaration=True)
        return [path]
    if ext == ".csv":
        import csv
        stem = path.with_suffix("")
        npath, epath = Path(f"{stem}.nodes.csv"), Path(f"{stem}.edges.csv")
        ncols = sorted({a for n in graph["nodes"] for a in n}, key=lambda a: (a != "id", a != "kind", a))
        with open(npath, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ncols)
            w.writeheader()
            for n in graph["nodes"]:
                w.writerow(n)
        ecols = sorted({a for e in graph["links"] for a in e}, key=lambda a: (a != "source", a != "target", a != "kind", a))
        with open(epath, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=ecols)
            w.writeheader()
            for e in graph["links"]:
                w.writerow(e)
        return [npath, epath]
    path.write_text(json.dumps(graph, indent=1, ensure_ascii=False), encoding="utf-8")
    return [path]


def cmd_graph(args):
    path = resolve_session(args.session)
    reader, subs = load_streams(path, not args.no_subagents)
    payload = build_payload(reader, subs, args)
    graph = build_graph(payload)
    written = write_graph(graph, args.output)
    print(f"wrote {', '.join(str(p) for p in written)}: {len(graph['nodes'])} nodes, {len(graph['links'])} edges")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tokenograph", description="telemetry and token accounting for coding-agent sessions")
    ap.add_argument("--version", action="version", version=f"tokenograph {__version__}")
    sub = ap.add_subparsers(dest="cmd")
    sub.required = True

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("session", help="transcript path, session id prefix, directory, or 'latest'")
    common.add_argument("--title", help="panel title (default: the first prompt, or the adapter's session name)")
    common.add_argument("--context-window", type=int, default=None,
                        help="context window in tokens for the fill ring (default: auto, 200k or 1M)")
    common.add_argument("--no-subagents", action="store_true", help="ignore subagent transcripts")
    common.add_argument("--format", choices=["auto", "claude", "codex", "pi"], default="auto")
    common.add_argument("--price", help="IN,OUT[,READ_MULT,WRITE5M_MULT,WRITE1H_MULT] in $/M tokens; default: built-in table")

    p = sub.add_parser("list", help="list Claude Code, Codex CLI and pi sessions, newest first")
    p.add_argument("-n", type=int, default=20, help="how many to show")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("build", parents=[common], help="write a self-contained HTML panel")
    p.add_argument("-o", "--output", default="tokenograph.html")
    p.add_argument("--fragment", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(fn=cmd_build, interval=2.5)

    p = sub.add_parser("serve", parents=[common], help="serve a live panel that follows the transcript")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--interval", type=float, default=2.5, help="browser poll interval in seconds")
    p.add_argument("--open", action="store_true", help="open the browser")
    p.set_defaults(fn=cmd_serve)

    p = sub.add_parser("json", parents=[common], help="dump the computed panel data as JSON")
    p.add_argument("-o", "--output")
    p.add_argument("--pretty", action="store_true")
    p.set_defaults(fn=cmd_json, interval=2.5)

    p = sub.add_parser("graph", parents=[common], help="export the session as a property graph")
    p.add_argument("-o", "--output", default="session.json", help=".json (node-link), .graphml, or .csv (nodes + edges)")
    p.set_defaults(fn=cmd_graph, interval=2.5)

    p = sub.add_parser("fleet", help="every session with herdr-style states, tokens and cost")
    p.add_argument("--serve", action="store_true", help="live page instead of a static file")
    p.add_argument("-o", "--output", default="fleet.html")
    p.add_argument("--limit", type=int, default=30, help="most recent sessions to include")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8788)
    p.add_argument("--open", action="store_true")
    p.add_argument("--context-window", type=int, default=None)
    p.add_argument("--price", default=None)
    p.set_defaults(fn=cmd_fleet)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
