"""Tiny HTTP helper shared by the metadata sources (no third-party deps)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from ..errors import SourceError

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")


def http_get(url: str, timeout: float = 15.0, headers: dict | None = None,
             data: bytes | None = None) -> str:
    hdrs = {"User-Agent": USER_AGENT, "Accept-Language": "en-US,en;q=0.8"}
    hdrs.update(headers or {})
    req = urllib.request.Request(url, headers=hdrs, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise SourceError("That link was not found (404).") from exc
        if exc.code in (401, 403):
            raise SourceError("The service rejected the request; check the API "
                              "credentials in config.toml.") from exc
        raise SourceError(f"The service returned HTTP {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise SourceError("Couldn't reach the service. Check the internet connection.") from exc


def http_json(url: str, timeout: float = 15.0, headers: dict | None = None,
              data: bytes | None = None):
    text = http_get(url, timeout, headers=headers, data=data)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise SourceError("The service sent an unexpected response.") from exc
