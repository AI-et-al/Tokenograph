# Source this file from .zshrc. It defines commands but never starts a collector.
typeset -g _TOKENOGRAPH_INTEGRATION=${${(%):-%x}:A:h}
# A new interactive child shell must opt in independently, even if it inherited a cache.
[[ -n ${_TOKENOGRAPH_COLLECTOR_PID:-} ]] || unset TOKENOGRAPH_PROMPT_CACHE

tg-off() {
  if [[ -n ${_TOKENOGRAPH_COLLECTOR_PID:-} ]]; then
    kill -TERM -- "$_TOKENOGRAPH_COLLECTOR_PID" 2>/dev/null
    wait "$_TOKENOGRAPH_COLLECTOR_PID" 2>/dev/null
    unset _TOKENOGRAPH_COLLECTOR_PID
  fi
  if [[ -n ${_TOKENOGRAPH_CACHE_DIR:-} ]]; then
    command rm -f -- "$_TOKENOGRAPH_CACHE_DIR/summary.json"
    command rmdir -- "$_TOKENOGRAPH_CACHE_DIR" 2>/dev/null
    unset _TOKENOGRAPH_CACHE_DIR
  fi
  unset TOKENOGRAPH_PROMPT_CACHE
  return 0
}

tg-on() {
  if [[ ! -o interactive || ! -t 0 || ! -t 1 ]]; then
    print -u2 'tg-on is for an interactive terminal; no collector started.'
    return 1
  fi
  if (( $# != 1 )); then
    print -u2 'Usage: tg-on SESSION_ID_OR_PATH (or latest, across all harnesses)'
    return 1
  fi
  (( $+commands[starship] )) || { print -u2 'Starship is not installed.'; return 1; }
  tg-off
  local tg_cache_dir
  tg_cache_dir=$(mktemp -d "${TMPDIR:-/tmp}/tokenograph-prompt.XXXXXXXX") || return 1
  typeset -g _TOKENOGRAPH_CACHE_DIR=$tg_cache_dir
  export TOKENOGRAPH_PROMPT_CACHE="$_TOKENOGRAPH_CACHE_DIR/summary.json"
  local tg_source
  tg_source=$("$_TOKENOGRAPH_INTEGRATION/prompt.sh" prepare "$1" --cache "$TOKENOGRAPH_PROMPT_CACHE") || { tg-off; return 1; }
  "$_TOKENOGRAPH_INTEGRATION/prompt.sh" watch "$tg_source" --cache "$TOKENOGRAPH_PROMPT_CACHE" --parent-pid $$ </dev/null >/dev/null 2>&1 &
  typeset -g _TOKENOGRAPH_COLLECTOR_PID=$!
  # Activate Starship only here, including shells that loaded another prompt last.
  if (( $+functions[prompt_powerlevel9k_teardown] )); then
    prompt_powerlevel9k_teardown
  fi
  eval "$(starship init zsh)"
  autoload -Uz add-zsh-hook
  add-zsh-hook zshexit tg-off
  print 'Tokenograph summary enabled here. tg-off stops it; new terminals stay opt-in.'
}
