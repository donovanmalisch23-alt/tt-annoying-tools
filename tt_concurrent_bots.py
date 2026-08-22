#!/usr/bin/env python3
"""Launch idle TeamTalk bots that connect, log in, and just sit there.

Each bot opens its own SDK connection, logs in, joins the configured channel
(or stays in the root channel when none is set), and then does nothing except
keep the connection alive.  The point is to occupy server slots: a wall of
silent, logged-in clients that clog the user roster and jam the server.

A single process can only sustain a limited number of SDK connections because
the native library drives each client with an ACE select() reactor that cannot
address file descriptors at or above FD_SETSIZE (1024).  That ceiling works out
to roughly 250 bots per process.  To reach the full 10,000-bot ceiling this
tool splits the requested bots into chunks and runs each chunk in its own
worker process, exactly like ``tt_suite.py --concurrent`` does.
"""

from __future__ import annotations

import argparse
import multiprocessing
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no resource module
    resource = None  # type: ignore[assignment]

from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    add_connection_arguments,
    comma_int,
    config_dir,
    config_from_args,
    kill_switch_triggered,
    print_tool_error,
    prompt_connection_config,
    prompt_int,
    prompt_yes_no,
)

PROJECT_DIR = config_dir()
DEFAULT_WHITELIST = PROJECT_DIR / "whitelist.txt"
MAX_BOTS = 10_000
DEFAULT_BOTS = 1


def _env_float(name: str, default: float) -> float:
    """Read a float environment variable, falling back on any error."""

    try:
        return float(os.environ.get(name, ""))
    except (TypeError, ValueError):
        return default


def normalize_host(host: str) -> str:
    """Normalize a host for exact whitelist comparison."""

    normalized = host.strip().casefold().rstrip(".")
    if normalized.startswith("[") and normalized.endswith("]"):
        normalized = normalized[1:-1]
    return normalized


def load_whitelist(path: Path) -> set[str]:
    """Load exact hostname/IP entries from a one-host-per-line file."""

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise TeamTalkConfigurationError(
            f"could not read server whitelist {path}: {exc}"
        ) from exc

    allowed = set()
    for line in lines:
        entry = line.split("#", 1)[0].strip()
        if entry:
            allowed.add(normalize_host(entry))
    if not allowed:
        raise TeamTalkConfigurationError(
            f"server whitelist {path} is empty; add one hostname or IP per line"
        )
    return allowed


def ensure_server_allowed(host: str, whitelist_path: Path) -> None:
    """Refuse to connect unless the server is in the whitelist."""

    allowed = load_whitelist(whitelist_path)
    normalized_host = normalize_host(host)
    if normalized_host not in allowed:
        raise TeamTalkConfigurationError(
            f"server {host!r} is not in the whitelist {whitelist_path}"
        )


