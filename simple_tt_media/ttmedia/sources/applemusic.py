"""Apple Music metadata source.

Like Spotify, Apple Music audio is DRM-protected, so this source is
metadata-only: it reads track/album info and emits ``search_query`` tracks
that the registry resolves to playable audio via YouTube.

Metadata comes from Apple's **public iTunes Search API**, which needs no key
or account.  An ``music.apple.com`` album/song URL is parsed for its numeric
id and looked up; free-text Apple Music queries are not handled here (they
fall through to YouTube search like any other text).
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request

from ..log import get_logger
from ..models import ResolveResult, Track
from .base import Source

log = get_logger(__name__)

_LOOKUP_URL = "https://itunes.apple.com/lookup"
_ID_RE = re.compile(r"[?&]i=(\d+)")           # song id inside an album URL
_ALBUM_RE = re.compile(r"/album/[^/]*/(\d+)")  # album id in the path


class AppleMusicSource(Source):
    name = "applemusic"
    is_audio_provider = False

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def matches(self, query: str) -> bool:
        return "music.apple.com/" in query.lower()

    def _lookup(self, params: dict) -> list:
        url = _LOOKUP_URL + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers={"User-Agent": "simple-tt-media"})
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("results", [])

    @staticmethod
    def _track_from_result(item: dict, requested_by: str) -> Track:
        name = item.get("trackName") or item.get("collectionName") or ""
        artist = item.get("artistName", "")
        return Track(
            title=name,
            stream_url="",
            source="applemusic",
            artist=artist,
            duration=(item.get("trackTimeMillis") or 0) / 1000 or None,
            webpage_url=item.get("trackViewUrl") or item.get("collectionViewUrl") or "",
            requested_by=requested_by,
            search_query=f"{artist} {name}".strip(),
        )

    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        song = _ID_RE.search(query)
        album = _ALBUM_RE.search(query)
        try:
            if song:
                results = self._lookup({"id": song.group(1), "entity": "song"})
            elif album:
                results = self._lookup(
                    {"id": album.group(1), "entity": "song", "limit": 200})
                # First element is the album/collection; keep only tracks.
                results = [r for r in results if r.get("wrapperType") == "track"]
            else:
                return ResolveResult(error="Unrecognised Apple Music URL.")
        except Exception as exc:  # noqa: BLE001
            log.warning("Apple Music lookup failed for %r: %s", query, exc)
            return ResolveResult(error=f"Apple Music lookup failed: {exc}")

        tracks = [self._track_from_result(r, requested_by) for r in results if r]
        if not tracks:
            return ResolveResult(error="No tracks found for that Apple Music link.")
        return ResolveResult(tracks=tracks)
