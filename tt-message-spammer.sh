#!/usr/bin/env bash
# Compatibility launcher. The implementation sends through TeamTalk's API.

set -euo pipefail
script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$script_dir/tt_message_spammer.py" "$@"
