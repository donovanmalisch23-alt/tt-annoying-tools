"""Local file and direct-URL source.

Handles anything ffmpeg can open directly: a path to an audio file on the
machine running the bot, or a plain HTTP(S) stream / radio URL that isn't a
recognised streaming service.  This is the lowest-priority fallback.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..models import ResolveResult, Track
from .base import Source

_DIRECT_AUDIO_EXTS = {
    ".mp3", ".wav", ".ogg", ".oga", ".opus", ".flac", ".m4a", ".aac",
    ".wma", ".mp4", ".webm",
}


class LocalSource(Source):
    name = "local"
    is_audio_provider = True

    def matches(self, query: str) -> bool:
        q = query.strip()
        if os.path.isfile(q):
            return True
        lower = q.lower()
        if lower.startswith(("http://", "https://")):
            # Treat as direct media only if it looks like a media file or a
            # stream; recognised services are handled by their own sources.
            return any(lower.split("?")[0].endswith(ext)
                       for ext in _DIRECT_AUDIO_EXTS) or ".m3u8" in lower
        return False

    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        q = query.strip()
        if os.path.isfile(q):
            title = Path(q).stem
        else:
            title = q
        return ResolveResult(tracks=[Track(
            title=title,
            stream_url=q,
            source=self.name,
            requested_by=requested_by,
        )])
