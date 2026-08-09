#!/usr/bin/env python3
"""Repeated TeamTalk login/logout test through the official SDK."""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace
from typing import Optional, Sequence

from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    add_connection_arguments,
    config_from_args,
    print_tool_error,
    prompt_connection_config,
    prompt_float,
    prompt_int,
)


MIN_INTERVAL = 0.0
MAX_WAIT = 300.0
DEFAULT_CYCLES = 5
DEFAULT_INTERVAL_MS = 200.0
DEFAULT_WAIT = 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Repeat TeamTalk login and logout cycles through the SDK."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--cycles",
        type=int,
        default=DEFAULT_CYCLES,
        help=f"login/logout cycles, any positive integer (default: {DEFAULT_CYCLES})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_MS / 1000.0,
        help=f"seconds between cycles; zero is allowed (default: {DEFAULT_INTERVAL_MS / 1000:g})",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT,
        help=f"seconds to wait before the first cycle (maximum {MAX_WAIT:g})",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="accepted for compatibility; interactive mode does not need confirmation",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.cycles < 1:
        raise TeamTalkConfigurationError("--cycles must be at least 1")
    if args.interval < MIN_INTERVAL:
        raise TeamTalkConfigurationError("--interval cannot be negative")
    if not 0 <= args.wait <= MAX_WAIT:
        raise TeamTalkConfigurationError(f"--wait must be between 0 and {MAX_WAIT:g}s")


def login_only_config_from_args(args: argparse.Namespace):
    """Build connection settings while preventing an inherited channel auto-join."""

    config = config_from_args(args)
    return replace(config, channel_id=None, channel_path=None, channel_password="")


def run_cycles(*, config, cycles: int, interval: float, wait: float) -> int:
    """Repeat login/logout cycles on one persistent TeamTalk connection."""

    if wait:
        print(f"You have {wait:g} seconds before the login/logout test starts…")
        time.sleep(wait)

    with TeamTalkSession(config) as session:
        for index in range(cycles):
            if index:
                session.login()
            print(f"Cycle {index + 1}/{cycles}: logged in.")
            session.logout()
            print(f"Cycle {index + 1}/{cycles}: logged out.")
            if index + 1 < cycles and interval:
                time.sleep(interval)
    print("Finished login/logout test.")
    return 0


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    config = login_only_config_from_args(args)
    return run_cycles(
        config=config,
        cycles=args.cycles,
        interval=args.interval,
        wait=args.wait,
    )


def interactive_run() -> int:
    cycles = prompt_int(
        "How many times to log in and out of TeamTalk?",
        DEFAULT_CYCLES,
        minimum=1,
    )
    interval_ms = prompt_float(
        "How fast to run, in milliseconds?",
        DEFAULT_INTERVAL_MS,
        minimum=0.0,
    )
    wait = prompt_float(
        "Time to wait prior to starting, in seconds?",
        DEFAULT_WAIT,
        minimum=0.0,
        maximum=MAX_WAIT,
    )
    config = prompt_connection_config(channel_required=False)
    return run_cycles(
        config=config,
        cycles=cycles,
        interval=interval_ms / 1000.0,
        wait=wait,
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not actual_argv:
            return interactive_run()
        parser = build_parser()
        return run(parser.parse_args(actual_argv))
    except (TeamTalkConfigurationError, TeamTalkError, OSError) as exc:
        return print_tool_error(exc)
    except (EOFError, KeyboardInterrupt):
        print("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
