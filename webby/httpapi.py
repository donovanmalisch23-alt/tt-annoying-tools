"""The HTTP surface: JSON API, server-sent events, and the built panel.

Routes (all JSON unless noted):

====  ==========================  ============================================
GET   /api/health                 liveness, config summary, whitelist + run state
GET   /api/tools                  the live tool catalog
GET   /api/whitelist              allowlist path/entries (+ raw text when authed)
PUT   /api/whitelist              **admin** replace the allowlist file
POST  /api/admin/login            username + password -> bearer token
POST  /api/admin/logout           **admin** drop the token
GET   /api/admin/session          whether the presented token is still valid
POST  /api/runs                   start a tool run (gated, optionally admin-only)
GET   /api/runs/current           the active or most recent run
GET   /api/runs/<id>/log?since=N  buffered output lines after N
POST  /api/runs/stop              SIGINT the active run
GET   /api/events                 server-sent events: state + log
====  ==========================  ============================================
"""

from __future__ import annotations

import json
import mimetypes
import os
import queue
import socket
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import __version__
from .admin import AdminError, AdminStore, LoginThrottle, SessionStore
from .config import BridgeConfig
from .runner import RunBusyError, RunManager, RunNotFoundError
from .tools import (
    ToolRequestError,
    build_request,
    require_allowed_ok,
    tool_catalog,
)
from .whitelist import WhitelistError, WhitelistFile, read as read_whitelist, write as write_whitelist

JSON_CT = "application/json; charset=utf-8"
SSE_CT = "text/event-stream; charset=utf-8"


class WebbyApp:
    """Shared state behind the handler: credentials, allowlist and runs."""

    def __init__(self, config: BridgeConfig):
        self.config = config
        self.admin = AdminStore(
            config.admin_path,
            env_user=os.environ.get("WEBBY_ADMIN_USER"),
            env_password=os.environ.get("WEBBY_ADMIN_PASSWORD"),
        )
        self.sessions = SessionStore(config.session_ttl)
        self.throttle = LoginThrottle()
        self.runs = RunManager(config)

    # ----- allowlist -------------------------------------------------------- #

    def whitelist(self) -> WhitelistFile:
        return read_whitelist(self.config.whitelist_path)

    def whitelist_public(self) -> dict[str, Any]:
        current = self.whitelist()
        return {
            "path": str(current.path),
            "entries": current.entries,
            "count": current.count,
            "exists": current.exists,
            "mtime": current.mtime,
        }

    # ----- health ---------------------------------------------------------- #

    def health(self) -> dict[str, Any]:
        whitelist = self.whitelist_public()
        record = None
        try:
            record = self.admin.load()
        except AdminError:
            record = None
        return {
            "ok": True,
            "service": "webby",
            "version": __version__,
            "mode": "live",
            "time": time.time(),
            "config": self.config.describe(),
            "whitelist": whitelist,
            "admin": {
                "configured": self.admin.configured,
                "username": record.username if record else None,
                "source": "env" if self.admin.uses_env else ("file" if self.admin.configured else "none"),
            },
            "run": self.runs.snapshot(),
            "tools": [tool["id"] for tool in tool_catalog()],
        }


