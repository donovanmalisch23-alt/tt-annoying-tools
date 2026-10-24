"""yt-dlp backed sources: YouTube, SoundCloud, and other yt-dlp sites.

yt-dlp is the audio provider for the whole app: it turns page URLs into
direct stream URLs, and it is what Spotify/Apple Music tracks are matched
against. YouTube now needs a JavaScript runtime to solve its challenges;
setup installs Deno into ``tools/deno`` and we point yt-dlp at it.

A fresh ``YoutubeDL`` instance is created per call (they are not
thread-safe), and searches/playlists use flat extraction so they're fast;
the per-track stream URL is fetched later by :meth:`resolve`.
"""

from __future__ import annotations

import shutil
import urllib.parse
from typing import List, Optional

from ..errors import SourceError
from ..log import get_logger
from ..models import Track
from .base import Source

log = get_logger(__name__)


def _load_yt_dlp():
    try:
        import yt_dlp
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise SourceError("yt-dlp is not installed. Re-run Setup.bat.") from exc
    return yt_dlp


def _host(url: str) -> str:
    try:
        return (urllib.parse.urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


class YtDlpRunner:
    """Builds yt-dlp options once (JS runtime, cookies) and runs extractions."""

    def __init__(self, deno_path: str = "", socket_timeout: int = 20) -> None:
        self._deno = deno_path
        self._timeout = socket_timeout

    def options(self, **extra) -> dict:
        opts = {
            "quiet": True,
            "no_warnings": True,
            # Prefer the highest-quality audio-only stream, Opus first (what
            # TeamTalk uses), then AAC, then anything.
            "format": "bestaudio[acodec=opus]/bestaudio[acodec^=mp4a]/bestaudio/best",
            "noplaylist": True,
            "socket_timeout": self._timeout,
            "retries": 2,              # bounded so a bad network never hangs setup/search
            "extractor_retries": 1,
            "skip_download": True,
            "noprogress": True,
            "cachedir": False,  # avoid a corruptible on-disk cache (cf. TTMediaBot #120)
        }
        runtime = self.js_runtime()
        if runtime:
            opts["js_runtimes"] = {runtime[0]: {"path": runtime[1]}}
        opts.update(extra)
        return opts

    def js_runtime(self) -> Optional[tuple]:
        if self._deno:
            return ("deno", self._deno)
        found = shutil.which("deno")
        if found:
            return ("deno", found)
        node = shutil.which("node")
        if node:
            return ("node", node)
        return None

    def extract(self, target: str, **extra) -> dict:
        yt_dlp = _load_yt_dlp()
        try:
            with yt_dlp.YoutubeDL(self.options(**extra)) as ydl:
                info = ydl.extract_info(target, download=False)
        except Exception as exc:  # noqa: BLE001 - yt-dlp raises many types
            msg = str(exc).replace("ERROR: ", "").strip()
            raise SourceError(_friendly_error(msg)) from exc
        if not info:
            raise SourceError("Nothing was found.")
        return info


def _friendly_error(msg: str) -> str:
    low = msg.lower()
    if "unsupported url" in low:
        return "That link isn't supported."
    if "private" in low or "unavailable" in low or "sign in" in low:
        return "That item is private, restricted, or unavailable."
    if "http error 403" in low:
        return "The service refused the request (HTTP 403). Please try again later."
    if "timed out" in low or "timeout" in low:
        return "The service took too long to respond. Please try again."
    return msg[:300] or "The service returned an error."


def _entry_url(entry: dict, base: str) -> str:
    url = entry.get("webpage_url") or entry.get("url") or ""
    if url and not url.startswith("http") and base == "youtube":
        url = f"https://www.youtube.com/watch?v={url}"
    if not url and entry.get("id") and base == "youtube":
        url = f"https://www.youtube.com/watch?v={entry['id']}"
    return url


def track_from_info(info: dict, source: str, requested_by: str) -> Track:
    """A fully-resolved (playable now) track from a full yt-dlp info dict."""
    return Track(
        title=info.get("track") or info.get("title") or "Unknown title",
        artist=info.get("artist") or info.get("uploader") or info.get("channel") or "",
        playback_url=info.get("url") or "",
        http_headers=dict(info.get("http_headers") or {}),
        duration=info.get("duration"),
        url=info.get("webpage_url") or info.get("original_url") or "",
        query=info.get("webpage_url") or info.get("original_url") or "",
        source=source,
        requested_by=requested_by,
        is_live=bool(info.get("is_live") or info.get("live_status") == "is_live"),
    )


def track_from_entry(entry: dict, source: str, requested_by: str) -> Track:
    """A lightweight (resolve-later) track from a flat playlist/search entry."""
    url = _entry_url(entry, source)
    return Track(
        title=entry.get("title") or "Unknown title",
        artist=entry.get("uploader") or entry.get("channel") or "",
        duration=entry.get("duration"),
        url=url,
        query=url,
        source=source,
        requested_by=requested_by,
        needs_resolve=True,
        is_live=entry.get("live_status") == "is_live",
    )


class YtDlpSource(Source):
    """Shared logic for yt-dlp sites; subclasses set hosts and search prefix."""

    hosts: tuple = ()
    search_prefix: str = ""

    def __init__(self, runner: YtDlpRunner, max_playlist: int = 500) -> None:
        self._runner = runner
        self._max_playlist = max_playlist

    @property
    def runner(self) -> YtDlpRunner:
        return self._runner

    def matches(self, text: str) -> bool:
        h = _host(text)
        return any(h == x or h.endswith("." + x) for x in self.hosts)

    def from_url(self, text: str, requested_by: str = "") -> List[Track]:
        info = self._runner.extract(
            text.strip(), extract_flat="in_playlist",
            playlistend=self._max_playlist)
        entries = info.get("entries")
        if entries is not None:
            tracks = [track_from_entry(e, self.name, requested_by)
                      for e in entries if e]
            tracks = [t for t in tracks if t.query]
            if not tracks:
                raise SourceError("That playlist is empty or unavailable.")
            return tracks[: self._max_playlist]
        track = track_from_info(info, self.name, requested_by)
        if not track.playback_url:
            raise SourceError("No playable audio stream was found.")
        return [track]

    def search(self, query: str, limit: int = 5, requested_by: str = "") -> List[Track]:
        limit = max(1, min(limit, 50))
        info = self._runner.extract(f"{self.search_prefix}{limit}:{query}",
                                    extract_flat="in_playlist")
        entries = [e for e in (info.get("entries") or []) if e]
        tracks = [track_from_entry(e, self.name, requested_by) for e in entries]
        return [t for t in tracks if t.query]

    def resolve(self, track: Track) -> None:
        target = track.query or track.url
        if not target:
            raise SourceError("This track has nothing to resolve.")
        info = self._runner.extract(target)
        if info.get("entries"):
            first = next((e for e in info["entries"] if e), None)
            if not first:
                raise SourceError("Nothing playable was found.")
            info = first if first.get("url") else self._runner.extract(
                _entry_url(first, self.name))
        url = info.get("url")
        if not url:
            raise SourceError("No playable audio stream was found.")
        track.playback_url = url
        track.http_headers = dict(info.get("http_headers") or {})
        track.duration = track.duration or info.get("duration")
        track.is_live = bool(info.get("is_live") or info.get("live_status") == "is_live")
        if not track.url:
            track.url = info.get("webpage_url") or ""
        track.needs_resolve = False


class YouTubeSource(YtDlpSource):
    name = "youtube"
    title = "YouTube"
    searchable = True
    hosts = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    search_prefix = "ytsearch"


class SoundCloudSource(YtDlpSource):
    name = "soundcloud"
    title = "SoundCloud"
    searchable = True
    hosts = ("soundcloud.com", "snd.sc")
    search_prefix = "scsearch"


class GenericSiteSource(YtDlpSource):
    """Anything else yt-dlp understands (Bandcamp, Vimeo, Mixcloud, ...)."""

    name = "web"
    title = "Web"
    searchable = False

    def matches(self, text: str) -> bool:
        return text.strip().lower().startswith(("http://", "https://"))
