"""Live message round-trip tests against this repo's local TeamTalk server.

Unlike the rest of the suite these are real integration tests: they connect
to the TeamTalk server shipped in ``local_server/`` (start it with
``python3 local_server/start.py``) and verify that messages actually transmit to
the server and arrive on the other side through every sending path the suite
has — the session layer's login/logout, channel messages, private (user)
messages, the ``tt_message_spammer`` tool for both targets, the
``tt_spammer`` login/logout tool, and the ``tt_concurrent_bots`` idle bots.

The whole module skips itself when the local server is not listening, so an
ordinary ``pytest`` run stays hermetic; start the server first to opt in.

Accounts come from the local server's own config (``local_server/tt5srv.xml``):
``loadtest``/``loadtest`` as the sender and ``loadadmin``/``loadadmin`` as the
receiver, so private-message targeting by username stays unambiguous.
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tt_teamtalk import (
    ConnectionConfig,
    TeamTalkError,
    TeamTalkSession,
    message_fields,
    sdk_event,
    sdk_int,
)

import tt_concurrent_bots
import tt_message_spammer
import tt_spammer


HOST = "127.0.0.1"
TCP_PORT = 10333
UDP_PORT = 10333
CHANNEL_PATH = "/LoadTest"
SENDER_USERNAME = "loadtest"
SENDER_PASSWORD = "loadtest"
RECEIVER_USERNAME = "loadadmin"
RECEIVER_PASSWORD = "loadadmin"

# TeamTalk 5 TextMsgType constants (stable across the SDK).
MSGTYPE_USER = 1
MSGTYPE_CHANNEL = 2

REPO_ROOT = Path(__file__).resolve().parents[2]
SDK_PYTHON = REPO_ROOT / "sdk" / "TeamTalk5.py"
SDK_LIBRARY = REPO_ROOT / "sdk" / "libTeamTalk5.so"

WAIT_TIMEOUT = 10.0  # seconds to wait for one message to arrive
BOT_TIMEOUT = 20.0   # seconds to wait for idle bots to appear on the roster


def _server_reachable() -> bool:
    """True when the local TeamTalk server is listening."""

    try:
        with socket.create_connection((HOST, TCP_PORT), timeout=1.0):
            return True
    except OSError:
        return False


if not _server_reachable():
    pytest.skip(
        "local TeamTalk server is not running; start it with python3 local_server/start.py",
        allow_module_level=True,
    )


def _config(username: str, password: str, nickname: str, *, channel: bool = True):
    """A connection config for the local server with the SDK license pre-approved."""

    return ConnectionConfig(
        host=HOST,
        tcp_port=TCP_PORT,
        udp_port=UDP_PORT,
        username=username,
        password=password,
        nickname=nickname,
        channel_path=CHANNEL_PATH if channel else None,
        channel_password="",
        accept_sdk_license=True,
        sdk_python=str(SDK_PYTHON),
        sdk_library=str(SDK_LIBRARY),
    )


def _marker(prefix: str) -> str:
    """A message text unique across runs."""

    return f"{prefix}-{time.monotonic_ns() % 1_000_000_000}"


def _await_text(
    session: TeamTalkSession,
    *,
    text: str,
    msg_type: int,
    from_user_id: int | None = None,
    to_user_id: int | None = None,
    timeout: float = WAIT_TIMEOUT,
) -> dict:
    """Poll until the message matching every given field arrives, or fail.

    Non-matching text events are skipped, not consumed-as-errors: a run may
    relay unrelated messages (bot join notices, earlier markers) before the
    one this call is waiting for.
    """

    wanted_event = sdk_event(session.sdk, "CLIENTEVENT_CMD_USER_TEXTMSG")
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        message = session.poll(max(1, int((deadline - time.monotonic()) * 1000)))
        if session.is_connection_failure(message):
            raise TeamTalkError("the session lost its connection while waiting")
        if sdk_int(getattr(message, "nClientEvent", 0)) != wanted_event:
            continue
        fields = message_fields(getattr(message, "textmessage", None))
        if fields["text"] != text or fields["type"] != msg_type:
            continue
        if from_user_id is not None and fields["from_user_id"] != from_user_id:
            continue
        if to_user_id is not None and fields["to_user_id"] != to_user_id:
            continue
        return fields
    raise TeamTalkError(
        f"no {msg_type} message {text!r} arrived within {timeout:g}s"
    )


@pytest.fixture
def sender_session():
    """A logged-in sender (loadtest) in the /LoadTest channel."""

    session = TeamTalkSession(_config(SENDER_USERNAME, SENDER_PASSWORD, "live-sender"))
    session.open()
    yield session
    session.close()


@pytest.fixture
def receiver_session():
    """A logged-in receiver (loadadmin) in the same channel to read messages."""

    session = TeamTalkSession(
        _config(RECEIVER_USERNAME, RECEIVER_PASSWORD, "live-receiver")
    )
    session.open()
    yield session
    session.close()


class TestSessionRoundtrip:
    """The session layer itself: connect, login, logout, login again."""

    def test_login_logout_cycle(self):
        config = _config(SENDER_USERNAME, SENDER_PASSWORD, "live-cycle", channel=False)
        session = TeamTalkSession(config)
        try:
            session.open()
            assert session.logged_in
            user_id = sdk_int(session.client.getMyUserID(), -1)
            assert user_id > 0

            session.logout()
            assert not session.logged_in

            session.login()
            assert session.logged_in
            assert sdk_int(session.client.getMyUserID(), -1) == user_id
        finally:
            session.close()

    def test_channel_message_transmitted_and_received(
        self, sender_session, receiver_session
    ):
        marker = _marker("live-channel")
        channel_id = sender_session.current_channel_id()
        sender_id = sdk_int(sender_session.client.getMyUserID(), -1)

        sender_session.send_channel_message(marker, channel_id)

        fields = _await_text(
            receiver_session,
            text=marker, msg_type=MSGTYPE_CHANNEL, from_user_id=sender_id,
        )
        assert fields["channel_id"] == channel_id

    def test_private_message_transmitted_and_received(
        self, sender_session, receiver_session
    ):
        marker = _marker("live-private")
        sender_id = sdk_int(sender_session.client.getMyUserID(), -1)
        receiver_id = sdk_int(receiver_session.client.getMyUserID(), -1)
        assert receiver_id > 0

        sender_session.send_private_message(marker, receiver_id)

        # The recipient's copy of a private message carries the sender's ID
        # but nToUserID 0 (the server addresses it, the field is not echoed
        # back), so the match is on sender + type + text.
        fields = _await_text(
            receiver_session,
            text=marker, msg_type=MSGTYPE_USER, from_user_id=sender_id,
        )
        assert fields["to_user_id"] == 0
        assert fields["channel_id"] == 0


class TestToolRoundtrip:
    """The suite's message tools against the same live server."""

    @staticmethod
    def _tool_args(target: str, marker: str, *target_args: str) -> list[str]:
        return [
            "--host", HOST,
            "--tcp-port", str(TCP_PORT),
            "--udp-port", str(UDP_PORT),
            "--username", SENDER_USERNAME,
            "--password", SENDER_PASSWORD,
            "--nickname", "live-tool-sender",
            "--channel-path", CHANNEL_PATH,
            "--accept-sdk-license",
            "--target", target,
            "--message", marker,
            "--count", "1",
            *target_args,
        ]

    def test_message_spammer_channel_send_is_received(self, receiver_session):
        marker = _marker("live-tool-channel")
        args = tt_message_spammer.build_parser().parse_args(
            self._tool_args("channel", marker)
        )
        assert tt_message_spammer.run(args) == 0
        _await_text(receiver_session, text=marker, msg_type=MSGTYPE_CHANNEL)

    def test_message_spammer_private_send_is_received(self, receiver_session):
        marker = _marker("live-tool-private")
        args = tt_message_spammer.build_parser().parse_args(
            self._tool_args("private", marker, "--user", RECEIVER_USERNAME)
        )
        assert tt_message_spammer.run(args) == 0
        _await_text(receiver_session, text=marker, msg_type=MSGTYPE_USER)

    def test_login_logout_tool_runs_cycles(self):
        config = _config(SENDER_USERNAME, SENDER_PASSWORD, "live-cycle-tool", channel=False)
        assert tt_spammer.run_cycles(
            config=config, cycles=2, interval=0.05, wait=0.0
        ) == 0


