"""Source registry: build enabled sources from config and dispatch queries.

Resolution order matters. Service URLs (Spotify, Apple Music) are matched
before the YouTube catch-all so a pasted Spotify link isn't swallowed by the
default text-search provider. Local files / direct URLs are matched first of
all. Metadata-only results are then back-filled with playable audio via the
audio provider (YouTube).
"""

from __future__ import annotations

from ..config import Config
from ..log import get_logger
from ..models import ResolveResult, Track
from .applemusic import AppleMusicSource
from .base import Source
from .local import LocalSource
from .spotify import SpotifySource
from .youtube import YouTubeSource

log = get_logger(__name__)


class SourceRegistry:
    def __init__(self, sources: list[Source], audio_provider: Source | None) -> None:
        self._sources = sources
        self._audio_provider = audio_provider

    @classmethod
    def from_config(cls, cfg: Config) -> "SourceRegistry":
        youtube = YouTubeSource() if cfg.sources.youtube_enabled else None
        ordered: list[Source] = [LocalSource()]
        if cfg.sources.spotify_enabled:
            ordered.append(SpotifySource(
                cfg.sources.spotify_client_id,
                cfg.sources.spotify_client_secret,
            ))
        if cfg.sources.applemusic_enabled:
            ordered.append(AppleMusicSource())
        if youtube is not None:
            ordered.append(youtube)  # catch-all, must be last
        return cls(ordered, audio_provider=youtube)

    def enabled_names(self) -> list[str]:
        return [s.name for s in self._sources]

    @property
    def audio_provider(self) -> Source | None:
        return self._audio_provider

    def related(self, track: Track, limit: int = 3, exclude=None) -> list:
        """Related tracks for autoplay, via the audio provider (if capable)."""
        provider = self._audio_provider
        if provider is None or not hasattr(provider, "related"):
            return []
        try:
            return provider.related(track, limit=limit, exclude=exclude)
        except Exception as exc:  # noqa: BLE001
            log.warning("related() failed: %s", exc)
            return []

    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        query = query.strip()
        if not query:
            return ResolveResult(error="Nothing to play.")
        for source in self._sources:
            try:
                if source.matches(query):
                    result = source.resolve(query, requested_by)
                    return self._fill_audio(result, requested_by)
            except Exception as exc:  # noqa: BLE001 - never let one source crash us
                log.exception("Source %s crashed on %r", source.name, query)
                return ResolveResult(error=f"{source.name} error: {exc}")
        return ResolveResult(error="No source could handle that query.")

    def _fill_audio(self, result: ResolveResult, requested_by: str) -> ResolveResult:
        """Resolve playable URLs for metadata-only tracks via the audio provider."""
        if not result.ok:
            return result
        if self._audio_provider is None:
            if any(not t.playable for t in result.tracks):
                return ResolveResult(
                    error="This link needs YouTube enabled to find playable audio."
                )
            return result

        filled: list[Track] = []
        for track in result.tracks:
            if track.playable:
                filled.append(track)
                continue
            found = self._audio_provider.resolve_search(
                track.search_query, requested_by)
            if found is None:
                log.info("No audio match for %r", track.search_query)
                continue
            # Keep the nicer original metadata, take the playable URL.
            found.title = track.title or found.title
            found.artist = track.artist or found.artist
            found.source = track.source
            found.webpage_url = track.webpage_url or found.webpage_url
            filled.append(found)

        if not filled:
            return ResolveResult(error="Couldn't find playable audio for that.")
        return ResolveResult(tracks=filled)


__all__ = ["SourceRegistry", "Source"]
