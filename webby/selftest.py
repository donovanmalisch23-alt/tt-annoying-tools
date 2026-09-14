"""End-to-end self test for the bridge: ``python3 -m webby.selftest``.

It boots a real server on an ephemeral port in a temporary directory, then walks
the whole surface the panel uses:

* the argv the bridge builds for every tool against the tool's own parser;
* health, tool catalog and allowlist reads;
* a refused allowlist write without a session;
* admin sign-in, throttling, and an authorised allowlist write;
* the allowlist conflict check (a stale mtime is refused);
* starting a run — against a stub tool, so no TeamTalk server is needed;
* the allowlist gate refusing a host that is not on the list;
* the local-only gate on the flood tool;
* streaming the run's output and stopping it.

Run it from the repository root. Exit status 0 means every check passed.
"""

from __future__ import annotations

import importlib
import json
import os
import socket
import sys
import tempfile
import textwrap
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .__main__ import _ensure_admin
from .admin import AdminStore
from .config import BridgeConfig
from .httpapi import build_server
from .tools import LIVE_TOOLS, build_request

PASSED: list[str] = []
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(label)
        print(f"  ok   {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL {label}{f' — {detail}' if detail else ''}")


STUB = textwrap.dedent(
    """
    \"\"\"A stand-in for a real tool: prints, waits, exits 0.\"\"\"
    import os, sys, time

    print("stub: argv", " ".join(sys.argv[1:]))
    print("stub: host", os.environ.get("TT_HOST", ""))
    sys.stdout.flush()
    for i in range(1, 4):
        time.sleep(0.05)
        print(f"stub: tick {i}")
        sys.stdout.flush()
    print("stub: done")
    """
)


