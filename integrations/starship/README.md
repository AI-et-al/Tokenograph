# Tokenograph in Starship

An opt-in summary at your working shell prompt. It shows the selected model/session,
confirmed context and session tokens, cache-hit ratio, estimated or recorded cost,
tool/error counts, the last recorded tool name, and usage age. `partial usage` means
some observed calls lack confirmed counters; `usage pending` is not measured zero.
`stale` means the collector has not checked its source in more than ten seconds.

1. Add the `[custom.tokenograph]` block from `starship.toml` to your existing
   `~/.config/starship.toml`, replacing `/path/to/Tokenograph` with the checkout path.
   In your top-level `format`, insert `${custom.tokenograph}` immediately before
   `$character`. Keep your other modules and theme. The summary uses Moonfly accents.
2. Source the shell helpers in `~/.zshrc` (use your actual checkout path):

   ```zsh
   source /path/to/Tokenograph/integrations/starship/tokenograph.zsh
   ```

3. In an interactive terminal, select a session:

   ```zsh
   tg-on SESSION_ID_OR_TRANSCRIPT_PATH
   # Or: tg-on latest   # newest across all harnesses, pinned once at startup
   tg-off               # stop this shell's collector and hide its summary
   ```

`tg-on` activates Starship **in that shell**, including when Powerlevel10k was loaded
last. `tg-off` removes the Tokenograph summary; Starship remains active until the shell
closes. Your normal startup configuration applies in other/new terminals. Sourcing the
helper alone starts nothing. Noninteractive/background invocations of `tg-on` are
rejected. No Codex/Claude commands or subagent launch hooks are replaced.

Each enabled shell gets its own private cache and collector. It checks its pinned
transcript once per second, analyzing only when the file changes. Starship reads only
the small cache when drawing the next prompt: it does **not** repeatedly parse sessions,
make model calls, or repaint continuously while you type or while an agent owns the
terminal. The continuously updating Vesper strip is a separate frontend. The collector
stops with `tg-off` or when its parent shell exits. `TOKENOGRAPH_PYTHON` overrides Python.

The cache contains aggregate counters and bounded model/tool names, not commands,
arguments, prompt text, or the transcript path. Invalid/missing caches produce no prompt
output. Estimated prices retain `~`; unknown model prices remain unavailable.

For other shells, use the example Starship module plus the standard-library cache CLI:
`python3 -m tokenograph.prompt prepare SESSION --cache PATH` resolves a session and
prints its absolute transcript path; `watch PATH --cache PATH --parent-pid PID` keeps it
updated, and `show --cache PATH` only reads it. Manage that collector with your shell's
own lifecycle hooks. The cache's parent directory must already exist.
