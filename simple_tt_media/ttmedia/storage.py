"""Saved playlists.

A playlist is stored as a list of *queries* (a URL or "artist title"), not
resolved stream URLs - stream URLs from services like YouTube expire, so we
re-resolve each entry freshly when the playlist is loaded. Storage is a plain
JSON file so it's easy to inspect, back up, or hand-edit.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List

from .log import get_logger
from .models import Track

log = get_logger(__name__)


def query_for(track: Track) -> str:
    """Best re-resolvable query for a track: its page URL, else artist+title."""
    if track.webpage_url:
        return track.webpage_url
    if track.artist:
        return f"{track.artist} {track.title}".strip()
    return track.title


class PlaylistStore:
    def __init__(self, path: str = "playlists.json") -> None:
        self._path = Path(path)
        self._lock = threading.Lock()

    def _read(self) -> Dict[str, List[str]]:
        if not self._path.exists():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return {k: list(v) for k, v in data.items()}
        except Exception as exc:  # noqa: BLE001
            log.warning("Could not read playlists file: %s", exc)
        return {}

    def _write(self, data: Dict[str, List[str]]) -> None:
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(self._path)  # atomic on POSIX

    def names(self) -> List[str]:
        with self._lock:
            return sorted(self._read().keys())

    def get(self, name: str) -> List[str]:
        with self._lock:
            return self._read().get(name.lower(), [])

    def save(self, name: str, queries: List[str]) -> None:
        with self._lock:
            data = self._read()
            data[name.lower()] = queries
            self._write(data)

    def delete(self, name: str) -> bool:
        with self._lock:
            data = self._read()
            if name.lower() in data:
                del data[name.lower()]
                self._write(data)
                return True
            return False
