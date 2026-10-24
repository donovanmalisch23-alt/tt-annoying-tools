"""Source registry: build the enabled sources and route queries through them.

Routing:
  * a URL is handed to the first source that ``matches`` it (services first,
    then direct media/radio, then a generic yt-dlp fallback);
  * free text is searched on the currently-selected *service* (default
    YouTube) — the ``service`` command changes it;
  * before playback, :meth:`resolve` fills a track's ``playback_url``.
    Metadata tracks (Spotify / Apple Music) are resolved by finding the
    closest match on YouTube by title and duration.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from ..config import Config
from ..errors import SourceError
from ..log import get_logger
from ..models import Track
from .applemusic import AppleMusicSource
from .base import Source
from .direct import DirectUrlSource
from .spotify import SpotifySource
from .ytdlp import (GenericSiteSource, SoundCloudSource, YouTubeSource,
                    YtDlpRunner, track_from_info)

log = get_logger(__name__)


class SourceRegistry:
    def __init__(self, cfg: Config) -> None:
        self._cfg = cfg
        s = cfg.sources
        self._runner = YtDlpRunner(deno_path=cfg.tools.deno_path,
                                   socket_timeout=20)
        self._youtube: Optional[YouTubeSource] = None

        url_sources: List[Source] = []
        searchable: Dict[str, Source] = {}
        by_name: Dict[str, Source] = {}

        if s.youtube:
            self._youtube = YouTubeSource(self._runner, s.max_playlist_tracks)
            url_sources.append(self._youtube)
            searchable["youtube"] = self._youtube
        if s.soundcloud:
            sc = SoundCloudSource(self._runner, s.max_playlist_tracks)
            url_sources.append(sc)
            searchable["soundcloud"] = sc
        if s.spotify:
            url_sources.append(SpotifySource(
                s.spotify_client_id, s.spotify_client_secret, s.max_playlist_tracks))
        if s.applemusic:
            am = AppleMusicSource(s.max_playlist_tracks)
            url_sources.append(am)
            searchable["apple"] = am
        if s.direct_url:
            url_sources.append(DirectUrlSource())
        # Generic yt-dlp fallback claims any other http(s) URL; keep it last.
        self._generic = GenericSiteSource(self._runner, s.max_playlist_tracks)
        url_sources.append(self._generic)

        for src in url_sources:
            by_name.setdefault(src.name, src)

        self._url_sources = url_sources
        self._searchable = searchable
        self._by_name = by_name
        # Default search service: YouTube if enabled, else first searchable.
        self._service = ("youtube" if "youtube" in searchable
                         else next(iter(searchable), ""))

    # ---- service selection ---------------------------------------------

    @property
    def service(self) -> str:
        return self._service

    def services(self) -> List[str]:
        return list(self._searchable.keys())

    def set_service(self, name: str) -> None:
        name = name.strip().lower()
        if name not in self._searchable:
            raise SourceError(
                f"Unknown service '{name}'. Available: "
                f"{', '.join(self._searchable) or 'none'}.")
        self._service = name

    def enabled_names(self) -> List[str]:
        seen = []
        for src in self._url_sources:
            if src.name not in seen and src.name != "web":
                seen.append(src.name)
        return seen

    # ---- input handling -------------------------------------------------

    def is_url(self, text: str) -> bool:
        return text.strip().lower().startswith(("http://", "https://", "spotify:"))

    def from_input(self, text: str, requested_by: str = "") -> List[Track]:
        """Resolve a URL into tracks, or search the current service for text."""
        text = text.strip()
        if not text:
            raise SourceError("Nothing to play.")
        if self.is_url(text):
            for src in self._url_sources:
                if src.matches(text):
                    return src.from_url(text, requested_by)
            raise SourceError("No source could handle that link.")
        return self.search(text, requested_by=requested_by)

    def play_direct(self, url: str, requested_by: str = "") -> List[Track]:
        """Force-treat a URL as a direct media/radio stream (the 'url' command)."""
        for src in self._url_sources:
            if src.name == "url":
                return src.from_url(url, requested_by)
        raise SourceError("Direct URL playback is disabled in the config.")

    def search(self, query: str, service: str = "", requested_by: str = "",
               limit: Optional[int] = None) -> List[Track]:
        name = (service or self._service).lower()
        src = self._searchable.get(name)
        if src is None:
            raise SourceError(f"Service '{name}' can't search.")
        n = limit or self._cfg.sources.max_search_results
        results = src.search(query, limit=n, requested_by=requested_by)
        if not results:
            raise SourceError(f"Nothing found on {src.title} for: {query}")
        return results

    # ---- playback resolution -------------------------------------------

    def resolve(self, track: Track) -> bool:
        """Fill track.playback_url. Returns True on success, else False."""
        try:
            src = self._by_name.get(track.source)
            if src is not None and _can_resolve_directly(src):
                src.resolve(track)
                return bool(track.playback_url)
            # Metadata-only (Spotify/Apple) or unknown: match on YouTube.
            return self._resolve_via_youtube(track)
        except SourceError as exc:
            log.info("resolve failed for %s: %s", track.label(), exc)
            return False
        except Exception:  # noqa: BLE001
            log.exception("unexpected error resolving %s", track.label())
            return False

    def _resolve_via_youtube(self, track: Track) -> bool:
        if self._youtube is None:
            log.warning("Cannot resolve %s: YouTube is disabled.", track.source)
            return False
        query = track.query or (f"{track.artist} - {track.title}"
                                if track.artist else track.title)
        info = self._runner.extract(f"ytsearch5:{query}", extract_flat=False)
        entries = [e for e in (info.get("entries") or []) if e and e.get("url")]
        if not entries:
            return False
        best = _best_by_duration(entries, track.duration)
        track.playback_url = best["url"]
        track.http_headers = dict(best.get("http_headers") or {})
        if not track.duration:
            track.duration = best.get("duration")
        if not track.url:
            track.url = best.get("webpage_url") or ""
        track.is_live = bool(best.get("is_live"))
        track.needs_resolve = False
        return True


def _can_resolve_directly(src: Source) -> bool:
    return src.name in ("youtube", "soundcloud", "web", "url")


def _best_by_duration(entries: List[dict], target: Optional[float]) -> dict:
    if not target:
        return entries[0]
    return min(entries, key=lambda e: abs((e.get("duration") or 1e9) - target))


__all__ = ["SourceRegistry", "Source"]
