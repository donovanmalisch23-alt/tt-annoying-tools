#!/usr/bin/env python3
"""A consent-aware TeamTalk event bot for the legacy offender entry point.

The Windows executable automatically insulted people who wrote channel
messages.  That behavior is not reproduced.  This replacement responds only
to an explicit trigger, only in the selected channel, and only for an
allowlisted user (or when ``--allow-all`` is explicitly chosen).  Its default
reply is intentionally benign.
"""

from __future__ import annotations

import argparse
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
    message_fields,
    print_tool_error,
    sdk_event,
    sdk_int,
    prompt_connection_config,
    prompt_text,
    prompt_yes_no,
)


MIN_COOLDOWN = 5.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a trigger-based TeamTalk response bot through the SDK."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--trigger",
        default="!hello",
        help="exact prefix that opts a message into a response (default: !hello)",
    )
    parser.add_argument(
        "--response",
        default="Hi {username}, thanks for your message!",
        help="benign response; supports {username}, {user_id}, and {message}",
    )
    parser.add_argument(
        "--allow-user",
        action="append",
        default=[],
        metavar="NAME[,NAME...]",
        help="username allowed to trigger responses; repeat or comma-separate "
        "for multiple users. Users are tracked by username, so the allowlist "
        "and the per-user cooldown survive a user reconnecting with a fresh "
        "server-assigned ID",
    )
    parser.add_argument(
        "--allow-user-id",
        type=int,
        action="append",
        default=[],
        help="user ID allowed to receive responses; repeat for multiple users. "
        "Each ID is resolved to that user's username at startup and tracked by "
        "name from then on (prefer --allow-user: IDs change on every login)",
    )
    parser.add_argument(
        "--allow-all",
        action="store_true",
        help="allow any user in the selected channel to trigger a response",
    )
    parser.add_argument(
        "--cooldown",
        type=float,
        default=30.0,
        help=f"minimum seconds between replies to one user (minimum {MIN_COOLDOWN:g})",
    )
    parser.add_argument(
        "--max-responses",
        type=comma_int,
        default=100,
        help="responses before exit; 0 means unlimited with --confirm",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm unlimited bot operation when --max-responses 0 is used",
    )
    return parser


def parse_allow_users(values: object) -> list[str]:
    """Parse repeatable or comma-separated --allow-user usernames."""

    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values)  # type: ignore[arg-type]

    names: list[str] = []
    for raw_value in raw_values:
        for value in str(raw_value).split(","):
            value = value.strip()
            if not value:
                raise TeamTalkConfigurationError(
                    "--allow-user must contain one or more usernames"
                )
            names.append(value)
    return names


def _sender_key(incoming: dict[str, object]) -> str:
    """Stable per-user key for a message sender: their username, not the ID.

    Server-assigned user IDs change on every login, so the per-user cooldown
    and the allowlist key on the username the message carries.  An anonymous
    sender (blank username) falls back to an ID-derived key — the best
    identity available for someone with no name.
    """

    username = str(incoming.get("from_username") or "").strip()
    if username:
        return username.casefold()
    return f"user-{int(incoming.get('from_user_id') or 0)}"


def _resolve_allowed_users(
    session: TeamTalkSession, allow_user_ids: Sequence[int]
) -> tuple[set[str], set[int]]:
    """Resolve --allow-user-id values to usernames via the online roster.

    Returns (usernames, unresolved_ids).  A user who is online is tracked by
    their username from then on, so the allowlist keeps matching after they
    reconnect with a different ID.  An offline ID cannot be resolved and is
    matched literally until it appears.
    """

    usernames: set[str] = set()
    unresolved: set[int] = set()
    if not allow_user_ids:
        return usernames, unresolved
    roster: dict[int, dict[str, object]] = {}
    try:
        for user in session.list_users():
            roster[int(user["id"])] = user
    except (TeamTalkError, TeamTalkConfigurationError, OSError):
        roster = {}
    for user_id in allow_user_ids:
        user = roster.get(int(user_id))
        if user is None:
            unresolved.add(int(user_id))
            print(
                f"[allowlist] user ID {user_id} is not online; matching that "
                "ID literally until they can be resolved by name."
            )
            continue
        name = str(user.get("username") or "").strip() or str(
            user.get("nickname") or ""
        ).strip()
        if not name:
            unresolved.add(int(user_id))
            continue
        usernames.add(name.casefold())
        print(f"[allowlist] user ID {user_id} resolves to {name!r}; tracking by username.")
    return usernames, unresolved


