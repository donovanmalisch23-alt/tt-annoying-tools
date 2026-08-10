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
        "--allow-user-id",
        type=int,
        action="append",
        default=[],
        help="user ID allowed to receive responses; repeat for multiple users",
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
        type=int,
        default=100,
        help="responses before exit; 0 means unlimited with --confirm",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="confirm unlimited bot operation when --max-responses 0 is used",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not args.trigger:
        raise TeamTalkConfigurationError("--trigger cannot be empty")
    if len(args.response.encode("utf-8")) > 4096:
        raise TeamTalkConfigurationError("--response is limited to 4096 UTF-8 bytes")
    if any(user_id < 0 for user_id in args.allow_user_id):
        raise TeamTalkConfigurationError("--allow-user-id values cannot be negative")
    if not args.allow_user_id and not args.allow_all:
        raise TeamTalkConfigurationError(
            "provide at least one --allow-user-id or explicitly use --allow-all"
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
        last_reply: dict[int, float] = {}
        response_count = 0
        last_check = 0.0
        reconnect_delay = config.reconnect_delay
        print(
            f"Listening in channel {channel_id}; trigger {args.trigger!r}. "
            "Press Ctrl+C to stop."
        )

        while args.max_responses == 0 or response_count < args.max_responses:
            message = session.poll(1000)
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
            if not args.allow_all and sender_id not in set(args.allow_user_id):
                continue
            if incoming["more"] or not str(incoming["text"]).startswith(args.trigger):
                continue
            now = time.monotonic()
            if now - last_reply.get(sender_id, 0.0) < args.cooldown:
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
            last_reply[sender_id] = now
            response_count += 1
            print(f"Replied to user {sender_id} ({response_count}/{args.max_responses or '∞'}).")
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