def _max_concurrent_bots() -> int:
    """Upper bound on simultaneous SDK connections this process can sustain.

    Mirrors ``tt_suite._max_concurrent_bots``: the native ACE select() reactor
    cannot address file descriptors at or above FD_SETSIZE (1024).  Each SDK
    connection needs a TCP socket, a UDP socket, and a notification pipe (two
    FDs), so the ceiling is roughly (1024 - baseline) / fds_per_bot.
    """

    ceiling = 1024  # FD_SETSIZE in the native ACE select reactor
    if resource is not None:
        try:
            soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
            target = min(hard, ceiling)
            if soft < target:
                resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
                soft = target
            ceiling = min(soft, ceiling)
        except (OSError, ValueError):
            pass
    baseline = 16  # stdin/stdout/stderr + interpreter + margin
    fds_per_bot = 4  # TCP socket + UDP socket + notification pipe (2 FDs)
    return max(1, (ceiling - baseline) // fds_per_bot)


def _sit_idle(session: TeamTalkSession, stop_event: threading.Event) -> None:
    """Keep one connected bot parked on the server until told to stop.

    Periodically re-checks that the connection is still up so a kicked bot
    reconnects and keeps jamming the server, while the session's background
    event pump keeps draining the SDK queue (which lets the universal kill
    switch fire).
    """

    while not stop_event.is_set():
        if kill_switch_triggered():
            return
        if not session.is_online():
            if not session.check_and_reconnect():
                return
        stop_event.wait(1.0)


def _idle_bot(
    base_config: Any,
    index: int,
    stop_event: threading.Event,
) -> None:
    """Run one idle bot: connect, log in, join the channel, then sit forever.

    Connecting is retried a bounded number of times.  When thousands of bots
    launch at once the server is slow to accept every connection, so the first
    attempt can trip the SDK command timeout; a bot that dies instead of
    retrying would quietly shrink the wall until nothing is left.  Retrying
    with a delay lets the launch flood clear before the bot gives up.
    """

    config = replace(base_config, nickname=f"{base_config.nickname}-{index}")
    max_attempts = max(1, int(_env_float("TT_BOT_CONNECT_ATTEMPTS", 5.0)))
    retry_delay = _env_float("TT_BOT_RETRY_DELAY", 2.0)
    for attempt in range(max_attempts):
        if stop_event.is_set() or kill_switch_triggered():
            return
        try:
            with TeamTalkSession(config) as session:
                _sit_idle(session, stop_event)
            return
        except TeamTalkConfigurationError:
            return  # permanent misconfiguration; retrying cannot help
        except TeamTalkError:
            if attempt + 1 >= max_attempts:
                return
            stop_event.wait(retry_delay)


def _worker_process(
    config: Any,
    start_index: int,
    count: int,
    kill_event: Any,
    worker_index: int,
    worker_count: int,
) -> None:
    """Run one chunk of idle bots in threads inside a child process.

    Each worker process stays under the native library's FD_SETSIZE ceiling by
    running at most ``_max_concurrent_bots()`` bots.  A local kill-switch
    watcher mirrors the process-local kill switch into the shared ``kill_event``
    so the parent can stop every other worker the moment any bot receives the
    emergency-stop phrase.

    Bot threads are started with a short delay between them.  Without it every
    bot opens its connection in the same instant; the server's connect backlog
    floods, connections stall, and the whole run trips the 15s SDK command
    timeout and dies.  Staggering the starts spreads the burst so each bot
    connects cleanly.  Tune with ``TT_BOT_START_DELAY`` seconds.
    """

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass

    print(
        f"[worker {worker_index}/{worker_count}] launching {count} idle bot(s) "
        f"(indices {start_index}..{start_index + count - 1}).",
        flush=True,
    )

    stop_event = threading.Event()
    threads = [
        threading.Thread(
            target=_idle_bot,
            args=(config, start_index + offset, stop_event),
            daemon=True,
        )
        for offset in range(count)
    ]

    def _watcher() -> None:
        while not stop_event.is_set():
            if kill_switch_triggered():
                kill_event.set()
                stop_event.set()
                return
            stop_event.wait(0.25)

    threading.Thread(target=_watcher, name="kill-switch-watcher", daemon=True).start()

    start_delay = _env_float("TT_BOT_START_DELAY", 0.1)
    for thread in threads:
        thread.start()
        if start_delay > 0:
            stop_event.wait(start_delay)
    for thread in threads:
        thread.join()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Launch idle TeamTalk bots that connect, log in, and sit "
        "on the server doing nothing (clogging the user roster)."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--count",
        type=comma_int,
        default=DEFAULT_BOTS,
        help=f"number of idle bots to launch, 1..{MAX_BOTS} (default: {DEFAULT_BOTS}). "
        "Bots are split across worker processes because a single process can "
        "only sustain ~250 SDK connections.",
    )
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=Path(os.environ.get("TT_WHITELIST", DEFAULT_WHITELIST)),
        help=f"exact server allowlist (default: {DEFAULT_WHITELIST})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the launch plan without connecting or spawning any bots",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm the idle-bot launch (required unless --dry-run)",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not 1 <= args.count <= MAX_BOTS:
        raise TeamTalkConfigurationError(
            f"--count must be between 1 and {MAX_BOTS}"
        )


