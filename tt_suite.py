#!/usr/bin/env python3
"""Run consent-aware TeamTalk discovery and load-test operations.

The suite discovers the channels and users visible to the authenticated
account, then optionally performs login/logout cycles, channel join/leave
cycles, channel test messages, and private test messages.  Bulk targets are
opt-in and the server must be present in ``whitelist.txt`` before a connection
is attempted.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional, Sequence

from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    add_connection_arguments,
    config_dir,
    config_from_args,
    print_tool_error,
    prompt_connection_config,
    prompt_float,
    prompt_int,
    prompt_text,
    prompt_yes_no,
)


# When frozen by PyInstaller, __file__ is inside a temp extraction dir, so
# resolve config relative to the executable (where the user runs the binary).
PROJECT_DIR = config_dir()
DEFAULT_WHITELIST = PROJECT_DIR / "whitelist.txt"
DEFAULT_INTERVAL = 0.2
DEFAULT_MESSAGE_COUNT = 1
DEFAULT_LOGIN_CYCLES = 0
DEFAULT_JOIN_LEAVE_CYCLES = 0


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
    allowed = load_whitelist(whitelist_path)
    normalized_host = normalize_host(host)
    if normalized_host not in allowed:
        raise TeamTalkConfigurationError(
            f"server {host!r} is not in the whitelist {whitelist_path}"
        )


def validate_test_message(value: str, label: str) -> str:
    message = value.rstrip("\r\n")
    if not message:
        raise TeamTalkConfigurationError(f"{label} cannot be empty")
    if len(message.encode("utf-8")) > 4096:
        raise TeamTalkConfigurationError(f"{label} is limited to 4096 UTF-8 bytes")
    return message


def parse_user_ids(values: object) -> tuple[list[int], bool]:
    """Parse IDs and the special ``all`` selector."""

    if values is None:
        return [], False
    if isinstance(values, (str, int)):
        raw_values = [values]
    else:
        raw_values = list(values)  # type: ignore[arg-type]

    user_ids: list[int] = []
    all_users = False
    for raw_value in raw_values:
        for value in str(raw_value).split(","):
            value = value.strip()
            if not value:
                raise TeamTalkConfigurationError(
                    "--user-id must contain numeric IDs or the word 'all'"
                )
            if value.casefold() == "all":
                all_users = True
                continue
            try:
                user_id = int(value)
            except ValueError as exc:
                raise TeamTalkConfigurationError(
                    f"invalid TeamTalk user ID: {value!r}"
                ) from exc
            if user_id < 0:
                raise TeamTalkConfigurationError(
                    "private-message recipient user IDs must be non-negative"
                )
            if user_id not in user_ids:
                user_ids.append(user_id)
    return user_ids, all_users


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Discover and run consent-aware TeamTalk test operations."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--whitelist",
        type=Path,
        default=Path(os.environ.get("TT_WHITELIST", DEFAULT_WHITELIST)),
        help=f"exact server allowlist (default: {DEFAULT_WHITELIST})",
    )
    parser.add_argument(
        "--all",
        dest="all_targets",
        action="store_true",
        help="select all discovered users and channels for requested operations",
    )
    parser.add_argument(
        "--all-users",
        action="store_true",
        help="select every discovered online user for private messages",
    )
    parser.add_argument(
        "--all-channels",
        action="store_true",
        help="select every discovered channel for channel operations",
    )
    parser.add_argument(
        "--user-id",
        action="append",
        metavar="ID[,ID...]|all",
        help="private-message recipient IDs; use 'all' for every discovered user",
    )
    parser.add_argument(
        "--channel-message",
        help="channel test message; use --all-channels for every discovered channel",
    )
    parser.add_argument(
        "--private-message",
        help="private test message; use --all-users or --user-id all for all users",
    )
    parser.add_argument(
        "--message-count",
        type=int,
        default=DEFAULT_MESSAGE_COUNT,
        help=f"messages per selected target, any positive integer (default: {DEFAULT_MESSAGE_COUNT})",
    )
    parser.add_argument(
        "--login-cycles",
        type=int,
        default=DEFAULT_LOGIN_CYCLES,
        help="additional login/logout cycles; zero skips the explicit cycle phase",
    )
    parser.add_argument(
        "--join-leave-cycles",
        type=int,
        default=DEFAULT_JOIN_LEAVE_CYCLES,
        help="join/leave cycles per selected channel; zero skips channel cycling",
    )
    parser.add_argument(
        "--concurrent",
        action="store_true",
        help="run the selected actions as concurrent bots (one per concern) "
        "instead of sequentially in a single session",
    )
    parser.add_argument(
        "--churn-bots",
        type=int,
        default=0,
        help="number of extra login/logout churn bots to spawn concurrently; "
        "any non-negative integer (requires --concurrent)",
    )
    parser.add_argument(
        "--churn-cycles",
        type=int,
        default=5,
        help="login/logout cycles per churn bot; any positive integer",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL,
        help=f"seconds between operations; zero is allowed (default: {DEFAULT_INTERVAL:g})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="discover and print targets without sending or joining",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm requested channel/private test actions",
    )
    return parser


def validate_args(args: argparse.Namespace) -> tuple[list[int], bool, bool]:
    if args.channel_path and args.channel_path.strip().casefold() == "all":
        args.channel_path = None
        args.all_channels = True

    user_ids, ids_request_all = parse_user_ids(args.user_id)
    all_users = bool(args.all_users or args.all_targets or ids_request_all)
    all_channels = bool(args.all_channels or args.all_targets)

    if args.message_count < 1:
        raise TeamTalkConfigurationError("--message-count must be at least 1")
    if args.login_cycles < 0:
        raise TeamTalkConfigurationError("--login-cycles cannot be negative")
    if args.join_leave_cycles < 0:
        raise TeamTalkConfigurationError("--join-leave-cycles cannot be negative")
    if args.interval < 0:
        raise TeamTalkConfigurationError("--interval cannot be negative")

    if args.channel_message is not None:
        args.channel_message = validate_test_message(
            args.channel_message, "--channel-message"
        )
    if args.private_message is not None:
        args.private_message = validate_test_message(
            args.private_message, "--private-message"
        )
        if not user_ids and not all_users:
            raise TeamTalkConfigurationError(
                "--private-message requires --user-id or --all-users"
            )

    channel_action = bool(
        args.channel_message is not None or args.join_leave_cycles > 0
    )
    if channel_action and not (
        all_channels or args.channel_id is not None or args.channel_path
    ):
        raise TeamTalkConfigurationError(
            "channel operations require --channel-id, --channel-path, or --all-channels"
        )

    if args.churn_bots < 0:
        raise TeamTalkConfigurationError("--churn-bots cannot be negative")
    if args.churn_cycles < 1:
        raise TeamTalkConfigurationError("--churn-cycles must be at least 1")
    if args.churn_bots > 0 and not args.concurrent:
        raise TeamTalkConfigurationError(
            "--churn-bots requires --concurrent (each churn bot opens its own "
            "SDK connection)"
        )
    if args.concurrent:
        has_action = (
            args.private_message is not None
            or channel_action
            or args.churn_bots > 0
        )
        if not has_action:
            raise TeamTalkConfigurationError(
                "--concurrent requires --private-message, a channel action "
                "(--channel-message or --join-leave-cycles), or --churn-bots"
            )
        if not args.dry_run and not args.confirm:
            raise TeamTalkConfigurationError(
                "--confirm is required for the concurrent bot run "
                "(use --dry-run to preview the bot plan)"
            )

    active_action = channel_action or args.private_message is not None
    if active_action and not args.dry_run and not args.confirm:
        raise TeamTalkConfigurationError(
            "--confirm is required for channel or private test actions"
        )
    return user_ids, all_users, all_channels


def normalize_channel_path(path: str) -> str:
    normalized = path.strip() or "/"
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    normalized = normalized.rstrip("/")
    return normalized or "/"


def resolve_channels(
    channels: Sequence[dict[str, Any]],
    config: Any,
    all_channels: bool,
) -> list[dict[str, Any]]:
    if all_channels:
        return list(channels)
    if config.channel_id is not None:
        for channel in channels:
            if int(channel["id"]) == int(config.channel_id):
                return [channel]
        return [{"id": int(config.channel_id), "name": str(config.channel_id), "path": ""}]

    requested_path = normalize_channel_path(config.channel_path or "/")
    for channel in channels:
        if normalize_channel_path(str(channel.get("path") or "/")) == requested_path:
            return [channel]
    raise TeamTalkConfigurationError(
        f"channel path {config.channel_path!r} was not found among visible channels"
    )


def pause(interval: float) -> None:
    if interval:
        time.sleep(interval)


def print_discovery(
    channels: Sequence[dict[str, Any]], users: Sequence[dict[str, Any]]
) -> None:
    print(f"Discovered {len(channels)} channel(s):")
    for channel in channels:
        path = channel.get("path") or "/"
        print(f"  channel {channel['id']}: {path}")
    print(f"Discovered {len(users)} online user(s):")
    for user in users:
        location = f" — {user['channel_path']}" if user.get("channel_path") else ""
        print(f"  user {user['id']}: {user['display_name']}{location}")


def run_login_logout_cycles(
    session: TeamTalkSession, cycles: int, interval: float
) -> bool:
    """Run login/logout cycles.  Returns False if a kick could not be recovered."""

    for index in range(cycles):
        try:
            if not session.logged_in:
                session.login()
            print(f"Auth cycle {index + 1}/{cycles}: logged in.")
            session.logout()
            print(f"Auth cycle {index + 1}/{cycles}: logged out.")
        except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
            print(f"[kick-resistance] auth cycle {index + 1} interrupted: {exc}")
            if not session.check_and_reconnect():
                print("Could not reconnect; stopping login/logout cycles.")
                return False
        if index + 1 < cycles:
            pause(interval)
    return True


def run_channel_operations(
    session: TeamTalkSession,
    channels: Sequence[dict[str, Any]],
    *,
    channel_password: str,
    join_leave_cycles: int,
    channel_message: Optional[str],
    message_count: int,
    interval: float,
) -> None:
    cycles = join_leave_cycles or (1 if channel_message is not None else 0)
    if cycles == 0:
        return

    for channel in channels:
        channel_id = int(channel["id"])
        channel_name = str(channel.get("path") or channel.get("name") or channel_id)
        password_required = bool(channel.get("password_required"))
        if password_required and not channel_password:
            print(f"Channel {channel_name} requires a password; attempting configured credentials.")
        session.rejoin_channel_id = channel_id
        session.rejoin_channel_password = channel_password
        for cycle in range(cycles):
            try:
                session.join_channel(channel_id, channel_password)
                print(
                    f"Channel {channel_name}: joined cycle {cycle + 1}/{cycles}."
                )
                if channel_message is not None:
                    for message_index in range(message_count):
                        session.send_channel_message(channel_message, channel_id)
                        print(
                            f"Channel {channel_name}: sent message "
                            f"{message_index + 1}/{message_count}."
                        )
                        if message_index + 1 < message_count:
                            pause(interval)
                session.leave_channel()
                print(f"Channel {channel_name}: left cycle {cycle + 1}/{cycles}.")
            except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                print(f"[kick-resistance] {channel_name} cycle {cycle + 1} interrupted: {exc}")
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping channel operations.")
                    return
            if cycle + 1 < cycles:
                pause(interval)


def run_private_operations(
    session: TeamTalkSession,
    user_ids: Sequence[int],
    *,
    message: str,
    message_count: int,
    interval: float,
) -> bool:
    """Send private messages.  Returns False if a kick could not be recovered."""

    for message_index in range(message_count):
        for recipient_index, user_id in enumerate(user_ids, start=1):
            try:
                session.send_private_message(message, int(user_id))
            except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                print(f"[kick-resistance] private message interrupted: {exc}")
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping private operations.")
                    return False
                continue
            print(
                f"Private message {message_index + 1}/{message_count} "
                f"to user {user_id} ({recipient_index}/{len(user_ids)} users)."
            )
            if recipient_index < len(user_ids) or message_index + 1 < message_count:
                pause(interval)
    return True


def _bot_pause(interval: float, stop_event: threading.Event) -> bool:
    """Sleep ``interval`` unless ``stop_event`` is set; return True if stopping."""

    if interval <= 0:
        return stop_event.is_set()
    return stop_event.wait(interval)


def _bot_config(base: Any, nickname: str) -> Any:
    """Return a connection config for a worker bot: unique nick, no auto-join."""

    return replace(
        base,
        nickname=nickname,
        channel_id=None,
        channel_path=None,
        channel_password="",
    )


def _user_bot(
    base_config: Any,
    user_ids: Sequence[int],
    message: str,
    count: int,
    interval: float,
    stop_event: threading.Event,
) -> None:
    config = _bot_config(base_config, f"{base_config.nickname}-users")
    with TeamTalkSession(config) as session:
        for message_index in range(count):
            if stop_event.is_set():
                return
            for recipient_index, user_id in enumerate(user_ids, start=1):
                try:
                    session.send_private_message(message, int(user_id))
                except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                    print(f"[user-bot] send interrupted: {exc}")
                    if not session.check_and_reconnect():
                        print("[user-bot] could not reconnect; stopping.")
                        return
                    continue
                print(
                    f"[user-bot] private message {message_index + 1}/{count} "
                    f"to user {user_id} ({recipient_index}/{len(user_ids)} users)."
                )
                if recipient_index < len(user_ids) or message_index + 1 < count:
                    if _bot_pause(interval, stop_event):
                        return


def _channel_bot(
    base_config: Any,
    channels: Sequence[dict[str, Any]],
    channel_password: str,
    channel_message: Optional[str],
    count: int,
    join_leave_cycles: int,
    interval: float,
    stop_event: threading.Event,
) -> None:
    config = _bot_config(base_config, f"{base_config.nickname}-channels")
    cycles = join_leave_cycles or (1 if channel_message is not None else 0)
    if cycles == 0:
        return
    with TeamTalkSession(config) as session:
        for channel in channels:
            if stop_event.is_set():
                return
            channel_id = int(channel["id"])
            channel_name = str(
                channel.get("path") or channel.get("name") or channel_id
            )
            password_required = bool(channel.get("password_required"))
            if password_required and not channel_password:
                print(
                    f"[channel-bot] {channel_name} requires a password; "
                    "attempting configured credentials."
                )
            session.rejoin_channel_id = channel_id
            session.rejoin_channel_password = channel_password
            for cycle in range(cycles):
                if stop_event.is_set():
                    return
                try:
                    session.join_channel(channel_id, channel_password)
                except TeamTalkError as exc:
                    print(f"[channel-bot] could not join {channel_name}: {exc}")
                    break
                print(
                    f"[channel-bot] {channel_name}: joined cycle "
                    f"{cycle + 1}/{cycles}."
                )
                if channel_message is not None:
                    for message_index in range(count):
                        if stop_event.is_set():
                            return
                        try:
                            session.send_channel_message(channel_message, channel_id)
                        except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                            print(f"[channel-bot] send interrupted: {exc}")
                            if not session.check_and_reconnect():
                                print("[channel-bot] could not reconnect; stopping.")
                                return
                            continue
                        print(
                            f"[channel-bot] {channel_name}: sent message "
                            f"{message_index + 1}/{count}."
                        )
                        if message_index + 1 < count and _bot_pause(
                            interval, stop_event
                        ):
                            return
                try:
                    session.leave_channel()
                except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                    print(f"[channel-bot] leave interrupted: {exc}")
                    if not session.check_and_reconnect():
                        print("[channel-bot] could not reconnect; stopping.")
                        return
                    continue
                print(
                    f"[channel-bot] {channel_name}: left cycle "
                    f"{cycle + 1}/{cycles}."
                )
                if cycle + 1 < cycles and _bot_pause(interval, stop_event):
                    return


def _churn_bot(
    base_config: Any,
    index: int,
    total: int,
    cycles: int,
    interval: float,
    stop_event: threading.Event,
) -> None:
    config = _bot_config(base_config, f"{base_config.nickname}-churn-{index}")
    tag = f"[churn-bot {index}/{total}]"
    # open() logs in, so the first cycle starts already authenticated.
    with TeamTalkSession(config) as session:
        for i in range(cycles):
            if stop_event.is_set():
                return
            try:
                if i:
                    session.login()
                print(f"{tag} cycle {i + 1}/{cycles}: logged in.")
                session.logout()
                print(f"{tag} cycle {i + 1}/{cycles}: logged out.")
            except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                print(f"{tag} cycle {i + 1} interrupted: {exc}")
                if not session.check_and_reconnect():
                    print(f"{tag} could not reconnect; stopping.")
                    return
            if i + 1 < cycles and _bot_pause(interval, stop_event):
                return


def _run_concurrent(
    config: Any,
    args: argparse.Namespace,
    user_ids: Sequence[int],
    all_users: bool,
    all_channels: bool,
) -> int:
    # Discovery happens on a throwaway session; every worker bot then opens its
    # own connection so the per-user, per-channel, and churn work run in parallel.
    discovery_config = replace(config, channel_id=None, channel_path=None)
    with TeamTalkSession(discovery_config) as session:
        channels = session.list_channels()
        users = session.list_users()
    print_discovery(channels, users)

    selected_users = (
        [int(user["id"]) for user in users] if all_users else list(user_ids)
    )
    channel_action = bool(
        args.channel_message is not None or args.join_leave_cycles > 0
    )
    selected_channels = (
        resolve_channels(channels, config, all_channels) if channel_action else []
    )

    if all_users:
        print(f"Selected all {len(selected_users)} discovered user(s).")
    elif selected_users:
        print(f"Selected {len(selected_users)} explicit user(s).")
    if all_channels:
        print(f"Selected all {len(selected_channels)} discovered channel(s).")
    elif selected_channels:
        print(f"Selected {len(selected_channels)} channel(s).")

    if args.private_message is not None and not selected_users:
        raise TeamTalkConfigurationError(
            "no online users matched the private-message selection"
        )

    plan_lines = ["Concurrent bot plan:"]
    if args.private_message is not None:
        plan_lines.append(
            f"  1 user-bot: {args.message_count} private message(s) to "
            f"{len(selected_users)} user(s)."
        )
    if channel_action:
        plan_lines.append(f"  1 channel-bot: {len(selected_channels)} channel(s).")
    if args.churn_bots > 0:
        plan_lines.append(
            f"  {args.churn_bots} churn-bot(s): {args.churn_cycles} "
            "login/logout cycle(s) each."
        )
    print("\n".join(plan_lines))

    if args.dry_run:
        print("Dry run complete; no bots were spawned, nothing was sent.")
        return 0

    stop_event = threading.Event()
    threads: list[threading.Thread] = []
    if args.private_message is not None:
        threads.append(
            threading.Thread(
                target=_user_bot,
                args=(
                    config,
                    selected_users,
                    args.private_message,
                    args.message_count,
                    args.interval,
                    stop_event,
                ),
                name="user-bot",
                daemon=True,
            )
        )
    if channel_action:
        threads.append(
            threading.Thread(
                target=_channel_bot,
                args=(
                    config,
                    selected_channels,
                    config.channel_password,
                    args.channel_message,
                    args.message_count,
                    args.join_leave_cycles,
                    args.interval,
                    stop_event,
                ),
                name="channel-bot",
                daemon=True,
            )
        )
    for i in range(args.churn_bots):
        threads.append(
            threading.Thread(
                target=_churn_bot,
                args=(
                    config,
                    i + 1,
                    args.churn_bots,
                    args.churn_cycles,
                    args.interval,
                    stop_event,
                ),
                name=f"churn-bot-{i + 1}",
                daemon=True,
            )
        )

    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    except KeyboardInterrupt:
        print("\nInterrupted: signalling bots to stop...")
        stop_event.set()
        for thread in threads:
            thread.join(timeout=5.0)
        print("Stopped.")
        return 130
    print("Finished concurrent TeamTalk suite.")
    return 0


def execute(config: Any, args: argparse.Namespace) -> int:
    user_ids, all_users, all_channels = validate_args(args)
    ensure_server_allowed(config.host, args.whitelist)

    if args.concurrent:
        return _run_concurrent(config, args, user_ids, all_users, all_channels)

    # Discover without automatically joining the configured channel.  Channel
    # targets are joined explicitly after the complete inventory is known.
    session_config = replace(config, channel_id=None, channel_path=None)
    with TeamTalkSession(session_config) as session:
        channels = session.list_channels()
        users = session.list_users()
        print_discovery(channels, users)

        selected_users = (
            [int(user["id"]) for user in users] if all_users else list(user_ids)
        )
        channel_action = bool(
            args.channel_message is not None or args.join_leave_cycles > 0
        )
        selected_channels = (
            resolve_channels(channels, config, all_channels) if channel_action else []
        )

        if all_users:
            print(f"Selected all {len(selected_users)} discovered user(s).")
        elif selected_users:
            print(f"Selected {len(selected_users)} explicit user(s).")
        if all_channels:
            print(f"Selected all {len(selected_channels)} discovered channel(s).")
        elif selected_channels:
            print(f"Selected {len(selected_channels)} channel(s).")

        if args.dry_run:
            print("Dry run complete; no messages or channel operations were performed.")
            return 0

        if args.login_cycles:
            if not run_login_logout_cycles(session, args.login_cycles, args.interval):
                print("Finished TeamTalk suite (interrupted).")
                return 1

        needs_operations = bool(selected_channels or args.private_message is not None)
        if needs_operations and not session.is_online():
            print("Session is offline; cannot continue channel/private operations.")
            return 1
        if needs_operations and not session.logged_in:
            session.login()
            print("Logged back in for requested test operations.")

        if selected_channels:
            run_channel_operations(
                session,
                selected_channels,
                channel_password=config.channel_password,
                join_leave_cycles=args.join_leave_cycles,
                channel_message=args.channel_message,
                message_count=args.message_count,
                interval=args.interval,
            )
        if args.private_message is not None:
            if not selected_users:
                raise TeamTalkConfigurationError(
                    "no online users matched the private-message selection"
                )
            if not run_private_operations(
                session,
                selected_users,
                message=args.private_message,
                message_count=args.message_count,
                interval=args.interval,
            ):
                print("Finished TeamTalk suite (interrupted).")
                return 1
    print("Finished TeamTalk suite.")
    return 0


def run(args: argparse.Namespace) -> int:
    config = config_from_args(args)
    return execute(config, args)


def interactive_run() -> int:
    config = prompt_connection_config(channel_required=False)
    all_channels = prompt_yes_no("Use all discovered channels?", False)
    channel_path = None
    channel_password = ""
    if not all_channels:
        channel_path = prompt_text(
            "Channel path for channel operations",
            os.environ.get("TT_CHANNEL_PATH", "/"),
        )
        channel_password = prompt_text(
            "TeamTalk channel password",
            os.environ.get("TT_CHANNEL_PASSWORD", "") or None,
            secret=True,
        )
    config = replace(
        config,
        channel_id=None,
        channel_path=channel_path,
        channel_password=channel_password,
    )
    all_users = prompt_yes_no("Use all discovered online users?", False)
    user_text = "" if all_users else prompt_text(
        "Private user IDs (comma-separated, or all; blank to skip)", None
    )
    channel_message = prompt_text("Channel test message (blank to skip)", None)
    private_message = prompt_text("Private test message (blank to skip)", None)
    login_cycles = prompt_int(
        "Additional login/logout cycles",
        DEFAULT_LOGIN_CYCLES,
        minimum=0,
    )
    join_leave_cycles = prompt_int(
        "Join/leave cycles per selected channel",
        1 if all_channels else DEFAULT_JOIN_LEAVE_CYCLES,
        minimum=0,
    )
    message_count = prompt_int(
        "Messages per selected target",
        DEFAULT_MESSAGE_COUNT,
        minimum=1,
    )
    interval = prompt_float(
        "Seconds between operations",
        DEFAULT_INTERVAL,
        minimum=0.0,
    )
    concurrent = prompt_yes_no(
        "Run as concurrent bots (one bot per concern, plus churn bots)?",
        False,
    )
    churn_bots = 0
    churn_cycles = 5
    if concurrent:
        churn_bots = prompt_int(
            "Number of login/logout churn bots", 0, minimum=0
        )
        if churn_bots:
            churn_cycles = prompt_int(
                "Login/logout cycles per churn bot", 5, minimum=1
            )
    confirm = prompt_yes_no("Proceed with the requested test operations?", False)
    args = argparse.Namespace(
        whitelist=Path(os.environ.get("TT_WHITELIST", DEFAULT_WHITELIST)),
        all_targets=False,
        all_users=all_users,
        all_channels=all_channels,
        user_id=[user_text] if user_text else None,
        channel_message=channel_message or None,
        private_message=private_message or None,
        message_count=message_count,
        login_cycles=login_cycles,
        join_leave_cycles=join_leave_cycles,
        concurrent=concurrent,
        churn_bots=churn_bots,
        churn_cycles=churn_cycles,
        interval=interval,
        dry_run=False,
        confirm=confirm,
        kick_resistance=config.kick_resistance,
        reconnect_delay=config.reconnect_delay,
        channel_id=config.channel_id,
        channel_path=config.channel_path,
    )
    return execute(config, args)


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
    raise SystemExit(main())
