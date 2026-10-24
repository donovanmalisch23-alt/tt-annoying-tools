"""Small value types shared across the app (no heavy imports)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


@dataclass
class Track:
    """One playable item.

    ``playback_url`` is what ffmpeg actually opens (a direct media URL or a
    local path). ``query`` is a re-resolvable description (a URL or "artist -
    title") used for saved lists, because direct URLs from services expire.
    ``needs_resolve`` marks a track whose ``playback_url`` must be fetched
    lazily just before playback (used by metadata-only services so a long
    queue doesn't pin thousands of soon-to-expire URLs).
    """

    title: str
    playback_url: str = ""
    source: str = "unknown"
    artist: str = ""
    duration: Optional[float] = None       # seconds
    url: str = ""                          # human-facing page link
    query: str = ""                        # re-resolvable description
    requested_by: str = ""
    http_headers: dict = field(default_factory=dict)
    needs_resolve: bool = False
    is_live: bool = False                  # radio / live streams: no seeking
    retries: int = 0                       # re-resolve attempts after errors

    def label(self) -> str:
        who = self.artist.strip()
        name = f"{who} - {self.title}" if who else self.title
        if self.duration:
            name += f" [{fmt_duration(self.duration)}]"
        return name


class RepeatMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


@dataclass
class SearchResult:
    """What a source returns: zero or more tracks, or a soft error message."""

    tracks: List[Track] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.tracks)


def fmt_duration(seconds: Optional[float]) -> str:
    if not seconds or seconds < 0:
        return "?:??"
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