def validate_args(args: argparse.Namespace) -> None:
    if not args.trigger:
        raise TeamTalkConfigurationError("--trigger cannot be empty")
    if len(args.response.encode("utf-8")) > 4096:
        raise TeamTalkConfigurationError("--response is limited to 4096 UTF-8 bytes")
    if any(user_id < 0 for user_id in args.allow_user_id):
        raise TeamTalkConfigurationError("--allow-user-id values cannot be negative")
    # Parse the names here so a malformed --allow-user value is rejected
    # before any connection is opened, not after the SDK has already tried
    # to connect (run() parses them again once connected).
    parse_allow_users(args.allow_user)
    if not args.allow_user and not args.allow_user_id and not args.allow_all:
        raise TeamTalkConfigurationError(
            "provide at least one --allow-user or --allow-user-id, or "
            "explicitly use --allow-all"
        )
    if args.cooldown < MIN_COOLDOWN:
        raise TeamTalkConfigurationError(
            f"--cooldown must be at least {MIN_COOLDOWN:g}s"
        )
    if args.max_responses < 0:
        raise TeamTalkConfigurationError("--max-responses must be zero or greater")
    if args.max_responses == 0 and not args.confirm:
        raise TeamTalkConfigurationError(
            "--confirm is required for unlimited operation (--max-responses 0)"
        )
    if args.channel_id is None and not args.channel_path:
        raise TeamTalkConfigurationError(
            "--channel-id or --channel-path is required for the response bot"
        )


def render_response(template: str, data: dict[str, object]) -> str:
    try:
        response = template.format(
            username=data["from_username"],
            user_id=data["from_user_id"],
            message=data["text"],
        ).strip()
    except KeyError as exc:
        raise TeamTalkConfigurationError(
            f"unsupported response placeholder {exc}; use username, user_id, or message"
        ) from exc
    except ValueError as exc:
        # str.format raises ValueError for malformed templates (e.g. an
        # unmatched "{"); surface it as a configuration error instead of an
        # unhandled traceback.
        raise TeamTalkConfigurationError(
            f"invalid response template: {exc}"
        ) from exc
    if not response:
        raise TeamTalkConfigurationError("rendered response cannot be empty")
    return response


