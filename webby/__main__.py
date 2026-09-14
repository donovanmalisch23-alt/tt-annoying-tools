"""Command line entry point: ``python3 -m webby <command>``.

Commands
--------
``serve`` (default)
    Run the bridge in the foreground: serve the built panel from ``dist/`` and
    answer the JSON API. ``run_webby.sh start`` runs this in the background.
``status``
    Report whether a bridge is running for this repository and, if so, whether
    its ``/api/health`` endpoint answers.
``stop``
    Ask the running bridge to shut down (SIGTERM, then SIGKILL after a grace
    period) and remove its pid file.
``admin set`` / ``admin show``
    Create or inspect the admin credential used to edit the allowlist.

The pid file lives in the state directory (``.webby/webby.pid`` by default) and
records the address the bridge is bound to, so status/stop and the shell script
never have to guess.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from . import __version__
from .admin import AdminError, AdminStore, MIN_PASSWORD_LENGTH, generate_password
from .config import BridgeConfig, config_from_args
from .httpapi import build_server

PID_FILENAME = "webby.pid"
STOP_GRACE_SECONDS = 8.0


# --- pid file --------------------------------------------------------------- #


def pid_path(config: BridgeConfig) -> Path:
    return config.state_dir / PID_FILENAME


def read_pid(config: BridgeConfig) -> dict[str, Any] | None:
    path = pid_path(config)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, ValueError):
        return None
    if not isinstance(payload, dict) or not payload.get("pid"):
        return None
    return payload


def write_pid(config: BridgeConfig) -> Path:
    path = pid_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "host": config.host,
        "port": config.port,
        "url": f"http://{display_host(config.host)}:{config.port}/",
        "repo_root": str(config.repo_root),
        "whitelist": str(config.whitelist_path),
        "version": __version__,
        "started_at": time.time(),
        "log": str(config.state_dir / "webby.log"),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def clear_pid(config: BridgeConfig) -> None:
    try:
        pid_path(config).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def display_host(host: str) -> str:
    """A host you can actually paste into a browser."""
    if host in ("0.0.0.0", "::", ""):
        return "127.0.0.1"
    return host


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# --- commands --------------------------------------------------------------- #


def command_serve(config: BridgeConfig, *, quiet: bool = False) -> int:
    existing = read_pid(config)
    if existing and process_alive(int(existing["pid"])) and int(existing["pid"]) != os.getpid():
        print(
            f"webby: already serving from this repository (pid {existing['pid']}, "
            f"{existing.get('url')}). Use './run_webby.sh restart' if that is not what you want.",
            file=sys.stderr,
        )
        return 1

    try:
        server = build_server(config)
    except OSError as exc:
        print(
            f"webby: cannot bind {config.host}:{config.port} ({exc}). "
            "Try './run_webby.sh start --port 8788'.",
            file=sys.stderr,
        )
        return 1

    _ensure_admin(config)

    write_pid(config)
    url = f"http://{display_host(config.host)}:{config.port}/"
    if not quiet:
        print(f"webby {__version__} serving the panel at {url}")
        print(f"  repo        {config.repo_root}")
        print(f"  allowlist   {config.whitelist_path}")
        print(f"  admin file  {config.admin_path}")
        print(f"  tools       {len(_tool_ids())} live tool(s)")
        if not (config.dist_dir / "index.html").is_file():
            print("  panel       not built yet — run './run_webby.sh build'")
        print("  stop with   ./run_webby.sh stop")
        sys.stdout.flush()

    stopping = threading.Event()

    def handle_signal(signum: int, _frame: Any) -> None:
        if not quiet:
            print(f"\nwebby: signal {signum}; shutting down")

        stopping.set()

    for number in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(number, handle_signal)
        except (ValueError, OSError):  # not the main thread, or unsupported
            pass

    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.4}, daemon=True
    )
    thread.start()
    try:
        while not stopping.is_set():
            stopping.wait(1.0)
    except KeyboardInterrupt:  # pragma: no cover - depends on the terminal
        pass
    finally:
        server.app.runs.stop_all()
        server.shutdown()
        server.server_close()
        clear_pid(config)
        if not quiet:
            print("webby: stopped")
    return 0


def _tool_ids() -> list[str]:
    from .tools import tool_catalog

    return [tool["id"] for tool in tool_catalog()]


def _ensure_admin(config: BridgeConfig) -> None:
    """Create an admin credential on start when asked and none exists.

    This is what makes the preview usable with no second terminal: the password
    is printed exactly once, into the dev server's log, and only a PBKDF2 hash is
    kept on disk. It never replaces an existing credential.
    """
    if not config.ensure_admin:
        return
    store = AdminStore(config.admin_path)
    if store.uses_env:
        return
    if store.configured:
        print(f"webby: admin login is already configured ({config.admin_path})")
        return
    password = generate_password()
    try:
        record = store.save("admin", password, force=False)
    except AdminError as exc:
        print(f"webby: could not create an admin credential: {exc}", file=sys.stderr)
        return
    print()
    print("=" * 68)
    print("  Webby admin login created for this machine")
    print(f"    username  {record.username}")
    print(f"    password  {password}")
    print("  Printed once: only a PBKDF2 hash is stored on disk. Change it")
    print("  any time with ./run_webby.sh admin set --username admin")
    print("=" * 68)
    print()
    sys.stdout.flush()


def command_status(config: BridgeConfig) -> int:
    record = read_pid(config)
    if record is None:
        print("webby: not running (no pid file)")
        _print_targets(config)
        return 1
    pid = int(record["pid"])
    if not process_alive(pid):
        print(f"webby: pid {pid} is gone; the bridge is not running (stale pid file)")
        clear_pid(config)
        return 1
    print(f"webby: running (pid {pid}) at {record.get('url')}")
    health = probe_health(config, record)
    if health is None:
        print("  health      no answer (still starting, or wedged)")
        return 1
    print(f"  version     {health.get('version')}")
    whitelist = health.get("whitelist") or {}
    print(f"  allowlist   {whitelist.get('count', 0)} entr(ies) in {whitelist.get('path')}")
    admin = health.get("admin") or {}
    print(f"  admin       {admin.get('source')} ({admin.get('username') or 'not set'})")
    run = health.get("run")
    if run:
        print(f"  run         {run.get('label')} [{run.get('state')}] on {run.get('host')}")
    else:
        print("  run         idle")
    _print_targets(config)
    return 0


def probe_health(config: BridgeConfig, record: dict[str, Any] | None = None) -> dict[str, Any] | None:
    host = display_host((record or {}).get("host") or config.host)
    port = int((record or {}).get("port") or config.port)
    try:
        with urlopen(f"http://{host}:{port}/api/health", timeout=4) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (URLError, OSError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _print_targets(config: BridgeConfig) -> None:
    print(f"  repo        {config.repo_root}")
    print(f"  allowlist   {config.whitelist_path}")
    print(f"  admin file  {config.admin_path}")


def command_stop(config: BridgeConfig) -> int:
    record = read_pid(config)
    if record is None:
        print("webby: not running (no pid file)")
        return 0
    pid = int(record["pid"])
    if not process_alive(pid):
        print("webby: stale pid file; cleaned up")
        clear_pid(config)
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"webby: cannot signal pid {pid}: {exc}", file=sys.stderr)
        return 1
    deadline = time.time() + STOP_GRACE_SECONDS
    while time.time() < deadline:
        if not process_alive(pid):
            print(f"webby: stopped (pid {pid})")
            clear_pid(config)
            return 0
        time.sleep(0.2)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    print(f"webby: pid {pid} did not exit in {STOP_GRACE_SECONDS:g}s; killed it")
    clear_pid(config)
    return 0


def command_admin(config: BridgeConfig, action: str, args: Any) -> int:
    store = AdminStore(config.admin_path)
    if action == "show":
        record = store.load()
        if record is None:
            print("webby: no admin credential is configured")
            print("  create one with: ./run_webby.sh admin set --username admin")
            return 1
        print(f"webby: admin user '{record.username}'")
        print(f"  file        {config.admin_path}")
        print(f"  updated     {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(record.updated_at))}")
        print("  the password is stored as a PBKDF2 hash and cannot be shown.")
        return 0

    if action != "set":
        print(f"webby: unknown admin action {action!r} (use set or show)", file=sys.stderr)
        return 2

    username = (args.username or "admin").strip() or "admin"
    password = args.password
    generated = False
    if not password and args.generate:
        password = generate_password()
        generated = True
    if not password:
        if not sys.stdin.isatty():
            print(
                "webby: no password given and stdin is not a terminal; "
                "pass --password or --generate",
                file=sys.stderr,
            )
            return 2
        import getpass

        first = getpass.getpass(f"password for '{username}' ({MIN_PASSWORD_LENGTH}+ chars): ")
        second = getpass.getpass("repeat: ")
        if first != second:
            print("webby: the passwords did not match", file=sys.stderr)
            return 2
        password = first
    try:
        store.save(username, password, force=bool(args.force))
    except AdminError as exc:
        print(f"webby: {exc}", file=sys.stderr)
        return 2
    print(f"webby: admin user '{username}' saved to {config.admin_path}")
    if generated:
        # Printed exactly once: only the hash is stored.
        print(f"webby: generated password — {password}")
        print("       write it down now; it cannot be recovered from the file.")
    print("       sign in from the panel's Bridge & admin tab.")
    return 0


def main(argv: list[str] | None = None) -> int:
    config, args = config_from_args(argv)
    command = args.command
    if command == "serve":
        return command_serve(config, quiet=bool(os.environ.get("WEBBY_QUIET")))
    if command == "status":
        return command_status(config)
    if command == "stop":
        return command_stop(config)
    if command == "admin":
        action = args.admin_action or "show"
        return command_admin(config, action, args)
    print(f"webby: unknown command {command!r}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
