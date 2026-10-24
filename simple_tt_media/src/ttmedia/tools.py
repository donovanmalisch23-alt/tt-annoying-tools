"""Locate the external tools we rely on: ffmpeg and the Deno JS runtime.

Resolution order for each: an explicit path in config, then a copy installed
under the project's ``tools/`` folder (what Setup installs), then the system
PATH. Returns "" when nothing is found so callers can warn clearly.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from .sdk import project_root


def _exe(name: str) -> str:
    import sys
    return f"{name}.exe" if sys.platform == "win32" else name


def _search_tools_dir(*relative: str) -> Optional[str]:
    base = project_root() / "tools"
    for rel in relative:
        p = base / rel
        if p.is_file():
            return str(p)
    # Also try a shallow glob (archive layouts vary, e.g. ffmpeg-*/bin/ffmpeg.exe).
    name = relative[0].split("/")[-1]
    for found in base.glob(f"**/{name}"):
        if found.is_file():
            return str(found)
    return None


def find_ffmpeg(configured: str = "") -> str:
    if configured and Path(configured).exists():
        return configured
    ff = _exe("ffmpeg")
    return (_search_tools_dir(f"ffmpeg/{ff}", f"ffmpeg/bin/{ff}")
            or shutil.which("ffmpeg") or "")


def find_deno(configured: str = "") -> str:
    if configured and Path(configured).exists():
        return configured
    dn = _exe("deno")
    return (_search_tools_dir(f"deno/{dn}", dn) or shutil.which("deno") or "")
