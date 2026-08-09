#!/usr/bin/env bash
# Start the repository's localhost-only TeamTalk test server.

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
if ! command -v tt5srv >/dev/null 2>&1; then
    echo "Error: tt5srv is not installed." >&2
    exit 1
fi

chmod 600 "$script_dir/tt5srv.xml"
echo "Starting TeamTalk on 127.0.0.1:10333 (Ctrl-C to stop)…"
exec tt5srv -nd -wd "$script_dir"
