"""The source plugin contract.

A source turns user input into :class:`Track` objects and, later, turns a
track into something ffmpeg can open. Two roles:

* ``matches(text)`` / ``from_url(text)`` - claim and expand a pasted URL
  (a single item, an album, a playlist ...).
* ``search(query, limit)`` - free-text search (only some sources support it).
* ``resolve(track)`` - fill ``track.playback_url`` (and headers/duration) just
  before playback. Metadata-only sources (Spotify, Apple Music) delegate this
  to the audio provider via the registry.

Sources fail soft with :class:`SourceError` carrying a user-facing message.
Adding a service = subclass, implement, register in ``sources/__init__.py``.
"""

from __future__ import annotations

import abc
from typing import List

from ..models import Track


class Source(abc.ABC):
    #: identifier used in config, ``service`` command and track.source
    name: str = "source"
    #: human-friendly name for messages
    title: str = "Source"
    #: can this source answer free-text searches?
    searchable: bool = False

    @abc.abstractmethod
    def matches(self, text: str) -> bool:
        """True if ``text`` is a URL/identifier this source handles."""

    @abc.abstractmethod
    def from_url(self, text: str, requested_by: str = "") -> List[Track]:
        """Expand a URL into one or more tracks. Raise SourceError on failure."""

    def search(self, query: str, limit: int = 5, requested_by: str = "") -> List[Track]:
        raise NotImplementedError

    def resolve(self, track: Track) -> None:
        """Fill track.playback_url (+ headers). Raise SourceError on failure."""
        raise NotImplementedError
