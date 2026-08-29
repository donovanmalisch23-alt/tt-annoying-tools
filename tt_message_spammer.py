#!/usr/bin/env python3
"""Send TeamTalk text messages through the official SDK.

Running this file without arguments connects to TeamTalk first, then offers
numbered channel and private-recipient menus before asking for the message
settings. No desktop focus, clipboard, or paste action is involved.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional, Sequence

from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    add_connection_arguments,
    comma_int,
    config_from_args,
    print_tool_error,
    prompt_connection_config,
    prompt_float,
    prompt_int,
    prompt_text,
    prompt_yes_no,
)


MAX_PRIVATE_RECIPIENTS = 20
MIN_REPEAT_INTERVAL = 0.0
MAX_WAIT = 300.0
DEFAULT_MESSAGE = "Oh Yeah!"
DEFAULT_COUNT = 3
DEFAULT_INTERVAL_MS = 50.0
DEFAULT_WAIT = 0.0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Send TeamTalk channel or private messages through the SDK."
    )
    add_connection_arguments(parser)
    parser.add_argument(
        "--target",
        choices=("channel", "private"),
        default="channel",
        help="send to the joined channel or a user (default: channel)",
    )
    message_group = parser.add_mutually_exclusive_group()
    message_group.add_argument(
        "--message",
        default=os.environ.get("TT_MESSAGE"),
        help="message text (or set TT_MESSAGE)",
    )
    message_group.add_argument(
        "--message-file",
        type=Path,
        help="read one UTF-8 message from a file",
    )
    parser.add_argument(
        "--user",
        action="append",
        metavar="NAME[,NAME...]",
        help=(
            "private-message recipient by username; repeat or comma-separate "
            f"for up to {MAX_PRIVATE_RECIPIENTS} users. Users are tracked by "
            "username, so a recipient keeps their message budget across a "
            "reconnect even when the server hands out a fresh user ID"
        ),
    )
    parser.add_argument(
        "--user-id",
        action="append",
        metavar="ID[,ID...]",
        help=(
            "private-message recipient by the server's numeric user ID; "
            "repeat or comma-separate for up to "
            f"{MAX_PRIVATE_RECIPIENTS} users. Each ID is resolved to the "
            "user's username at startup and tracked by that name from then on "
            "(prefer --user: IDs change on every login)"
        ),
    )
    parser.add_argument(
        "--count",
        type=comma_int,
        default=DEFAULT_COUNT,
        help=f"number of sends, any positive integer (default: {DEFAULT_COUNT})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=DEFAULT_INTERVAL_MS / 1000.0,
        help=f"seconds between sends; zero is allowed (default: {DEFAULT_INTERVAL_MS / 1000:g})",
    )
    parser.add_argument(
        "--wait",
        type=float,
        default=DEFAULT_WAIT,
        help=f"seconds to wait before the first SDK send (maximum {MAX_WAIT:g})",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="accepted for compatibility; interactive mode does not need confirmation",
    )
    return parser


def validate_message(message: str) -> str:
    message = message.rstrip("\r\n")
    if not message:
        raise TeamTalkConfigurationError("message cannot be empty")
    if len(message.encode("utf-8")) > 4096:
        raise TeamTalkConfigurationError("message is limited to 4096 UTF-8 bytes")
    return message


def read_message(args: argparse.Namespace) -> str:
    if args.message_file is not None:
        try:
            message = args.message_file.read_text(encoding="utf-8")
        except OSError as exc:
            raise TeamTalkConfigurationError(
                f"could not read message file {args.message_file}: {exc}"
            ) from exc
    else:
        message = args.message or ""
    if not message:
        raise TeamTalkConfigurationError(
            "provide --message, TT_MESSAGE, or --message-file; message cannot be empty"
        )
    return validate_message(message)


def parse_user_ids(values: object) -> list[int]:
    """Parse one or more repeatable or comma-separated TeamTalk user IDs."""

    if values is None:
        return []
    if isinstance(values, (str, int)):
        raw_values = [values]
    else:
        raw_values = list(values)  # type: ignore[arg-type]

    user_ids: list[int] = []
    for raw_value in raw_values:
        for value in str(raw_value).split(","):
            value = value.strip()
            if not value:
                raise TeamTalkConfigurationError(
                    "--user-id must contain one or more numeric user IDs"
                )
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
            user_ids.append(user_id)
    return user_ids


def parse_user_names(values: object) -> list[str]:
    """Parse one or more repeatable or comma-separated recipient usernames."""

    if values is None:
        return []
    if isinstance(values, str):
        raw_values = [values]
    else:
        raw_values = list(values)  # type: ignore[arg-type]

    user_names: list[str] = []
    for raw_value in raw_values:
        for value in str(raw_value).split(","):
            value = value.strip()
            if not value:
                raise TeamTalkConfigurationError(
                    "--user must contain one or more usernames"
                )
            user_names.append(value)
    return user_names


def validate_args(args: argparse.Namespace) -> list[int]:
    user_ids = parse_user_ids(args.user_id)
    user_names = parse_user_names(args.user)
    if args.count < 1:
        raise TeamTalkConfigurationError("--count must be at least 1")
    if args.interval < MIN_REPEAT_INTERVAL:
        raise TeamTalkConfigurationError("--interval cannot be negative")
    if not 0 <= args.wait <= MAX_WAIT:
        raise TeamTalkConfigurationError(f"--wait must be between 0 and {MAX_WAIT:g}s")
    if args.target == "private":
        if not user_ids and not user_names:
            raise TeamTalkConfigurationError(
                "at least one --user or --user-id is required for private messages"
            )
        if len(user_ids) + len(user_names) > MAX_PRIVATE_RECIPIENTS:
            raise TeamTalkConfigurationError(
                f"private messages support at most {MAX_PRIVATE_RECIPIENTS} recipients "
                "per run"
            )
    elif user_ids or user_names:
        raise TeamTalkConfigurationError(
            "--user and --user-id are only valid with --target private"
        )
    return user_ids


def parse_selection_indices(value: str, maximum: int) -> list[int]:
    """Parse comma-separated 1-based selections from the interactive user list."""

    selections: list[int] = []
    for raw_value in value.split(","):
        raw_value = raw_value.strip()
        if not raw_value:
            raise TeamTalkConfigurationError(
                "recipient selection must contain one or more list numbers"
            )
        try:
            selection = int(raw_value)
        except ValueError as exc:
            raise TeamTalkConfigurationError(
                f"invalid recipient list number: {raw_value!r}"
            ) from exc
        if not 1 <= selection <= maximum:
            raise TeamTalkConfigurationError(
                f"recipient list numbers must be between 1 and {maximum}"
            )
        if selection not in selections:
            selections.append(selection)
    return selections


def prompt_numbered_index(label: str, options: Sequence[str]) -> int:
    """Display options as a numbered list and return a zero-based selection."""

    if not options:
        raise TeamTalkConfigurationError(f"no options are available for {label.lower()}")
    print(f"{label}:")
    for index, option in enumerate(options, start=1):
        print(f"  {index}. {option}")
    return (
        prompt_int(
            f"Select {label.lower()} number",
            1,
            minimum=1,
            maximum=len(options),
        )
        - 1
    )


def prompt_target() -> str:
    target_index = prompt_numbered_index(
        "Message target",
        ("Channel message", "Private message"),
    )
    return ("channel", "private")[target_index]


def prompt_channel(session) -> None:
    """Display visible channels and join the selected one on the open session."""

    channels = session.list_channels()
    labels: list[str] = []
    for channel in channels:
        name = str(channel.get("name") or "Root channel")
        path = str(channel.get("path") or "/")
        password_note = " — password required" if channel.get("password_required") else ""
        labels.append(f"{name} ({path}) [ID {channel['id']}]{password_note}")
    selected = channels[prompt_numbered_index("Available channels", labels)]

    configured_password = os.environ.get("TT_CHANNEL_PASSWORD", "")
    password = configured_password
    if selected.get("password_required"):
        password = prompt_text(
            "TeamTalk channel password",
            configured_password or None,
            secret=True,
        )
    session.join_channel(int(selected["id"]), password)
    session.rejoin_channel_id = int(selected["id"])
    session.rejoin_channel_password = password
    print(f"Joined {selected.get('path') or selected.get('name') or selected['id']}.")


def prompt_private_recipients(session) -> tuple[list[str], list[int]]:
    """Select up to 20 online users one at a time for private messages.

    Selected users are returned by NAME (username, or nickname when the
    username is blank) — not by their server-assigned IDs.  The run keeps
    re-scanning the roster by that name before every send, so a recipient who
    relogs mid-run and is handed a brand-new user ID is still found and
    retargeted.  A user with no name at all is returned as a raw ID, because
    an ID is the only identity they have.
    """

    users = session.list_users()
    if not users:
        raise TeamTalkConfigurationError("no online TeamTalk users are available")

    selected_indices: list[int] = []
    while True:
        print("Available private-message recipients:")
        for index, user in enumerate(users, start=1):
            channel_path = str(user.get("channel_path") or "")
            location = f" — {channel_path}" if channel_path else ""
            selected = " [selected]" if index in selected_indices else ""
            print(f"  {index}. {_user_display(user)}{location}{selected}")

        selection_number = len(selected_indices) + 1
        value = prompt_text(f"Type the number for User {selection_number}")
        try:
            selections = parse_selection_indices(value, len(users))
        except TeamTalkConfigurationError as exc:
            print(f"Please choose valid recipient numbers: {exc}")
            continue
        if len(selections) != 1:
            print("Please select one user at a time.")
            continue
        selection = selections[0]
        if selection in selected_indices:
            print("That user is already selected. Choose a different user.")
            continue

        selected_indices.append(selection)
        chosen = users[selection - 1]
        print(f"Selected User {len(selected_indices)}: {_user_display(chosen)}.")
        if len(selected_indices) >= MAX_PRIVATE_RECIPIENTS:
            print(f"Reached the {MAX_PRIVATE_RECIPIENTS}-user selection limit.")
            break
        if not prompt_yes_no(
            f"Select another private-message user? ({len(selected_indices)}/"
            f"{MAX_PRIVATE_RECIPIENTS})",
            True,
        ):
            break

    names: list[str] = []
    anonymous_ids: list[int] = []
    for index in selected_indices:
        user = users[index - 1]
        name = _user_identity(user)
        if name:
            names.append(name)
        else:
            anonymous_ids.append(int(user["id"]))
    return names, anonymous_ids


def send_messages(
    *,
    config,
    message: str,
    count: int,
    interval: float,
    wait: float,
    target: str,
    user_ids: Optional[Sequence[int]] = None,
    user_id: Optional[int] = None,
    user_names: Optional[Sequence[str]] = None,
) -> int:
    if user_id is not None:
        if user_ids is not None:
            raise TeamTalkConfigurationError(
                "provide either user_id or user_ids, not both"
            )
        recipient_ids = [user_id]
    else:
        recipient_ids = list(user_ids or ())
    recipient_names = [str(name).strip() for name in (user_names or ()) if str(name).strip()]

    if target == "private":
        if not recipient_ids and not recipient_names:
            raise TeamTalkConfigurationError(
                "at least one recipient is required for private messages"
            )
        if any(recipient_id < 0 for recipient_id in recipient_ids):
            raise TeamTalkConfigurationError(
                "private-message recipient user IDs must be non-negative"
            )
        if len(recipient_ids) + len(recipient_names) > MAX_PRIVATE_RECIPIENTS:
            raise TeamTalkConfigurationError(
                f"private messages support at most {MAX_PRIVATE_RECIPIENTS} recipients "
                "per run"
            )
    elif recipient_ids or recipient_names:
        raise TeamTalkConfigurationError(
            "recipients are only valid with --target private"
        )

    with TeamTalkSession(config) as session:
        return send_messages_on_session(
            session=session,
            message=message,
            count=count,
            interval=interval,
            wait=wait,
            target=target,
            recipient_ids=recipient_ids,
            recipient_names=recipient_names,
        )


# Upper bound on consecutive failed sends before the run gives up — reconnects
# after kicks are retried, but not forever.
MAX_CONSECUTIVE_FAILURES = 3


def _user_key(user) -> str:
    """Stable identity for a user: username (never the server-assigned ID)."""

    for field in ("username", "nickname"):
        value = str(user.get(field) or "").strip()
        if value:
            return value.casefold()
    return ""


def _user_identity(user) -> str:
    """The name to track this user by: username, else nickname, else nothing.

    A user with neither has no name to be re-found by, so only their raw
    server ID can identify them.
    """

    for field in ("username", "nickname"):
        value = str(user.get(field) or "").strip()
        if value:
            return value
    return ""


def _user_display(user) -> str:
    """Human-readable label: nickname plus @username when the two differ."""

    username = str(user.get("username") or "").strip()
    display = str(
        user.get("display_name") or user.get("nickname") or ""
    ).strip()
    if username and display and display.casefold() != username.casefold():
        return f"{display} (@{username})"
    if display:
        return display
    if username:
        return f"@{username}"
    return f"user {user.get('id')}"


def _resolve_recipients(session: TeamTalkSession, recipient_ids: Sequence[int]) -> list:
    """Map the requested user IDs onto usernames once, up front.

    Server IDs change on every login, so the run tracks each recipient by
    username and re-resolves the current ID at send time.  A recipient that is
    not online keeps its raw ID as a fallback with a printed note.
    """

    roster = session.list_users(include_self=True)
    resolved = []
    for recipient_id in recipient_ids:
        user = next(
            (entry for entry in roster if int(entry["id"]) == int(recipient_id)),
            None,
        )
        if user is None:
            print(f"User ID {recipient_id} is not online right now; keeping the raw ID.")
            resolved.append(
                {"key": "", "id": int(recipient_id), "display": f"user {recipient_id}"}
            )
            continue
        resolved.append(
            {"key": _user_key(user), "id": int(user["id"]), "display": _user_display(user)}
        )
    return resolved


def _current_user_id(session: TeamTalkSession, recipient) -> Optional[int]:
    """Resolve a recipient to its *current* server ID by username."""

    key = recipient["key"]
    if not key:
        return recipient["id"]
    for user in session.list_users(include_self=True):
        if _user_key(user) == key:
            return int(user["id"])
    return None


def _name_recipients(recipient_names: Sequence[str]) -> list[dict[str, object]]:
    """Build recipient entries from --user names; no roster lookup needed.

    A named recipient is tracked by that username from the start — the name is
    the identity, so there is no ID to go stale.
    """

    return [
        {"key": name.casefold(), "id": -1, "display": name}
        for name in recipient_names
        if str(name).strip()
    ]


def _enrich_name_displays(session: TeamTalkSession, recipients: list) -> None:
    """Upgrade name-keyed recipients to their roster display labels.

    A recipient selected as "amy" (typed or from the interactive picker)
    prints as "Amy (@amy)" once the roster is consulted.  Purely cosmetic —
    the send-time identity is the name either way.
    """

    try:
        roster = session.list_users(include_self=True)
    except (TeamTalkError, TeamTalkConfigurationError, OSError):
        return
    by_key = {_user_key(user): user for user in roster}
    for recipient in recipients:
        user = by_key.get(str(recipient.get("key") or ""))
        if user is not None:
            recipient["display"] = _user_display(user)


def send_messages_on_session(
    *,
    session: TeamTalkSession,
    message: str,
    count: int,
    interval: float,
    wait: float,
    target: str,
    recipient_ids: Sequence[int] = (),
    recipient_names: Sequence[str] = (),
) -> int:
    """Send on an already-connected session so interactive selection reconnects once.

    Only delivered messages count: a kick retries the interrupted send after
    reconnecting (re-resolving the recipient's fresh ID by username), so each
    recipient gets exactly ``count`` messages — the run never restarts the
    numbering or multiplies the totals.
    """

    if wait:
        print(f"You have {wait:g} seconds before the API starts sending…")
        time.sleep(wait)

    misses = 0
    if target == "channel":
        try:
            session.rejoin_channel_id = session.current_channel_id()
            # The session may carry no config (interactive selection reuses an
            # already-built session; tests inject bare fakes), so read the
            # rejoin password defensively.
            config = getattr(session, "config", None)
            session.rejoin_channel_password = getattr(config, "channel_password", "") or ""
        except TeamTalkError:
            pass
        sent = 0
        while sent < count:
            try:
                channel_id = session.current_channel_id()
                session.send_channel_message(message, channel_id)
            except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                print(f"[kick-resistance] send {sent + 1} interrupted: {exc}")
                misses += 1
                if misses >= MAX_CONSECUTIVE_FAILURES:
                    print("Too many failed sends in a row; stopping message test.")
                    return 1
                if not session.check_and_reconnect():
                    print("Could not reconnect; stopping message test.")
                    return 1
                continue
            misses = 0
            sent += 1
            print(f"Sent {sent}/{count} to channel {channel_id}.")
            if sent < count and interval:
                time.sleep(interval)
        print("Finished sending.")
        return 0

    recipients = _resolve_recipients(session, recipient_ids) + _name_recipients(
        recipient_names
    )
    _enrich_name_displays(session, recipients)
    for index in range(count):
        for recipient_index, recipient in enumerate(recipients, start=1):
            while True:
                user_id = _current_user_id(session, recipient)
                if user_id is None:
                    print(
                        f"{recipient['display']} is not online; "
                        "skipping them this round."
                    )
                    break
                try:
                    session.send_private_message(message, user_id)
                except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
                    print(
                        f"[kick-resistance] send to {recipient['display']} "
                        f"interrupted: {exc}"
                    )
                    misses += 1
                    if misses >= MAX_CONSECUTIVE_FAILURES:
                        print("Too many failed sends in a row; stopping message test.")
                        return 1
                    if not session.check_and_reconnect():
                        print("Could not reconnect; stopping message test.")
                        return 1
                    continue  # retry the same send; the counters do not move
                misses = 0
                break
            if user_id is None:
                continue
            if len(recipients) == 1:
                print(f"Sent {index + 1}/{count} to {recipient['display']}.")
            else:
                print(
                    f"Sent message {index + 1}/{count} to {recipient['display']} "
                    f"({recipient_index}/{len(recipients)} recipients)."
                )
            if (
                recipient_index < len(recipients) or index + 1 < count
            ) and interval:
                time.sleep(interval)
    print("Finished sending.")
    return 0


def run(args: argparse.Namespace) -> int:
    user_ids = validate_args(args)
    user_names = parse_user_names(args.user)
    message = read_message(args)
    config = config_from_args(args)
    return send_messages(
        config=config,
        message=message,
        count=args.count,
        interval=args.interval,
        wait=args.wait,
        target=args.target,
        user_ids=user_ids,
        user_names=user_names,
    )


def interactive_run() -> int:
    config = prompt_connection_config(channel_required=False)
    with TeamTalkSession(config) as session:
        print("Connected and logged in to TeamTalk.")
        target = prompt_target()
        user_ids: Optional[list[int]] = None
        user_names: list[str] = []
        if target == "channel":
            prompt_channel(session)
        else:
            # Selected users come back by name so the run keeps re-resolving
            # their current ID from the roster at every send.
            user_names, user_ids = prompt_private_recipients(session)

        message = validate_message(prompt_text("Send what text?", DEFAULT_MESSAGE))
        count = prompt_int(
            "How many times to send the text?",
            DEFAULT_COUNT,
            minimum=1,
        )
        interval_ms = prompt_float(
            "How fast to send, in milliseconds?",
            DEFAULT_INTERVAL_MS,
            minimum=0.0,
        )
        wait = prompt_float(
            "Time to wait prior to sending, in seconds?",
            DEFAULT_WAIT,
            minimum=0.0,
            maximum=MAX_WAIT,
        )
        return send_messages_on_session(
            session=session,
            message=message,
            count=count,
            interval=interval_ms / 1000.0,
            wait=wait,
            target=target,
            recipient_ids=user_ids or (),
            recipient_names=user_names,
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