class TestConcurrentBots:
    """Idle bots come online concurrently, and messages still flow past them."""

    def test_bots_online_and_messages_still_transmit(
        self, sender_session, receiver_session
    ):
        bot_nickname = _marker("live-idle-bot")
        expected = {f"{bot_nickname}-{index}" for index in range(3)}
        process = subprocess.Popen(
            [
                sys.executable, str(REPO_ROOT / "tt_concurrent_bots.py"),
                "--host", HOST,
                "--tcp-port", str(TCP_PORT),
                "--udp-port", str(UDP_PORT),
                "--username", SENDER_USERNAME,
                "--password", SENDER_PASSWORD,
                "--nickname", bot_nickname,
                "--channel-path", CHANNEL_PATH,
                "--accept-sdk-license",
                "--sdk-python", str(SDK_PYTHON),
                "--sdk-library", str(SDK_LIBRARY),
                "--count", "3",
                "--confirm",
            ],
            cwd=REPO_ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            deadline = time.monotonic() + BOT_TIMEOUT
            seen: set[str] = set()
            while time.monotonic() < deadline:
                seen = {user["nickname"] for user in receiver_session.list_users()}
                if expected <= seen:
                    break
                time.sleep(0.5)
            assert expected <= seen, (
                f"idle bots never appeared on the roster: missing {expected - seen}"
            )

            # With all three bots sitting in the channel, a real message must
            # still travel sender -> server -> receiver.
            marker = _marker("live-during-bots")
            channel_id = sender_session.current_channel_id()
            sender_session.send_channel_message(marker, channel_id)
            _await_text(receiver_session, text=marker, msg_type=MSGTYPE_CHANNEL)
        finally:
            # SIGINT takes the tool through its own graceful shutdown path
            # ("Interrupted: terminating workers..."); terminate is the fallback.
            process.send_signal(2)  # SIGINT
            try:
                process.wait(timeout=10.0)
            except subprocess.TimeoutExpired:
                process.terminate()
                process.wait(timeout=10.0)

    def test_concurrent_bots_dry_run_needs_no_server(self):
        """--dry-run plans the launch without connecting; safe with no server."""

        parser = tt_concurrent_bots.build_parser()
        args = parser.parse_args(["--count", "2", "--dry-run"])
        tt_concurrent_bots.validate_args(args)  # bounds gate, no connection
        assert args.count == 2
        assert args.dry_run is True