def _run(config: Any, args: argparse.Namespace) -> int:
    total_bots = args.count
    max_bots = _max_concurrent_bots()

    # Thousands of bots connecting at once swamp the server's connect backlog,
    # so connect/login can take far longer than the shared 15s SDK default.
    # Give the workers a generous floor (60s) for the initial ramp; an explicit
    # higher --timeout still wins.
    worker_timeout = max(config.command_timeout, 60.0)
    if worker_timeout != config.command_timeout:
        config = replace(config, command_timeout=worker_timeout)

    if total_bots <= max_bots:
        chunks = [total_bots]
    else:
        chunks = [max_bots] * (total_bots // max_bots)
        remainder = total_bots % max_bots
        if remainder:
            chunks.append(remainder)

    print(
        f"Idle-bot plan: {total_bots} bot(s) across {len(chunks)} worker "
        f"process(es) (up to {max_bots} each)."
    )
    for idx, chunk in enumerate(chunks, start=1):
        print(f"  worker {idx}/{len(chunks)}: {chunk} bot(s)")

    if args.dry_run:
        print("Dry run complete; no bots were spawned.")
        return 0

    if not args.confirm:
        raise TeamTalkConfigurationError(
            "--confirm is required to launch idle bots (use --dry-run to preview)"
        )

    kill_event = multiprocessing.Event()
    processes: list[multiprocessing.Process] = []
    start_index = 0
    for idx, chunk in enumerate(chunks, start=1):
        process = multiprocessing.Process(
            target=_worker_process,
            args=(config, start_index, chunk, kill_event, idx, len(chunks)),
            name=f"tt-idle-worker-{idx}",
            daemon=True,
        )
        processes.append(process)
        process.start()
        start_index += chunk

    def _kill_monitor() -> None:
        kill_event.wait()
        for process in processes:
            if process.is_alive():
                process.terminate()

    monitor = threading.Thread(target=_kill_monitor, name="kill-monitor", daemon=True)
    monitor.start()

    print(
        f"Launched {total_bots} idle bot(s). Press Ctrl+C to stop, or send the "
        "kill phrase to any bot to shut everything down."
    )

    try:
        for process in processes:
            process.join()
    except KeyboardInterrupt:
        print("\nInterrupted: terminating workers...")
        for process in processes:
            process.terminate()
        for process in processes:
            process.join(timeout=5.0)
        print("Stopped.")
        return 130

    if kill_event.is_set():
        print("[kill-switch] shutting down all workers.")
        return 0

    for process in processes:
        if process.exitcode not in (0, None):
            print(f"worker {process.name} exited with code {process.exitcode}.")
            return process.exitcode or 1

    print("All idle bots stopped.")
    return 0


def run(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    ensure_server_allowed(config.host, args.whitelist)
    return _run(config, args)


def interactive_run() -> int:
    config = prompt_connection_config(channel_required=False)
    count = prompt_int(
        "How many idle bots to launch?",
        DEFAULT_BOTS,
        minimum=1,
        maximum=MAX_BOTS,
    )
    whitelist = Path(
        os.environ.get("TT_WHITELIST", DEFAULT_WHITELIST)
    )
    ensure_server_allowed(config.host, whitelist)
    if not prompt_yes_no(
        f"Launch {count} idle bot(s) on {config.host}?",
        False,
    ):
        print("Launch cancelled.")
        return 0
    args = argparse.Namespace(
        count=count,
        dry_run=False,
        confirm=True,
        whitelist=whitelist,
    )
    return _run(config, args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not actual_argv:
            return interactive_run()
        parser = build_parser()
        args = parser.parse_args(actual_argv)
        validate_args(args)
        return run(args)
    except (TeamTalkConfigurationError, TeamTalkError, OSError) as exc:
        return print_tool_error(exc)
    except (EOFError, KeyboardInterrupt):
        print("Interrupted.")
        return 130


if __name__ == "__main__":
    multiprocessing.freeze_support()
    raise SystemExit(main())
