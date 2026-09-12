#!/usr/bin/env python3
"""Start the repository's localhost-only TeamTalk test server.

Runs ``tt5srv`` in the foreground with the bundled ``tt5srv.xml`` — Ctrl-C
stops it, exactly like the shell script this replaces.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def main() -> int:
    if shutil.which("tt5srv") is None:
        print("Error: tt5srv is not installed.", file=sys.stderr)
        return 1

    os.chmod(SCRIPT_DIR / "tt5srv.xml", 0o600)
    print("Starting TeamTalk on 127.0.0.1:10333 (Ctrl-C to stop)…", flush=True)
    # Replace this process with the server, so signals (Ctrl-C, SIGINT from
    # tests) reach tt5srv directly.
    os.execvp("tt5srv", ["tt5srv", "-nd", "-wd", str(SCRIPT_DIR)])
    return 0  # unreachable: execvp replaces the process or raises


if __name__ == "__main__":
    raise SystemExit(main())