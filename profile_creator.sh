#!/bin/sh
# Interactive creator for TT Annoying Tools .profile files.
#
# Walks every settable value across all the tools, shows what each one does,
# and lets you press Enter to skip anything you do not want. Settings you
# skip are written into the profile as comments, so filling one in later is
# just uncommenting a line.
#
# The profile is written to the current directory, which is also the first
# place --profile looks:
#
#   ./profile_creator.sh              # prompts for a name first
#   ./profile_creator.sh my-lab       # pre-fills the profile name

set -e
exec python3 "$(dirname "$0")/tt_profile.py" --create "$@"