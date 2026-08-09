#!/usr/bin/env bash
# Compatibility menu for the Linux Python replacements.

set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

echo "Select a TeamTalk SDK tool:"
echo "1) tt_spammer.py (login/logout tester)"
echo "2) ttbot_the_offender.py (trigger-based response bot)"
read -r -p "Choice [1-2]: " choice

case "$choice" in
    1) exec python3 "$script_dir/tt_spammer.py" "$@" ;;
    2) exec python3 "$script_dir/ttbot_the_offender.py" "$@" ;;
    *) echo "Invalid choice." >&2; exit 2 ;;
esac
