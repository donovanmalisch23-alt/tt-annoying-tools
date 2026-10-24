"""Download the external tools the bot needs, into the project's tools/ folder.

Run by Setup (``python -m ttmedia.bootstrap``). On Windows it fetches a static
ffmpeg build and the Deno JS runtime (needed by yt-dlp for YouTube). ffmpeg and
Deno are downloaded **in parallel**, each with a short socket timeout and a
hard overall deadline, so setup can never hang on a slow or dead mirror — a
failed download is reported and setup continues (tools already on PATH still
work). Existing copies are left untouched.
"""

from __future__ import annotations

import io
import sys
import threading
import time
import urllib.request
import zipfile
from pathlib import Path
from typing import Optional

from .sdk import project_root
from .tools import find_deno, find_ffmpeg

FFMPEG_WIN = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_WIN_FALLBACK = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
                       "latest/ffmpeg-master-latest-win64-gpl.zip")
DENO_WIN = ("https://github.com/denoland/deno/releases/latest/download/"
            "deno-x86_64-pc-windows-msvc.zip")

SOCKET_TIMEOUT = 30         # seconds per read with no data -> give up
OVERALL_DEADLINE = 300      # seconds hard cap for one download


def _tools_dir() -> Path:
    d = project_root() / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str) -> bytes:
    """Stream a URL to memory with a per-read timeout and a hard deadline."""
    req = urllib.request.Request(url, headers={"User-Agent": "simple-tt-media-setup"})
    deadline = time.monotonic() + OVERALL_DEADLINE
    buf = io.BytesIO()
    with urllib.request.urlopen(req, timeout=SOCKET_TIMEOUT) as resp:  # noqa: S310
        while True:
            if time.monotonic() > deadline:
                raise TimeoutError("download exceeded the time limit")
            chunk = resp.read(65536)
            if not chunk:
                break
            buf.write(chunk)
    return buf.getvalue()


def _extract_member(data: bytes, endswith: str, dest: Path) -> bool:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.filename.lower().replace("\\", "/").endswith(endswith):
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    out.write(src.read())
                return True
    return False


def _install_ffmpeg(result: dict) -> None:
    if find_ffmpeg():
        print("[ok] ffmpeg is already available.")
        result["ffmpeg"] = True
        return
    if sys.platform != "win32":
        print("[!!] Install ffmpeg with your package manager (e.g. apt install ffmpeg).")
        return
    dest = _tools_dir() / "ffmpeg" / "ffmpeg.exe"
    for url in (FFMPEG_WIN, FFMPEG_WIN_FALLBACK):
        try:
            print(f"Downloading ffmpeg from {url.split('/')[2]} ...")
            if _extract_member(_download(url), "bin/ffmpeg.exe", dest):
                print(f"[ok] Installed ffmpeg.")
                result["ffmpeg"] = True
                return
        except Exception as exc:  # noqa: BLE001
            print(f"[..] {url.split('/')[2]} failed: {exc}")
    print("[!!] Could not download ffmpeg automatically — see docs/TROUBLESHOOTING.md.")


def _install_deno(result: dict) -> None:
    if find_deno():
        print("[ok] A JavaScript runtime for YouTube is already available.")
        result["deno"] = True
        return
    if sys.platform != "win32":
        print("[!!] Install deno (https://deno.land) for YouTube support.")
        return
    dest = _tools_dir() / "deno" / "deno.exe"
    try:
        print("Downloading deno (needed for YouTube) ...")
        if _extract_member(_download(DENO_WIN), "deno.exe", dest):
            print("[ok] Installed deno.")
            result["deno"] = True
            return
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] Could not download deno: {exc}")


def main() -> int:
    print("Installing helper tools into:", _tools_dir())
    result: dict = {}
    threads = [
        threading.Thread(target=_install_ffmpeg, args=(result,)),
        threading.Thread(target=_install_deno, args=(result,)),
    ]
    for t in threads:
        t.start()
    # Join with a generous cap so a wedged thread can never block setup forever.
    for t in threads:
        t.join(timeout=OVERALL_DEADLINE + 30)
    # ffmpeg is required for any playback; deno is only needed for YouTube.
    return 0 if result.get("ffmpeg") else 1


if __name__ == "__main__":
    sys.exit(main())
