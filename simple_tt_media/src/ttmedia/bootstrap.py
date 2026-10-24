"""Download the external tools the bot needs, into the project's tools/ folder.

Run by Setup (``python -m ttmedia.bootstrap``). On Windows it fetches a static
ffmpeg build and the Deno JS runtime (needed by yt-dlp for YouTube). Existing
copies are left alone, and a failure is reported but never fatal — the bot can
still use tools already on PATH.
"""

from __future__ import annotations

import io
import sys
import urllib.request
import zipfile
from pathlib import Path

from .sdk import project_root
from .tools import find_deno, find_ffmpeg

FFMPEG_WIN = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
FFMPEG_WIN_FALLBACK = ("https://github.com/BtbN/FFmpeg-Builds/releases/download/"
                       "latest/ffmpeg-master-latest-win64-gpl.zip")
DENO_WIN = ("https://github.com/denoland/deno/releases/latest/download/"
            "deno-x86_64-pc-windows-msvc.zip")


def _tools_dir() -> Path:
    d = project_root() / "tools"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "simple-tt-media-setup"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
        return resp.read()


def _extract_member(data: bytes, endswith: str, dest: Path) -> bool:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for info in zf.infolist():
            if info.filename.lower().replace("\\", "/").endswith(endswith):
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as out:
                    out.write(src.read())
                return True
    return False


def install_ffmpeg() -> bool:
    if find_ffmpeg():
        print("[ok] ffmpeg is already available.")
        return True
    if sys.platform != "win32":
        print("[!!] Please install ffmpeg with your package manager.")
        return False
    dest = _tools_dir() / "ffmpeg" / "ffmpeg.exe"
    for url in (FFMPEG_WIN, FFMPEG_WIN_FALLBACK):
        try:
            print(f"Downloading ffmpeg from {url.split('/')[2]} ...")
            if _extract_member(_download(url), "bin/ffmpeg.exe", dest):
                print(f"[ok] Installed ffmpeg -> {dest}")
                return True
        except Exception as exc:  # noqa: BLE001
            print(f"[..] {url.split('/')[2]} failed: {exc}")
    print("[!!] Could not download ffmpeg automatically. See docs/SETUP.md.")
    return False


def install_deno() -> bool:
    if find_deno():
        print("[ok] A JavaScript runtime (deno/node) is already available.")
        return True
    if sys.platform != "win32":
        print("[!!] Install deno (https://deno.land) for YouTube support.")
        return False
    dest = _tools_dir() / "deno" / "deno.exe"
    try:
        print("Downloading deno (needed for YouTube) ...")
        if _extract_member(_download(DENO_WIN), "deno.exe", dest):
            print(f"[ok] Installed deno -> {dest}")
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] Could not download deno: {exc}")
    return False


def main() -> int:
    print("Installing helper tools into:", _tools_dir())
    ok_ff = install_ffmpeg()
    install_deno()  # YouTube-only; not fatal for other services
    # ffmpeg is required for any playback; signal failure if it's missing.
    return 0 if ok_ff else 1


if __name__ == "__main__":
    sys.exit(main())
