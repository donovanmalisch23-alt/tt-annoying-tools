"""Spotify links (metadata only), via Spotify's official interfaces.

Spotify audio is DRM-protected and cannot be restreamed, so this source only
reads *track information* and the registry plays the matching audio found on
YouTube.

* With a client id/secret (a free app from developer.spotify.com, entered in
  setup) the official Web API is used: tracks, albums and playlists.
* Without credentials, single-track links still work through Spotify's public
  oEmbed endpoint (title only); albums/playlists then ask for credentials.
"""

from __future__ import annotations

import base64
import re
import threading
import time
import urllib.parse
from typing import List, Optional

from ..errors import SourceError
from ..log import get_logger
from ..models import Track
from .base import Source
from .web import http_json

log = get_logger(__name__)

_URL_RE = re.compile(
    r"(?:open\.spotify\.com/(?:intl-[a-z]{2}(?:-[a-z]{2})?/)?|spotify:)"
    r"(track|album|playlist)[/:]([A-Za-z0-9]{10,40})")

API = "https://api.spotify.com/v1"
TOKEN_URL = "https://accounts.spotify.com/api/token"


def parse_spotify(text: str) -> Optional[tuple]:
    m = _URL_RE.search(text.strip())
    return (m.group(1), m.group(2)) if m else None


class SpotifySource(Source):
    name = "spotify"
    title = "Spotify"
    searchable = False

    def __init__(self, client_id: str = "", client_secret: str = "",
                 max_tracks: int = 500) -> None:
        self._id = client_id.strip()
        self._secret = client_secret.strip()
        self._max = max_tracks
        self._token = ""
        self._token_expiry = 0.0
        self._lock = threading.Lock()

    @property
    def has_credentials(self) -> bool:
        return bool(self._id and self._secret)

    def matches(self, text: str) -> bool:
        return parse_spotify(text) is not None

    # ---- official Web API ----------------------------------------------

    def _bearer(self) -> str:
        with self._lock:
            if self._token and time.time() < self._token_expiry - 60:
                return self._token
            auth = base64.b64encode(f"{self._id}:{self._secret}".encode()).decode()
            data = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
            tok = http_json(TOKEN_URL, headers={
                "Authorization": f"Basic {auth}",
                "Content-Type": "application/x-www-form-urlencoded"}, data=data)
            self._token = tok.get("access_token", "")
            self._token_expiry = time.time() + int(tok.get("expires_in", 3600))
            if not self._token:
                raise SourceError("Spotify did not return an access token; "
                                  "check the client id/secret.")
            return self._token

    def _api(self, url: str):
        return http_json(url, headers={"Authorization": f"Bearer {self._bearer()}"})

    def _paged(self, url: str) -> List[dict]:
        items: List[dict] = []
        while url and len(items) < self._max:
            page = self._api(url)
            items.extend(page.get("items") or [])
            url = page.get("next")
        return items[: self._max]

    # ---- Source API -----------------------------------------------------

    def from_url(self, text: str, requested_by: str = "") -> List[Track]:
        parsed = parse_spotify(text)
        if not parsed:
            raise SourceError("That doesn't look like a Spotify link.")
        kind, sid = parsed
        if not self.has_credentials:
            if kind == "track":
                return [self._via_oembed(sid, requested_by)]
            raise SourceError(
                "Spotify albums and playlists need a free Spotify developer app. "
                "Add spotify_client_id and spotify_client_secret to config.toml "
                "(see docs/SETUP.md).")
        if kind == "track":
            items = [self._api(f"{API}/tracks/{sid}")]
        elif kind == "album":
            album = self._api(f"{API}/albums/{sid}")
            items = self._paged(f"{API}/albums/{sid}/tracks?limit=50")
            for it in items:  # album track objects omit the album; keep artists
                it.setdefault("external_urls", {})
            if not items and album.get("tracks"):
                items = album["tracks"].get("items") or []
        else:
            items = [it.get("track") for it in
                     self._paged(f"{API}/playlists/{sid}/tracks?limit=100")]
        tracks = [_track(it, requested_by) for it in items if it and it.get("name")]
        if not tracks:
            raise SourceError("No playable tracks were found in that Spotify link.")
        return tracks

    def _via_oembed(self, sid: str, requested_by: str) -> Track:
        page = f"https://open.spotify.com/track/{sid}"
        data = http_json("https://open.spotify.com/oembed?url=" +
                         urllib.parse.quote(page, safe=""))
        title = (data.get("title") or "").strip()
        if not title:
            raise SourceError("Spotify didn't return details for that track.")
        return Track(title=title, source="spotify", url=page, query=title,
                     requested_by=requested_by, needs_resolve=True)


def _track(item: dict, requested_by: str) -> Track:
    artists = ", ".join(a.get("name", "") for a in item.get("artists") or [] if a)
    name = item.get("name", "")
    ms = item.get("duration_ms") or 0
    return Track(
        title=name,
        artist=artists,
        duration=ms / 1000 if ms else None,
        url=(item.get("external_urls") or {}).get("spotify", ""),
        query=f"{artists} - {name}" if artists else name,
        source="spotify",
        requested_by=requested_by,
        needs_resolve=True,
    )