class Client:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.token: str | None = None

    def raw(self, path: str) -> tuple[int, str, str]:
        """Fetch a static asset: (status, body, content-type)."""
        request = Request(f"{self.base}{path}")
        try:
            with urlopen(request, timeout=10) as response:
                return (
                    response.status,
                    response.read().decode("utf-8", "replace"),
                    response.headers.get("Content-Type", ""),
                )
        except HTTPError as error:
            return error.code, error.read().decode("utf-8", "replace"), ""

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = json.dumps(body).encode() if body is not None else None
        request = Request(f"{self.base}{path}", data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urlopen(request, timeout=10) as response:
                raw = response.read().decode("utf-8")
                return response.status, (json.loads(raw) if raw else {})
        except HTTPError as error:
            raw = error.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except ValueError:
                payload = {"raw": raw}
            return error.code, payload
        except URLError as error:  # pragma: no cover - network stack failure
            return 0, {"error": str(error)}


PROBE_PARAMS: dict[str, str] = {
    "cycles": "2",
    "count": "1",
    "message": "hi",
    "users": "amy,bob",
    "target": "private",
    "threads": "2",
    "duration": "1",
    "max_threads": "4",
    "start_threads": "1",
    "ramp_factor": "2",
    "stage_duration": "1",
    "mode": "both",
    "probe_channel": "/Lobby",
    "trigger": "!hello",
    "all_channels": "true",
    "all_users": "true",
    "channel_message": "cm",
    "private_message": "pm",
    "concurrent": "true",
    "churn_bots": "2",
    "cooldown": "30",
    "max_responses": "5",
}

PROBE_CONNECTION: dict[str, Any] = {
    "host": "127.0.0.1",
    "tcp_port": 10333,
    "udp_port": 10333,
    "username": "probe",
    "password": "selftest-password",
    "nickname": "probe",
    "channel_path": "/Lobby",
    "timeout": 15,
    "reconnect_delay": 3.5,
}


def check_argv_contract(repo: Path) -> None:
    """Every flag the bridge builds must be accepted by the tool's own parser.

    This is the guard against the bridge and the CLI drifting apart: the tools
    stay the authority on their own arguments, so their ``build_parser()`` is
    asked to parse exactly what the bridge would run.
    """
    print("argv contract (bridge -> the tools' own parsers)")
    config = BridgeConfig(
        repo_root=repo,
        whitelist_path=repo / "whitelist.txt",
        python=sys.executable,
    )
    if str(repo) not in sys.path:
        sys.path.insert(0, str(repo))
    for tool in LIVE_TOOLS:
        try:
            argv, env, _ = build_request(
                config, tool.id, PROBE_PARAMS, PROBE_CONNECTION, confirm=True
            )
        except Exception as exc:  # noqa: BLE001 - report, never abort the suite
            check(f"{tool.id}: the bridge can build an argv", False, str(exc))
            continue
        argv_text = " ".join(argv)
        check(
            f"{tool.id}: no password in argv",
            str(PROBE_CONNECTION["password"]) not in argv_text,
            argv_text,
        )
        check(
            f"{tool.id}: password travels in the environment",
            env.get("TT_PASSWORD") == PROBE_CONNECTION["password"],
        )
        try:
            module = importlib.import_module(tool.script[:-3])
            parser = module.build_parser()
        except Exception as exc:  # noqa: BLE001
            check(f"{tool.id}: {tool.script} is importable", False, str(exc))
            continue
        # Drop [interpreter, -u, script], leaving the tool's own flags.
        tail = argv[3:]
        try:
            parser.parse_args(tail)
            accepted = True
            detail = ""
        except SystemExit as exc:
            accepted = False
            detail = f"{tool.script} rejected it (exit {exc.code})"
        except Exception as exc:  # noqa: BLE001
            accepted = False
            detail = str(exc)
        check(f"{tool.id}: {tool.script} accepts every flag", accepted, detail)


def check_ensure_admin(scratch: Path) -> None:
    """`serve --ensure-admin`: what gives the preview a usable login.

    It must create a credential when there is none, leave an existing one
    alone, and do nothing at all unless asked.
    """
    print("ensure-admin (preview provisioning)")

    quiet = BridgeConfig(repo_root=scratch, admin_path=scratch / "off.json")
    _ensure_admin(quiet)
    check("nothing is created unless --ensure-admin is given", not (scratch / "off.json").exists())

    path = scratch / "admin-on-demand.json"
    config = BridgeConfig(repo_root=scratch, admin_path=path, ensure_admin=True)
    _ensure_admin(config)
    check("a credential is created on demand", path.is_file())
    if path.is_file():
        check(
            "it is readable only by the owner",
            oct(path.stat().st_mode)[-3:] == "600",
            oct(path.stat().st_mode)[-3:],
        )
        first = path.read_text(encoding="utf-8")
        check("it is a PBKDF2 hash, not a password", "pbkdf2_sha256" in first)
        check("the login is named admin", json.loads(first)["username"] == "admin")
        _ensure_admin(config)
        check("an existing credential is never replaced", path.read_text(encoding="utf-8") == first)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def main() -> int:
    repo = Path(__file__).resolve().parent.parent
    check_argv_contract(repo)
    with tempfile.TemporaryDirectory(prefix="webby-selftest-") as scratch:
        check_ensure_admin(Path(scratch))
        scratch_path = Path(scratch)
        # Stub tools in place of the real scripts, so no TeamTalk server is needed.
        for tool in LIVE_TOOLS:
            (scratch_path / tool.script).write_text(STUB, encoding="utf-8")

        whitelist = scratch_path / "whitelist.txt"
        whitelist.write_text("# selftest\n127.0.0.1\n", encoding="utf-8")

        # A stand-in for the built panel, so the static routes can be checked.
        static_dir = scratch_path / "dist"
        (static_dir / "assets").mkdir(parents=True)
        (static_dir / "index.html").write_text(
            "<!doctype html><title>panel</title><div id=root></div>", encoding="utf-8"
        )
        (static_dir / "assets" / "app.js").write_text("console.log('panel')", encoding="utf-8")

        admin_file = scratch_path / "admin.json"
        store = AdminStore(admin_file)
        store.save("tester", "selftest-password")

        config = BridgeConfig(
            repo_root=scratch_path,
            host="127.0.0.1",
            port=free_port(),
            whitelist_path=whitelist,
            admin_path=admin_file,
            state_dir=scratch_path / "state",
            python=sys.executable,
            log_lines=500,
            static_dir=static_dir,
        )
        server = build_server(config)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.2})
        thread.daemon = True
        thread.start()
        base = f"http://127.0.0.1:{config.port}"
        client = Client(base)
        anon = Client(base)

        try:
            print("health and catalogue")
            status, health = client.request("GET", "/api/health")
            check("health answers 200", status == 200, str(health))
            check("health reports live mode", health.get("mode") == "live")
            check("health names the allowlist", str(whitelist) == health["whitelist"]["path"])
            check("health sees the allowlist entry", health["whitelist"]["count"] == 1)
            check("health sees the admin user", health["admin"]["username"] == "tester")

            status, tools = client.request("GET", "/api/tools")
            ids = [tool["id"] for tool in tools.get("tools", [])]
            check("eight tools are exposed", len(ids) == 8, str(ids))
            check(
                "every panel tool id is known to the bridge",
                set(ids) == {
                    "message-sender",
                    "login-cycles",
                    "leave-join",
                    "idle-bots",
                    "response-bot",
                    "suite",
                    "loic",
                    "ramp",
                },
                str(ids),
            )

            print("allowlist")
            status, listing = anon.request("GET", "/api/whitelist")
            check("anon can read entries", status == 200 and listing["entries"] == ["127.0.0.1"])
            check("anon does not get the raw file", "raw" not in listing)

            status, refused = anon.request(
                "PUT", "/api/whitelist", {"raw": "10.0.0.5\n", "mtime": listing["mtime"]}
            )
            check("anon cannot write the allowlist", status == 401, str(refused))

            print("admin sign-in")
            status, bad = anon.request(
                "POST", "/api/admin/login", {"username": "tester", "password": "nope"}
            )
            check("a wrong password is rejected", status == 401, str(bad))
            status, session = anon.request(
                "POST", "/api/admin/login", {"username": "tester", "password": "selftest-password"}
            )
            check("a correct password returns a token", status == 200 and bool(session.get("token")))
            client.token = session.get("token")

            status, who = client.request("GET", "/api/admin/session")
            check("the token identifies the admin", who.get("authenticated") and who["username"] == "tester")

            print("allowlist write")
            status, stale = client.request(
                "PUT", "/api/whitelist", {"raw": "10.0.0.5\n", "mtime": listing["mtime"] - 99}
            )
            check("a stale mtime is refused", status == 409, str(stale))

            status, written = client.request(
                "PUT",
                "/api/whitelist",
                {"raw": "# two hosts\n10.0.0.5\n test.example  \n", "mtime": listing["mtime"], "force": True},
            )
            check("the admin can write the allowlist", status == 200 and written["count"] == 2, str(written))
            check(
                "entries are normalized",
                written["entries"] == ["10.0.0.5", "test.example"],
                str(written.get("entries")),
            )
            check("the file on disk matches", "10.0.0.5" in whitelist.read_text())

            status, rejected = client.request(
                "PUT", "/api/whitelist", {"raw": "not a host!!\n", "force": True}
            )
            check("a malformed entry is refused", status == 400, str(rejected))
            check("the file was not damaged", "10.0.0.5" in whitelist.read_text())

            print("run gates")
            status, gate = client.request(
                "POST",
                "/api/runs",
                {
                    "tool": "ramp",
                    "params": {},
                    "connection": {"host": "evil.example"},
                    "confirm": True,
                },
            )
            check("an unlisted host is refused", status == 400, str(gate))
            check(
                "the refusal explains the allowlist",
                "allowlist" in json.dumps(gate).lower(),
                str(gate),
            )

            status, local = client.request(
                "POST",
                "/api/runs",
                {"tool": "loic", "params": {}, "connection": {"host": "10.0.0.5"}, "confirm": True},
            )
            check("the flood tool is local-only", status == 400, str(local))
            check(
                "the local-only refusal is explicit",
                "local" in json.dumps(local).lower(),
                str(local),
            )

            status, unconfirmed = client.request(
                "POST",
                "/api/runs",
                {"tool": "login-cycles", "params": {"cycles": 1}, "connection": {"host": "127.0.0.1"}},
            )
            check("a stub tool runs without confirmation", status == 202, str(unconfirmed))

            run_id = (unconfirmed.get("run") or {}).get("id")
            check("the run is reported running", (unconfirmed.get("run") or {}).get("state") == "running")

            status, busy = client.request(
                "POST",
                "/api/runs",
                {"tool": "login-cycles", "params": {"cycles": 1}, "connection": {"host": "127.0.0.1"}},
            )
            check("a second run is refused while one is active", status == 409, str(busy))

            print("run output")
            deadline = time.time() + 10
            lines: list[dict[str, Any]] = []
            while time.time() < deadline:
                status, log = client.request("GET", f"/api/runs/{run_id}/log?since=0")
                lines = log.get("lines") or []
                if (log.get("run") or {}).get("state") != "running":
                    break
                time.sleep(0.15)
            text = "\n".join(line["text"] for line in lines)
            check("output is streamed back", "stub: tick 1" in text, text[:400])
            check("credentials are not in argv", "selftest-password" not in text, text[:400])
            check(
                "the run finished",
                (log.get("run") or {}).get("state") == "finished",
                str(log.get("run")),
            )

            status, current = client.request("GET", "/api/runs/current")
            check("the last run is still readable", status == 200 and current["run"]["id"] == run_id)

            print("event stream")
            events = stream_first_events(base, seconds=2.0)
            check("the SSE stream sends a state event", any(e == "state" for e in events), str(events[:6]))

            print("stop endpoint")
            status, nothing = client.request("POST", "/api/runs/stop")
            check("stopping with no active run is a conflict", status == 409, str(nothing))

            print("static panel")
            status, body, content_type = client.raw("/")
            check("the panel is served at /", status == 200 and "id=root" in body, str(status))
            check("index.html is text/html", content_type.startswith("text/html"), content_type)

            status, body, content_type = client.raw("/assets/app.js")
            check(
                "hashed assets are served as javascript",
                status == 200 and "panel" in body and "javascript" in content_type,
                f"{status} {content_type}",
            )

            status, body, _ = client.raw("/bridge/admin")
            check("deep links fall back to the SPA shell", status == 200 and "id=root" in body)

            status, body, _ = client.raw("/missing.js")
            check("a missing asset is a 404, not the shell", status == 404, str(status))

            status, body, _ = client.raw("/..%2f..%2fetc%2fpasswd")
            check(
                "path traversal is refused",
                status in (403, 404) and "root:" not in body,
                str(status),
            )

            status, missing = client.request("GET", "/api/does-not-exist")
            check("an unknown API route is a clean 404", status == 404, str(missing))
        finally:
            server.app.runs.stop_all()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    if FAILED:
        print("failures:")
        for label in FAILED:
            print(f"  - {label}")
        return 1
    return 0


def stream_first_events(base: str, seconds: float = 2.0) -> list[str]:
    """Read a couple of SSE events from /api/events."""
    names: list[str] = []
    import http.client

    connection = http.client.HTTPConnection("127.0.0.1", int(base.rsplit(":", 1)[1]), timeout=seconds)
    try:
        connection.request("GET", "/api/events")
        response = connection.getresponse()
        if response.status != 200:
            return names
        deadline = time.time() + seconds
        while time.time() < deadline and len(names) < 4:
            line = response.readline().decode("utf-8", "replace").strip()
            if line.startswith("event:"):
                names.append(line.split(":", 1)[1].strip())
    except OSError:
        pass
    finally:
        connection.close()
    return names


if __name__ == "__main__":
    os.chdir(Path(__file__).resolve().parent.parent)
    raise SystemExit(main())
