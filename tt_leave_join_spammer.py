#!/usr/bin/env python3
"""Run a TeamTalk leave/join test through the official SDK.

With no command-line arguments this keeps the old tool's prompts: cycle count,
delay in milliseconds, and startup wait in seconds.  The API connection and
channel prompts follow those values because the SDK no longer uses the
currently focused TeamTalk window.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from typing import Optional, Sequence

from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    add_connection_arguments,
    comma_int,
    config_from_args,
    kill_switch_triggered,
    print_tool_error,
    prompt_connection_config,
    prompt_float,
    prompt_int,
)


MIN_INTERVAL = 0.0
MAX_WAIT = 300.0
DEFAULT_CYCLES = 5
DEFAULT_INTERVAL_MS = 200.0
DEFAULT_WAIT = 50.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a TeamTalk channel leave/join test through the SDK."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--cycles",
        type=comma_int,
        default=DEFAULT_CYCLES,
        help=f"leave/join cycles, any positive integer (default: {DEFAULT_CYCLES})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_MS / 1000.0,
        help="seconds between leave and join commands; zero is allowed (default: 0.2)",
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
    if args.channel_id is None and not args.channel_path:
        raise TeamTalkConfigurationError(
            "--channel-id or --channel-path is required for a leave/join test"
        )


def run_cycles(*, config, cycles: int, interval: float, wait: float) -> int:
    if wait:
        print(f"You have {wait:g} seconds before the API starts the test…")
        time.sleep(wait)

    with TeamTalkSession(config) as session:
        if config.channel_id is not None:
            session.rejoin_channel_id = config.channel_id
            session.rejoin_channel_password = config.channel_password
        for index in range(cycles):
            if kill_switch_triggered():
                print("[kill-switch] stopping leave/join test.")
                return 130
            try:
                current_channel = session.current_channel_id()
                session.leave_channel()
                print(f"Cycle {index + 1}/{cycles}: left channel {current_channel}.")
                time.sleep(interval)
                if config.channel_id is not None:
                    joined_channel = session.join_channel(
                        config.channel_id,
                        config.channel_password,
                    )
                else:
                    joined_channel = session.join_channel_path(
                        config.channel_path or "/",
                        config.channel_password,
                    )
                print(f"Cycle {index + 1}/{cycles}: joined channel {joined_channel}.")
            except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                print(f"[kick-resistance] cycle {index + 1} interrupted: {exc}")
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping leave/join test.")
                    return 1
            if index + 1 < cycles:
                time.sleep(interval)
    print("Finished leave/join test.")
    return 0


def run(args: argparse.Namespace) -> int:
    validate_args(args)
    config = config_from_args(args)
    return run_cycles(
        config=config,
        cycles=args.cycles,
        interval=args.interval,
        wait=args.wait,
    )


def interactive_run() -> int:
    cycles = prompt_int(
        "How many times to leave and join the TeamTalk channel?",
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
    config = prompt_connection_config(channel_required=True)
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
