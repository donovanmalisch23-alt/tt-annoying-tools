#!/usr/bin/env bash
# Compatibility launcher. The implementation uses TeamTalk SDK calls.

set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/tt_leave_join_spammer.py" "$@"
