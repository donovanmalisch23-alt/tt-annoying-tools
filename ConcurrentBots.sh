#!/usr/bin/env bash
# Compatibility launcher. The implementation launches idle bots through
# TeamTalk's SDK; running without arguments prompts for the connection and
# bot-count settings interactively.

set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/tt_concurrent_bots.py" "$@"