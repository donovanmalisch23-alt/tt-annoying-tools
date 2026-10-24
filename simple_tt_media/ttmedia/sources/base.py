"""The source plugin contract.

A *source* turns a user query (a URL or free text) into one or more
:class:`~ttmedia.models.Track` objects.  Sources come in two flavours:

* **Audio providers** return tracks with a ready-to-play ``stream_url``
  (e.g. YouTube via yt-dlp, or a direct file/URL).
* **Metadata providers** (Spotify, Apple Music) return tracks with a
  ``search_query`` but no ``stream_url``; the registry resolves the actual
  audio through an audio provider.  This is deliberate: Spotify and Apple
  Music audio is DRM-protected and cannot be restreamed, so we use them for
  search/metadata only and play the matching audio from YouTube.

Adding a new service is just: subclass :class:`Source`, implement
``matches`` and ``resolve``, and register it.  Nothing else needs to change.
"""

from __future__ import annotations

import abc

from ..models import ResolveResult


class Source(abc.ABC):
    #: short identifier shown in "now playing" and used in logs
    name: str = "source"
    #: True if this source produces playable stream URLs directly
    is_audio_provider: bool = False

    @abc.abstractmethod
    def matches(self, query: str) -> bool:
        """Return True if this source should handle ``query``."""

    @abc.abstractmethod
    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        """Turn ``query`` into a :class:`ResolveResult`.

        Implementations should fail soft by returning a result with ``error``
        set rather than raising, so one broken query never takes the bot down.
        """

    # Optional: audio providers override this to resolve a free-text search
    # into a single playable track (used to back metadata providers).
    def resolve_search(self, query: str, requested_by: str = ""):  # -> Track | None
        return None
