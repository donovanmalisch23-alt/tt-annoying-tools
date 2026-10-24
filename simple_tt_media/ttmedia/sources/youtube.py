"""YouTube (and general ffmpeg-readable) audio provider, backed by yt-dlp.

yt-dlp extracts a direct audio stream URL that the TeamTalk SDK can stream
through ffmpeg.  This source also doubles as the default search provider and
as the audio backend for the Spotify / Apple Music metadata sources.

yt-dlp is imported lazily so the package stays importable (and testable)
without it installed; a clear error is surfaced only when YouTube is actually
used.
"""

from __future__ import annotations

import re
import urllib.parse

from ..log import get_logger
from ..models import ResolveResult, Track
from .base import Source

log = get_logger(__name__)

_YT_HOSTS = ("youtube.com", "youtu.be", "music.youtube.com")


def _load_yt_dlp():
    try:
        import yt_dlp  # noqa: WPS433 (lazy import by design)
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "yt-dlp is not installed. Install it with `pip install yt-dlp`."
        ) from exc
    return yt_dlp


class YouTubeSource(Source):
    name = "youtube"
    is_audio_provider = True

    #: yt-dlp options tuned for audio-only extraction without downloading.
    _YDL_OPTS = {
        "format": "bestaudio/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": False,
        "extract_flat": False,
        "default_search": "ytsearch",
    }

    def matches(self, query: str) -> bool:
        # Default search provider: handle bare text and any YouTube URL.
        q = query.strip().lower()
        if any(host in q for host in _YT_HOSTS):
            return True
        return not q.startswith(("http://", "https://"))

    def _track_from_info(self, info: dict, requested_by: str) -> Track | None:
        stream_url = info.get("url")
        if not stream_url:
            return None
        return Track(
            title=info.get("title") or "Unknown title",
            stream_url=stream_url,
            source=self.name,
            artist=info.get("artist") or info.get("uploader") or "",
            duration=info.get("duration"),
            webpage_url=info.get("webpage_url") or "",
            requested_by=requested_by,
        )

    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        yt_dlp = _load_yt_dlp()
        try:
            with yt_dlp.YoutubeDL(self._YDL_OPTS) as ydl:
                info = ydl.extract_info(query, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
            log.warning("yt-dlp failed for %r: %s", query, exc)
            return ResolveResult(error=f"Could not resolve: {exc}")

        if info is None:
            return ResolveResult(error="No results found.")

        # A playlist / search result carries an "entries" list.
        entries = info.get("entries")
        if entries is not None:
            tracks = []
            for entry in entries:
                if not entry:
                    continue
                track = self._track_from_info(entry, requested_by)
                if track:
                    tracks.append(track)
            if not tracks:
                return ResolveResult(error="No playable results found.")
            return ResolveResult(tracks=tracks)

        track = self._track_from_info(info, requested_by)
        if track is None:
            return ResolveResult(error="No playable stream in result.")
        return ResolveResult(tracks=[track])

    def resolve_search(self, query: str, requested_by: str = "") -> Track | None:
        result = self.resolve(f"ytsearch1:{query}", requested_by)
        return result.tracks[0] if result.ok else None

    @staticmethod
    def _video_id(url: str) -> str:
        if not url:
            return ""
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            return ""
        if parsed.hostname and "youtu.be" in parsed.hostname:
            return parsed.path.lstrip("/")
        qs = urllib.parse.parse_qs(parsed.query)
        if "v" in qs:
            return qs["v"][0]
        m = re.search(r"/(?:shorts|embed)/([A-Za-z0-9_-]{6,})", url)
        return m.group(1) if m else ""

    def related(self, track: Track, limit: int = 3, exclude=None) -> list:
        """Find tracks related to ``track`` for autoplay.

        Prefers YouTube's auto-generated radio mix (``RD<id>``); falls back to
        a search by artist/title. Returns fully-resolved, playable tracks.
        This does network I/O and is meant to be called off the event loop.
        """
        exclude = set(exclude or ())
        yt_dlp = _load_yt_dlp()
        candidates: list[str] = []

        vid = self._video_id(track.webpage_url)
        if vid:
            mix_url = f"https://www.youtube.com/watch?v={vid}&list=RD{vid}"
            flat_opts = dict(self._YDL_OPTS, extract_flat=True,
                             noplaylist=False, playlistend=15)
            try:
                with yt_dlp.YoutubeDL(flat_opts) as ydl:
                    info = ydl.extract_info(mix_url, download=False)
                for entry in (info.get("entries") or []):
                    if not entry:
                        continue
                    url = entry.get("url") or entry.get("id") or ""
                    if url and not url.startswith("http"):
                        url = f"https://www.youtube.com/watch?v={url}"
                    if url:
                        candidates.append(url)
            except Exception as exc:  # noqa: BLE001
                log.debug("radio mix lookup failed: %s", exc)

        resolved: list[Track] = []
        for url in candidates:
            if self._video_id(url) == vid:
                continue  # skip the seed itself
            result = self.resolve(url)
            if result.ok:
                track_out = result.tracks[0]
                if track_out.stream_url and track_out.stream_url not in exclude:
                    resolved.append(track_out)
            if len(resolved) >= limit:
                break

        if not resolved and (track.artist or track.title):
            query = f"{track.artist} {track.title}".strip()
            result = self.resolve(f"ytsearch{limit + 1}:{query}")
            if result.ok:
                for t in result.tracks:
                    if t.stream_url and t.stream_url not in exclude:
                        resolved.append(t)
                    if len(resolved) >= limit:
                        break
        return resolved