class WebbyHandler(BaseHTTPRequestHandler):
    server_version = f"webby/{__version__}"
    protocol_version = "HTTP/1.1"

    app: WebbyApp  # injected by build_server

    # ----- plumbing --------------------------------------------------------- #

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        # Quiet by default: the run log is the interesting output, not every GET.
        if os.environ.get("WEBBY_HTTP_LOG"):
            super().log_message(format, *args)

    def _origin(self) -> str | None:
        return self.headers.get("Origin")

    def _cors_headers(self) -> None:
        origin = self._origin()
        allowed = self.app.config.allow_origins
        if not origin:
            return
        if "*" in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
        elif origin in allowed:
            self.send_header("Access-Control-Allow-Origin", origin)
        else:
            return
        self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, OPTIONS")

    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self' http://127.0.0.1:* http://localhost:*; "
            "worker-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        )

    def _send_json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", JSON_CT)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_error_json(self, status: int, message: str, **extra: Any) -> None:
        self._send_json(status, {"error": message, **extra})

    def _read_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return {}
        if length > 1_000_000:
            raise ValueError("request body is too large")
        raw = self.rfile.read(length)
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValueError(f"body is not valid JSON: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("body must be a JSON object")
        return parsed

    def _token(self) -> str | None:
        header = self.headers.get("Authorization") or ""
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return None

    def _session(self):
        return self.app.sessions.validate(self._token())

    def _client_key(self) -> str:
        return self.client_address[0] if self.client_address else "unknown"

    # ----- routing --------------------------------------------------------- #

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib naming
        self.send_response(HTTPStatus.NO_CONTENT)
        self._cors_headers()
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path == "/api/health":
                return self._send_json(HTTPStatus.OK, self.app.health())
            if path == "/api/tools":
                return self._send_json(HTTPStatus.OK, {"tools": tool_catalog()})
            if path == "/api/whitelist":
                payload = self.app.whitelist_public()
                if self._session() is not None:
                    payload["raw"] = self.app.whitelist().raw
                return self._send_json(HTTPStatus.OK, payload)
            if path == "/api/admin/session":
                session = self._session()
                if session is None:
                    return self._send_json(HTTPStatus.OK, {"authenticated": False})
                return self._send_json(
                    HTTPStatus.OK,
                    {
                        "authenticated": True,
                        "username": session.username,
                        "expires_at": session.expires_at,
                    },
                )
            if path == "/api/runs/current":
                snapshot = self.app.runs.snapshot()
                return self._send_json(HTTPStatus.OK, {"run": snapshot})
            if path.startswith("/api/runs/") and path.endswith("/log"):
                run_id = path[len("/api/runs/") : -len("/log")]
                since = int((query.get("since") or ["0"])[0])
                summary, lines, next_index = self.app.runs.log(run_id, since)
                return self._send_json(
                    HTTPStatus.OK, {"run": summary, "lines": lines, "next": next_index}
                )
            if path == "/api/events":
                return self._stream_events()
            if path.startswith("/api/"):
                return self._send_error_json(HTTPStatus.NOT_FOUND, "no such API route")
            return self._serve_static(path)
        except RunNotFoundError:
            self._send_error_json(HTTPStatus.NOT_FOUND, "no such run")
        except ValueError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except WhitelistError as exc:
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"bridge error: {exc}")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))

        try:
            if path == "/api/admin/login":
                return self._handle_login(body)
            if path == "/api/admin/logout":
                token = self._token()
                if token:
                    self.app.sessions.revoke(token)
                return self._send_json(HTTPStatus.OK, {"ok": True})
            if path == "/api/runs":
                return self._handle_start_run(body)
            if path == "/api/runs/stop":
                run = self.app.runs.stop()
                if run is None:
                    return self._send_error_json(
                        HTTPStatus.CONFLICT, "no run is currently active"
                    )
                return self._send_json(HTTPStatus.OK, {"run": run.summary()})
            return self._send_error_json(HTTPStatus.NOT_FOUND, "no such API route")
        except RunBusyError as exc:
            self._send_error_json(HTTPStatus.CONFLICT, str(exc))
        except ToolRequestError as exc:
            self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        except Exception as exc:  # pragma: no cover - last-resort guard
            self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, f"bridge error: {exc}")

    def do_PUT(self) -> None:  # noqa: N802
        path = unquote(urlparse(self.path).path)
        try:
            body = self._read_json()
        except ValueError as exc:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        if path != "/api/whitelist":
            return self._send_error_json(HTTPStatus.NOT_FOUND, "no such API route")

        session = self._session()
        if session is None:
            return self._send_error_json(
                HTTPStatus.UNAUTHORIZED,
                "editing the allowlist needs an admin session; sign in first",
            )
        raw = body.get("raw")
        if not isinstance(raw, str):
            return self._send_error_json(HTTPStatus.BAD_REQUEST, "raw (the file text) is required")
        try:
            current = self.app.whitelist()
            if not body.get("force") and "mtime" in body:
                try:
                    expected = float(body["mtime"])
                except (TypeError, ValueError):
                    expected = current.mtime
                if current.exists and abs(current.mtime - expected) > 1e-6:
                    return self._send_error_json(
                        HTTPStatus.CONFLICT,
                        "the allowlist changed on disk since you loaded it; reload and re-apply",
                        mtime=current.mtime,
                    )
            updated = write_whitelist(self.app.config.whitelist_path, raw)
        except WhitelistError as exc:
            return self._send_error_json(HTTPStatus.BAD_REQUEST, str(exc))
        payload = {
            "path": str(updated.path),
            "entries": updated.entries,
            "count": updated.count,
            "exists": updated.exists,
            "mtime": updated.mtime,
            "raw": updated.raw,
            "by": session.username,
        }
        if updated.count == 0:
            payload["warning"] = "the allowlist is now empty; every gated tool will refuse to run"
        return self._send_json(HTTPStatus.OK, payload)

    # ----- handlers -------------------------------------------------------- #

    def _handle_login(self, body: dict[str, Any]) -> None:
        key = self._client_key()
        blocked = self.app.throttle.blocked_for(key)
        if blocked > 0:
            return self._send_error_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                f"too many failed sign-ins; try again in {blocked:.0f}s",
            )
        username = str(body.get("username") or "")
        password = str(body.get("password") or "")
        if not self.app.admin.configured:
            return self._send_error_json(
                HTTPStatus.UNAUTHORIZED,
                "no admin credential is configured; run ./run_webby.sh admin set",
            )
        try:
            ok = self.app.admin.verify(username, password)
        except AdminError as exc:
            return self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, str(exc))
        if not ok:
            self.app.throttle.record_failure(key)
            return self._send_error_json(HTTPStatus.UNAUTHORIZED, "wrong username or password")
        self.app.throttle.record_success(key)
        session = self.app.sessions.issue(username)
        return self._send_json(
            HTTPStatus.OK,
            {
                "token": session.token,
                "username": session.username,
                "expires_at": session.expires_at,
                "ttl": self.app.config.session_ttl,
            },
        )

    def _handle_start_run(self, body: dict[str, Any]) -> None:
        if self.app.config.require_admin_for_runs and self._session() is None:
            return self._send_error_json(
                HTTPStatus.UNAUTHORIZED, "this bridge requires an admin session to start runs"
            )
        tool_id = str(body.get("tool") or "")
        params = body.get("params") or {}
        connection = body.get("connection") or {}
        if not isinstance(params, dict) or not isinstance(connection, dict):
            return self._send_error_json(HTTPStatus.BAD_REQUEST, "params/connection must be objects")
        confirm = bool(body.get("confirm"))

        argv, env, warnings = build_request(
            self.app.config, tool_id, params, connection, confirm=confirm
        )
        # The tools enforce the allowlist themselves; failing here as well means a
        # clear answer before a process is ever spawned.
        require_allowed_ok(self.app.config, tool_id, argv, self.app.whitelist(), warnings)

        run = self.app.runs.start(
            tool_from_id(tool_id), argv, env, note="; ".join(warnings)
        )
        return self._send_json(HTTPStatus.ACCEPTED, {"run": run.summary()})

    # ----- server-sent events --------------------------------------------- #

    def _stream_events(self) -> None:
        channel = self.app.runs.subscribe()
        self.close_connection = True
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", SSE_CT)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self._cors_headers()
        self.end_headers()
        try:
            for event in self.app.runs.initial_events():
                self._write_sse(event)
            while True:
                try:
                    event = channel.get(timeout=15.0)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                self._write_sse(event)
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            pass
        finally:
            self.app.runs.unsubscribe(channel)

    def _write_sse(self, event: dict[str, Any]) -> None:
        payload = json.dumps(event["data"])
        self.wfile.write(f"event: {event['event']}\ndata: {payload}\n\n".encode("utf-8"))
        self.wfile.flush()

    # ----- static files ---------------------------------------------------- #

    def _serve_static(self, path: str) -> None:
        dist = self.app.config.dist_dir
        index = dist / "index.html"
        if not index.is_file():
            return self._serve_unbuilt_page()

        relative = path.lstrip("/") or "index.html"
        candidate = (dist / relative).resolve()
        try:
            candidate.relative_to(dist.resolve())
        except ValueError:
            return self._send_error_json(HTTPStatus.FORBIDDEN, "path escape refused")

        if candidate.is_dir() or not candidate.is_file():
            if "." in Path(relative).name:
                return self._send_error_json(HTTPStatus.NOT_FOUND, "not found")
            candidate = index  # SPA fallback

        try:
            body = candidate.read_bytes()
        except OSError:
            return self._send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "cannot read asset")

        content_type, _ = mimetypes.guess_type(str(candidate))
        if candidate.name == "index.html":
            content_type = "text/html; charset=utf-8"
        elif candidate.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif candidate.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif candidate.suffix == ".webmanifest":
            content_type = "application/manifest+json"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache" if candidate == index else "public, max-age=300")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _serve_unbuilt_page(self) -> None:
        body = (
            "<!doctype html><meta charset='utf-8'>"
            "<title>Webby — panel not built</title>"
            "<body style='font:16px system-ui;background:#05080e;color:#e8eefb;padding:40px'>"
            "<h1>Webby is running, but the panel is not built yet.</h1>"
            "<p>Build it once, then reload this page:</p>"
            "<pre style='background:#101a29;padding:12px;border-radius:8px'>"
            "cd " + str(self.app.config.repo_root) + " &amp;&amp; bun install &amp;&amp; bun run build</pre>"
            "<p>The API is live meanwhile: <a style='color:#2fd4c0' href='/api/health'>/api/health</a></p>"
        ).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        self.wfile.write(body)


def tool_from_id(tool_id: str):
    from .tools import TOOLS_BY_ID

    tool = TOOLS_BY_ID.get(tool_id)
    if tool is None:
        raise ToolRequestError(f"unknown tool {tool_id!r}")
    return tool


class WebbyServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    address_family = socket.AF_INET6 if socket.has_ipv6 and os.environ.get("WEBBY_IPV6") else socket.AF_INET

    def __init__(self, address: tuple[str, int], app: WebbyApp):
        handler = type("BoundWebbyHandler", (WebbyHandler,), {"app": app})
        self.app = app
        super().__init__(address, handler)


def build_server(config: BridgeConfig) -> WebbyServer:
    app = WebbyApp(config)
    server = WebbyServer((config.host, config.port), app)
    return server
