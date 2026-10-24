"""Direct media URLs and internet radio (anything ffmpeg can open directly)."""

from __future__ import annotations

import os
import urllib.parse
from typing import List

from ..errors import SourceError
from ..models import Track
from .base import Source

_MEDIA_EXTS = {".mp3", ".aac", ".m4a", ".ogg", ".oga", ".opus", ".flac",
               ".wav", ".wma", ".mp4", ".m4b", ".webm", ".mka"}
_STREAM_HINTS = (".m3u8", ".pls", ".m3u", "/stream", "icecast", "shoutcast", ":8000")


class DirectUrlSource(Source):
    name = "url"
    title = "Direct URL / radio"
    searchable = False

    def matches(self, text: str) -> bool:
        t = text.strip().lower()
        if not t.startswith(("http://", "https://")):
            return False
        path = urllib.parse.urlparse(t).path
        ext = os.path.splitext(path)[1]
        if ext in _MEDIA_EXTS or any(h in t for h in _STREAM_HINTS):
            return True
        # Radio URLs often carry the codec as a token, e.g. ".../stream-128-mp3".
        base = path.rsplit("/", 1)[-1]
        return any(tag in base for tag in ("mp3", "aac", "ogg", "opus", "flac"))

    def from_url(self, text: str, requested_by: str = "") -> List[Track]:
        url = text.strip()
        low = url.lower()
        path = urllib.parse.urlparse(low).path
        ext = os.path.splitext(path)[1]
        name = os.path.basename(path) or urllib.parse.urlparse(url).hostname or url
        is_live = ext not in _MEDIA_EXTS  # radio/HLS: no reliable duration/seek
        return [Track(title=name, playback_url=url, url=url, query=url,
                      source=self.name, requested_by=requested_by,
                      is_live=is_live)]

    def resolve(self, track: Track) -> None:
        if not track.playback_url:
            if not track.query:
                raise SourceError("This track has no URL to play.")
            track.playback_url = track.query
        track.needs_resolve = False
