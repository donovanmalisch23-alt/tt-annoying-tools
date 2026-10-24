"""Spotify metadata source.

Spotify audio is DRM-protected and cannot be restreamed, so this source does
not provide audio.  Instead it reads track / album / playlist *metadata* via
the Spotify Web API (Client Credentials flow) and emits tracks carrying a
``search_query`` ("artist title").  The registry then resolves the actual
audio through the YouTube provider.

Requires a free Spotify developer app (client id + secret) and the
``spotipy`` package.  Both the dependency and the credentials are optional:
if Spotify is disabled in config this module is never constructed.
"""

from __future__ import annotations

import re

from ..log import get_logger
from ..models import ResolveResult, Track
from .base import Source

log = get_logger(__name__)

_SPOTIFY_RE = re.compile(
    r"open\.spotify\.com/(?:intl-[a-z]+/)?(track|album|playlist)/([A-Za-z0-9]+)"
)


def _load_spotipy():
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyClientCredentials
    except ModuleNotFoundError as exc:  # pragma: no cover
        raise RuntimeError(
            "spotipy is not installed. Install it with `pip install spotipy`."
        ) from exc
    return spotipy, SpotifyClientCredentials


class SpotifySource(Source):
    name = "spotify"
    is_audio_provider = False

    def __init__(self, client_id: str, client_secret: str) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._client = None  # lazily created

    def _get_client(self):
        if self._client is None:
            spotipy, SpotifyClientCredentials = _load_spotipy()
            auth = SpotifyClientCredentials(
                client_id=self._client_id,
                client_secret=self._client_secret,
            )
            self._client = spotipy.Spotify(client_credentials_manager=auth)
        return self._client

    def matches(self, query: str) -> bool:
        return "open.spotify.com/" in query.lower()

    @staticmethod
    def _track_from_item(item: dict, requested_by: str) -> Track:
        name = item.get("name", "")
        artists = ", ".join(a["name"] for a in item.get("artists", []))
        return Track(
            title=name,
            stream_url="",  # resolved later via YouTube
            source="spotify",
            artist=artists,
            duration=(item.get("duration_ms") or 0) / 1000 or None,
            webpage_url=item.get("external_urls", {}).get("spotify", ""),
            requested_by=requested_by,
            search_query=f"{artists} {name}".strip(),
        )

    def resolve(self, query: str, requested_by: str = "") -> ResolveResult:
        match = _SPOTIFY_RE.search(query)
        if not match:
            return ResolveResult(error="Unrecognised Spotify URL.")
        kind, spotify_id = match.group(1), match.group(2)
        try:
            client = self._get_client()
            if kind == "track":
                items = [client.track(spotify_id)]
            elif kind == "album":
                album = client.album(spotify_id)
                items = album["tracks"]["items"]
            else:  # playlist
                results = client.playlist_items(spotify_id)
                items = [it["track"] for it in results["items"] if it.get("track")]
        except Exception as exc:  # noqa: BLE001
            log.warning("Spotify lookup failed for %r: %s", query, exc)
            return ResolveResult(error=f"Spotify lookup failed: {exc}")

        tracks = [self._track_from_item(it, requested_by) for it in items if it]
        if not tracks:
            return ResolveResult(error="No tracks found for that Spotify link.")
        return ResolveResult(tracks=tracks)
