#!/bin/sh
# Works directly from a checkout; no installation or model/API calls.
tg_root=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd) || exit 1
cd -- "$tg_root" || exit 1
exec "${TOKENOGRAPH_PYTHON:-python3}" -m tokenograph.prompt "$@"
