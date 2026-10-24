"""Core data types shared across Simple TT Media.

These are deliberately tiny and dependency-free so that every other module
(and the test suite) can import them without pulling in the TeamTalk SDK or
any streaming-service library.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Track:
    """A single playable item.

    ``stream_url`` is what actually gets handed to the TeamTalk SDK for
    playback.  It may be a local file path or an HTTP(S) URL that ffmpeg can
    read.  The remaining fields are metadata used for the "now playing" and
    queue displays.
    """

    title: str
    stream_url: str
    source: str = "unknown"
    artist: str = ""
    duration: Optional[float] = None  # seconds, if known
    webpage_url: str = ""             # human-facing link to the original item
    requested_by: str = ""            # nickname of the user who queued it
    # Set by metadata-only sources (Spotify/Apple Music). When present and
    # stream_url is empty, the registry resolves playable audio via the
    # configured audio provider (YouTube) using this query.
    search_query: str = ""

    @property
    def playable(self) -> bool:
        return bool(self.stream_url)

    def display(self) -> str:
        """A short one-line label for chat messages."""
        who = self.artist.strip()
        name = f"{who} - {self.title}" if who else self.title
        if self.duration:
            name += f" [{format_duration(self.duration)}]"
        return name


@dataclass
class ResolveResult:
    """What a :class:`~ttmedia.sources.base.Source` returns.

    A query can expand to several tracks (a playlist or album), so resolvers
    always return a list.  ``error`` lets a resolver fail softly with a
    human-readable reason instead of raising.
    """

    tracks: list = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.tracks)


def format_duration(seconds: Optional[float]) -> str:
    """Render a second count as ``m:ss`` or ``h:mm:ss``."""
    if not seconds or seconds < 0:
        return "?:??"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
