# Optional Vesper terminal viewer

This is the read-only terminal frontend developed with the local Vesper companion.
It displays the selected Codex task's public responses and activity above a Moonfly
Tokenograph strip. It needs an **already running, compatible Vesper companion**;
the voice backend, credentials, audio, and personal settings are not part of Tokenograph.
Node.js 20+ is required for this optional viewer; Tokenograph itself remains Python
standard library only.

```sh
export VESPER_DATA_DIR="/absolute/path/to/vesper-companion/data"
node integrations/vesper-viewer/watch.mjs
```

The directory must contain the companion's generated `launch.json`. Its loopback URL
and local cookie authenticate read-only `/api/state` and `/api/events` requests. The
viewer never prints the cookie, sends a prompt, takes task ownership, or starts a voice
session. Keep the data directory outside the repository. No API key is needed here.
`TOKENOGRAPH_DIR` and `TOKENOGRAPH_PYTHON` can override the checkout and Python binary.

The strip refreshes visually every 250 ms. A local collector checks the selected
transcript each second and recomputes on changes. Usage only changes when reported;
clocks can continue independently. Cost marked `~` is a dated API list-price estimate,
not a subscription invoice. Inner tool completion names are shown when recorded; starts
and per-tool token attribution are not invented. The monitor makes no model calls.

Controls: Ctrl+O changes **this viewer's strip detail**; Page Up/Down scrolls the captured
history; End returns to live; Ctrl+C closes only the viewer. This is not the native Codex
TUI and has no keyboard prompt or private reasoning display. Speak or type in Vesper.
Changing Vesper's selected task requires reopening the viewer; it never silently follows
another task. `--plain` uses scrolling text, and `--check` tests connectivity for six
seconds without opening the fullscreen display.

```sh
node --test integrations/vesper-viewer/tests/*.test.mjs
VESPER_DATA_DIR="/absolute/path/to/vesper-companion/data" \
  node integrations/vesper-viewer/watch.mjs --check
```

These files are a portable copy of the working companion frontend. The companion's
installed copy continues to run independently; publishing this integration does not
restart or replace an active voice connection.
