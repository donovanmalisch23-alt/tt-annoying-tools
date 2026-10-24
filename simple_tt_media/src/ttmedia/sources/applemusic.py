"""Apple Music links and search (metadata only), via Apple's public iTunes API.

Apple Music audio is DRM-protected, so like Spotify this source reads track
information and the registry plays the matching audio from YouTube.

Uses the documented iTunes Search / Lookup API, which needs no key:
  * song links   (…/album/name/123?i=456 or …/song/name/456)
  * album links  (…/album/name/123)
  * free-text search (``service apple`` then ``search``/``play``)
Apple Music *playlists* require Apple's developer-token API and are reported
as unsupported with a clear message.
"""

from __future__ import annotations

import re
import urllib.parse
from typing import List, Optional

from ..errors import SourceError
from ..models import Track
from .base import Source
from .web import http_json

LOOKUP = "https://itunes.apple.com/lookup"
SEARCH = "https://itunes.apple.com/search"

_COUNTRY_RE = re.compile(r"music\.apple\.com/([a-z]{2})/", re.I)
_SONG_PARAM_RE = re.compile(r"[?&]i=(\d+)")
_ALBUM_RE = re.compile(r"/album/(?:[^/?#]+/)?(\d+)")
_SONG_RE = re.compile(r"/song/(?:[^/?#]+/)?(\d+)")


def _country(text: str) -> str:
    m = _COUNTRY_RE.search(text)
    return m.group(1).lower() if m else "us"


class AppleMusicSource(Source):
    name = "apple"
    title = "Apple Music"
    searchable = True

    def __init__(self, max_tracks: int = 500) -> None:
        self._max = max_tracks

    def matches(self, text: str) -> bool:
        return "music.apple.com/" in text.lower() or "itunes.apple.com/" in text.lower()

    def from_url(self, text: str, requested_by: str = "") -> List[Track]:
        country = _country(text)
        if "/playlist/" in text.lower():
            raise SourceError("Apple Music playlists aren't supported (Apple only "
                              "exposes them to its developer API). Song and album "
                              "links work.")
        song = _SONG_PARAM_RE.search(text) or _SONG_RE.search(text)
        if song:
            results = self._lookup({"id": song.group(1), "country": country})
        else:
            album = _ALBUM_RE.search(text)
            if not album:
                raise SourceError("That Apple Music link isn't a song or album.")
            results = self._lookup({"id": album.group(1), "entity": "song",
                                    "limit": 200, "country": country})
        tracks = [_track(r, requested_by) for r in results
                  if r.get("wrapperType") == "track" or r.get("kind") == "song"]
        if not tracks:
            raise SourceError("Apple Music returned no tracks for that link.")
        return tracks[: self._max]

    def search(self, query: str, limit: int = 5, requested_by: str = "") -> List[Track]:
        data = http_json(SEARCH + "?" + urllib.parse.urlencode(
            {"term": query, "entity": "song", "limit": max(1, min(limit, 50))}))
        return [_track(r, requested_by) for r in data.get("results") or []
                if r.get("trackName")]

    def _lookup(self, params: dict) -> list:
        data = http_json(LOOKUP + "?" + urllib.parse.urlencode(params))
        return data.get("results") or []


def _track(r: dict, requested_by: str) -> Track:
    name = r.get("trackName") or ""
    artist = r.get("artistName") or ""
    ms: Optional[int] = r.get("trackTimeMillis")
    return Track(
        title=name,
        artist=artist,
        duration=ms / 1000 if ms else None,
        url=r.get("trackViewUrl") or "",
        query=f"{artist} - {name}" if artist else name,
        source="apple",
        requested_by=requested_by,
        needs_resolve=True,
    )