def run(args: argparse.Namespace, config=None) -> int:
    validate_args(args)
    config = config or config_from_args(args)

    with TeamTalkSession(config) as session:
        sdk = session.sdk
        text_event = sdk_event(sdk, "CLIENTEVENT_CMD_USER_TEXTMSG")
        if text_event is None:
            raise TeamTalkError("the loaded SDK does not expose text-message events")
        channel_id = session.current_channel_id()
        session.rejoin_channel_id = channel_id
        session.rejoin_channel_password = config.channel_password
        own_user_id = sdk_int(session.client.getMyUserID(), -1)
        # Allowlist and cooldown are tracked by username: an allowed user keeps
        # their allowance, and a cooling-down user keeps their cooldown, even
        # after the server hands them a brand-new user ID on a relog.
        allowed_usernames = {name.casefold() for name in parse_allow_users(args.allow_user)}
        allowed_ids: set[int] = set()
        if args.allow_user_id:
            resolved_names, allowed_ids = _resolve_allowed_users(
                session, args.allow_user_id
            )
            allowed_usernames |= resolved_names
        last_reply: dict[str, float] = {}
        response_count = 0
        last_check = 0.0
        reconnect_delay = config.reconnect_delay
        print(
            f"Listening in channel {channel_id}; trigger {args.trigger!r}. "
            "Press Ctrl+C to stop."
        )

        while args.max_responses == 0 or response_count < args.max_responses:
            message = session.poll(1000)
            # Catch a server kick the moment the CON_LOST/CON_FAILED event is
            # dequeued, instead of relying on the getMyUserID() watchdog below.
            if session.is_connection_failure(message):
                print("[kick-resistance] bot lost its connection.")
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping bot.")
                    return 1
                try:
                    channel_id = session.current_channel_id()
                    own_user_id = sdk_int(session.client.getMyUserID(), -1)
                except TeamTalkError:
                    pass
                continue
            watch_now = time.monotonic()
            if watch_now - last_check >= reconnect_delay:
                last_check = watch_now
                if not session.is_online():
                    print("[kick-resistance] bot was kicked from the server.")
                    if not session.check_and_reconnect():
                        print("Could not reconnect; stopping bot.")
                        return 1
                    try:
                        channel_id = session.current_channel_id()
                        own_user_id = sdk_int(session.client.getMyUserID(), -1)
                    except TeamTalkError:
                        pass
                    continue
                # Still online: rejoin if kicked out of the channel only.
                try:
                    session.current_channel_id()
                except TeamTalkConfigurationError:
                    print("[kick-resistance] kicked from channel; rejoining.")
                    try:
                        session.join_channel(channel_id, config.channel_password)
                    except TeamTalkError as exc:
                        print(f"[kick-resistance] rejoin failed: {exc}")
            if sdk_int(getattr(message, "nClientEvent", 0)) != text_event:
                continue
            incoming = message_fields(getattr(message, "textmessage", None))
            sender_id = int(incoming["from_user_id"])
            if sender_id == own_user_id or incoming["channel_id"] != channel_id:
                continue
            sender_key = _sender_key(incoming)
            if (
                not args.allow_all
                and sender_key not in allowed_usernames
                and sender_id not in allowed_ids
            ):
                continue
            if incoming["more"] or not str(incoming["text"]).startswith(args.trigger):
                continue
            now = time.monotonic()
            # Absent means "never replied", so the first message from a user
            # always gets a reply even when the monotonic clock started small
            # (e.g. right after boot).
            last_time = last_reply.get(sender_key)
            if last_time is not None and now - last_time < args.cooldown:
                continue
            response = render_response(args.response, incoming)
            try:
                session.send_channel_message(response, channel_id)
            except (TeamTalkError, OSError) as exc:
                print(f"[kick-resistance] reply interrupted: {exc}")
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping bot.")
                    return 1
                try:
                    channel_id = session.current_channel_id()
                    own_user_id = sdk_int(session.client.getMyUserID(), -1)
                except TeamTalkError:
                    pass
                continue
            last_reply[sender_key] = now
            response_count += 1
            sender_display = str(incoming["from_username"] or "").strip() or f"user {sender_id}"
            print(
                f"Replied to {sender_display} "
                f"({response_count}/{args.max_responses or '∞'})."
            )
    return 0


def interactive_run() -> int:
    config = prompt_connection_config(channel_required=True)
    trigger = prompt_text("Trigger text", "!hello")
    response = prompt_text(
        "Response text",
        "Hi {username}, thanks for your message!",
    )
    allow_all = prompt_yes_no(
        "Respond to any user in the selected channel?",
        True,
    )
    args = argparse.Namespace(
        trigger=trigger,
        response=response,
        allow_user=[],
        allow_user_id=[],
        allow_all=allow_all,
        cooldown=30.0,
        max_responses=100,
        confirm=False,
        channel_id=config.channel_id,
        channel_path=config.channel_path,
    )
    return run(args, config=config)


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
