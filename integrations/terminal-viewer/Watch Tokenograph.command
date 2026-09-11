#!/bin/zsh
cd -- "${0:A:h}" || exit 1
node ./watch.mjs "$@"
if (( $? != 0 )); then
  print 'Press Return to close this window.'
  read -r
fi
