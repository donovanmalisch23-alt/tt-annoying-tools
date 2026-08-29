"""Shared fixtures for the TT Annoying Tools test suite.

Every test here is hermetic: no TeamTalk server, no network, and no real SDK.
``FakeSDK``/``FakeClient`` emulate the official ``TeamTalk5`` module and its
ctypes client in memory, and ``FakeToolSession`` stands in for the high-level
``TeamTalkSession`` when a test only needs call recording.

Environment isolation happens at import time (before any repo module loads):
``tt_teamtalk`` reads ``TT_ENV_FILE`` when it is first imported, so pointing it
at ``os.devnull`` (a non-regular file) guarantees the developer's local
``teamtalk.env`` credentials are never picked up by a test run.
"""

from __future__ import annotations

import collections
import os
import sys
import time
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# --- import-time environment isolation ------------------------------------ #
os.environ["TT_ENV_FILE"] = os.devnull
for _key in [k for k in os.environ if k.startswith(("TT_", "TEAMTALK_"))]:
    if _key != "TT_ENV_FILE":
        os.environ.pop(_key, None)
os.environ.setdefault("TT_SHUTDOWN_SETTLE_SECONDS", "0")

sys.path.insert(0, str(REPO_ROOT))

import tt_teamtalk  # noqa: E402  (after env isolation, on purpose)


# --------------------------------------------------------------------------- #
# Fake TeamTalk5 SDK (emulates the ctypes interface in memory)
# --------------------------------------------------------------------------- #

class FakeClientEvent:
    CLIENTEVENT_NONE = 0
    CLIENTEVENT_CON_SUCCESS = 1
    CLIENTEVENT_CON_FAILED = 2
    CLIENTEVENT_CON_CRYPT_ERROR = 3
    CLIENTEVENT_CON_LOST = 4
    CLIENTEVENT_CMD_SUCCESS = 5
    CLIENTEVENT_CMD_ERROR = 6
    CLIENTEVENT_CMD_PROCESSING = 7
    CLIENTEVENT_CMD_MYSELF_LOGGEDIN = 8
    CLIENTEVENT_CMD_MYSELF_LOGGEDOUT = 9
    CLIENTEVENT_CMD_USER_TEXTMSG = 10


class FakeTextMsgType:
    MSGTYPE_USER = 1
    MSGTYPE_CHANNEL = 2
    MSGTYPE_BROADCAST = 3


@dataclass
class FakeTTMessage:
    """Stand-in for the SDK's ``TTMessage`` event structure."""

    nClientEvent: int = 0
    nSource: int = -1
    textmessage: Any = None
    clienterrormsg: Any = None
    bActive: int = 0


def make_text_message(
    text: str,
    *,
    msg_type: int = FakeTextMsgType.MSGTYPE_USER,
    from_user_id: int = 5,
    from_username: str = "sender",
    to_user_id: int = 0,
    channel_id: int = 0,
    more: bool = False,
) -> types.SimpleNamespace:
    """Build a stand-in for the SDK's ``TextMessage`` structure."""

    return types.SimpleNamespace(
        nMsgType=msg_type,
        nFromUserID=from_user_id,
        szFromUsername=from_username,
        nToUserID=to_user_id,
        nChannelID=channel_id,
        szMessage=text,
        bMore=more,
    )


def _build_text_message(text, msgtype, nChannelID=0, nToUserID=0):
    """Mirror of the SDK helper: one full message per call (no splitting)."""

    return [
        types.SimpleNamespace(
            szMessage=text,
            nMsgType=msgtype,
            nChannelID=nChannelID,
            nToUserID=nToUserID,
            nFromUserID=0,
            szFromUsername="",
            bMore=False,
        )
    ]


class FakeClient:
    """In-memory replacement for ``sdk.TeamTalk()``.

    Commands push their completion events onto an internal queue, which the
    session's background pump drains exactly as it would with the real native
    library — so ``TeamTalkSession`` is exercised end to end, just without a
    server.
    """

    def __init__(self) -> None:
        self._queue: collections.deque = collections.deque()
        self._cmd_counter = 0
        self.connected = False
        self.logged_in = False
        self.channel_id = 0
        self.my_user_id = 0
        self.channels: dict[int, str] = {1: "/"}
        self.users: list[dict[str, Any]] = []
        self.channel_users: dict[int, list[dict[str, Any]]] = {}
        self.channel_passwords: dict[int, str] = {}
        self.sent_messages: list[Any] = []
        self.closed = False
        self.disconnected = False
        self.fail_commands = False
        self.reject_connect = False
        self.join_error: Optional[tuple[int, str]] = None

    # -- event plumbing ----------------------------------------------------- #
    def _next_cmd(self) -> int:
        self._cmd_counter += 1
        return self._cmd_counter

    def inject_event(self, message: FakeTTMessage) -> None:
        """Queue an unsolicited server event (kick notice, text message...)."""

        self._queue.append(message)

    def getMessage(self, nWaitMS: int = 0, *args: Any, **kwargs: Any) -> FakeTTMessage:
        if self._queue:
            return self._queue.popleft()
        # No event: sleep a sliver so the pump thread does not busy-spin,
        # then hand back CLIENTEVENT_NONE like the real wait would.
        time.sleep(0.001)
        return FakeTTMessage(FakeClientEvent.CLIENTEVENT_NONE)

    # -- commands ------------------------------------------------------------ #
    def connect(self, host, tcp_port, udp_port, *args):
        if self.reject_connect:
            return False
        self.connected = True
        self._queue.append(FakeTTMessage(FakeClientEvent.CLIENTEVENT_CON_SUCCESS))
        return True

    def disconnect(self):
        self.connected = False
        self.logged_in = False
        self.disconnected = True
        return 0

    def doLogin(self, nickname, username, password, client_name):
        if self.fail_commands or not self.connected:
            return -1
        cmd = self._next_cmd()
        self.logged_in = True
        self.my_user_id = 100
        self._queue.append(
            FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_MYSELF_LOGGEDIN, nSource=cmd)
        )
        return cmd

    def doLogout(self):
        if self.fail_commands:
            return -1
        cmd = self._next_cmd()
        self.logged_in = False
        self.my_user_id = 0
        self._queue.append(
            FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_MYSELF_LOGGEDOUT, nSource=cmd)
        )
        return cmd

    def doJoinChannelByID(self, channel_id, password):
        if self.fail_commands:
            return -1
        cmd = self._next_cmd()
        if self.join_error is not None:
            number, text = self.join_error
            self._queue.append(
                FakeTTMessage(
                    FakeClientEvent.CLIENTEVENT_CMD_ERROR,
                    nSource=cmd,
                    clienterrormsg=types.SimpleNamespace(
                        nErrorNo=number, szErrorMsg=text
                    ),
                )
            )
            return cmd
        required = self.channel_passwords.get(int(channel_id))
        if required and required != password:
            self._queue.append(
                FakeTTMessage(
                    FakeClientEvent.CLIENTEVENT_CMD_ERROR,
                    nSource=cmd,
                    clienterrormsg=types.SimpleNamespace(
                        nErrorNo=2004, szErrorMsg="incorrect channel password"
                    ),
                )
            )
            return cmd
        self.channel_id = int(channel_id)
        self._queue.append(
            FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_SUCCESS, nSource=cmd)
        )
        return cmd

    def doLeaveChannel(self):
        if self.fail_commands:
            return -1
        cmd = self._next_cmd()
        self.channel_id = 0
        self._queue.append(
            FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_SUCCESS, nSource=cmd)
        )
        return cmd

    def doTextMessage(self, message):
        if self.fail_commands:
            return -1
        cmd = self._next_cmd()
        self.sent_messages.append(message)
        self._queue.append(
            FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_SUCCESS, nSource=cmd)
        )
        return cmd

    # -- queries -------------------------------------------------------------- #
    def getMyUserID(self):
        return self.my_user_id if self.logged_in else 0

    def getMyChannelID(self):
        return self.channel_id

    def getChannelIDFromPath(self, path):
        wanted = tt_teamtalk.sdk_text(path)
        for channel_id, channel_path in self.channels.items():
            if channel_path == wanted:
                return channel_id
        return 0

    def getChannelPath(self, channel_id):
        return self.channels.get(int(channel_id), "/")

    def getServerChannels(self):
        return [
            types.SimpleNamespace(
                nChannelID=cid,
                nParentID=0,
                szName=path.strip("/") or "root",
                bPassword=cid in self.channel_passwords,
                uChannelType=0,
            )
            for cid, path in sorted(self.channels.items())
        ]

    def getServerUsers(self):
        return [
            types.SimpleNamespace(
                nUserID=user["id"],
                nChannelID=user.get("channel_id", 1),
                szNickname=user.get("nickname", ""),
                szUsername=user.get("username", ""),
            )
            for user in self.users
        ]

    def getChannelUsers(self, channel_id):
        return [
            types.SimpleNamespace(
                nUserID=user["id"],
                nChannelID=int(channel_id),
                szNickname=user.get("nickname", ""),
                szUsername=user.get("username", ""),
            )
            for user in self.channel_users.get(int(channel_id), [])
        ]

    def getErrorMessage(self, number):
        return f"fake error {number}"

    def closeTeamTalk(self):
        self.closed = True
        return True


def make_fake_sdk(configure: Optional[Callable[[FakeClient], None]] = None):
    """Build (sdk_module_stub, client); ``configure`` pre-seeds the client."""

    client = FakeClient()
    if configure is not None:
        configure(client)
    license_calls: list[tuple[Any, Any]] = []

    sdk = types.SimpleNamespace(
        TeamTalk=lambda: client,
        ClientEvent=FakeClientEvent,
        TextMsgType=FakeTextMsgType,
        ttstr=lambda value: value,
        buildTextMessage=_build_text_message,
        setLicense=lambda name, key: license_calls.append((name, key)) is None or True,
    )
    sdk.license_calls = license_calls
    return sdk, client


@pytest.fixture
def fake_sdk():
    return make_fake_sdk


@pytest.fixture
def make_config():
    """``ConnectionConfig`` factory pre-accepting the SDK license gate."""

    def factory(**overrides: Any) -> tt_teamtalk.ConnectionConfig:
        options: dict[str, Any] = {
            "host": "whitelisted.local",
            "accept_sdk_license": True,
            "command_timeout": 2.0,
            "reconnect_delay": 0.0,
        }
        options.update(overrides)
        return tt_teamtalk.ConnectionConfig(**options)

    return factory


# --------------------------------------------------------------------------- #
# FakeToolSession: high-level stand-in for TeamTalkSession (call recording)
# --------------------------------------------------------------------------- #

@dataclass
class FakeToolSession:
    """Records high-level bot operations without any SDK involvement.

    Tools under test call ``TeamTalkSession(config)``; monkeypatch the tool
    module's ``TeamTalkSession`` symbol with a factory producing these.  All
    state transitions are synchronous and nothing blocks.
    """

    config: Any
    auto_connect: bool = True
    connected: bool = False
    logged_in: bool = False
    channel_id: Optional[int] = None
    rejoin_channel_id: Optional[int] = None
    rejoin_channel_password: str = ""
    channels: list[dict[str, Any]] = field(default_factory=list)
    users: list[dict[str, Any]] = field(default_factory=list)
    poll_script: list[Any] = field(default_factory=list)
    client: Any = field(
        default_factory=lambda: types.SimpleNamespace(getMyUserID=lambda: 100)
    )
    sdk: Any = field(
        default_factory=lambda: types.SimpleNamespace(ClientEvent=FakeClientEvent)
    )

    # recorded operations
    login_calls: int = 0
    logout_calls: int = 0
    joins: list[tuple[int, str]] = field(default_factory=list)
    leaves: int = 0
    channel_messages: list[tuple[str, int]] = field(default_factory=list)
    private_messages: list[tuple[str, int]] = field(default_factory=list)
    reconnect_calls: int = 0
    reconnect_result: bool = True

    # scripted failures
    fail_login: bool = False
    fail_join_ids: set[int] = field(default_factory=set)
    fail_private: bool = False

    def __enter__(self) -> "FakeToolSession":
        if self.auto_connect:
            self.connected = True
            self.logged_in = True
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def close(self, *args: Any, **kwargs: Any) -> None:
        self.connected = False
        self.logged_in = False

    def login(self) -> None:
        if self.fail_login:
            raise tt_teamtalk.TeamTalkError("login failed (scripted)")
        self.logged_in = True
        self.login_calls += 1

    def logout(self) -> None:
        if not self.logged_in:
            raise tt_teamtalk.TeamTalkError("not logged in (scripted)")
        self.logged_in = False
        self.logout_calls += 1

    def is_online(self) -> bool:
        return self.connected and self.logged_in

    def check_and_reconnect(self) -> bool:
        self.reconnect_calls += 1
        if self.reconnect_result:
            self.connected = True
            self.logged_in = True
        return self.reconnect_result

    def is_connection_failure(self, message: Any) -> bool:
        return bool(getattr(message, "is_failure", False))

    def list_channels(self) -> list[dict[str, Any]]:
        return list(self.channels)

    def list_users(self, include_self: bool = False) -> list[dict[str, Any]]:
        return list(self.users)

    def join_channel(self, channel_id: int, password: str = "") -> int:
        if int(channel_id) in self.fail_join_ids:
            raise tt_teamtalk.TeamTalkError("join failed (scripted)")
        self.joins.append((int(channel_id), password))
        self.channel_id = int(channel_id)
        return self.channel_id

    def join_channel_path(self, path: str, password: str = "") -> int:
        wanted = path if str(path).startswith("/") else f"/{path}"
        for channel in self.channels:
            if str(channel.get("path") or "/") == wanted:
                return self.join_channel(int(channel["id"]), password)
        raise tt_teamtalk.TeamTalkError(f"TeamTalk channel path was not found: {path}")

    def leave_channel(self) -> None:
        self.leaves += 1
        self.channel_id = None

    def current_channel_id(self) -> int:
        if self.channel_id is None:
            raise tt_teamtalk.TeamTalkConfigurationError(
                "the client is not in a channel; supply --channel-id or --channel-path"
            )
        return int(self.channel_id)

    def send_channel_message(self, text: str, channel_id: Optional[int] = None) -> None:
        self.channel_messages.append((text, int(channel_id or self.channel_id or 0)))

    def send_private_message(self, text: str, user_id: int) -> None:
        if self.fail_private:
            raise tt_teamtalk.TeamTalkError("send failed (scripted)")
        self.private_messages.append((text, int(user_id)))

    def poll(self, wait_ms: int = 1000) -> Any:
        if self.poll_script:
            return self.poll_script.pop(0)
        return types.SimpleNamespace(nClientEvent=0)


@pytest.fixture
def tool_session_factory():
    """Return (factory, created) to inject ``FakeToolSession`` into a tool.

    ``factory`` replaces a tool module's ``TeamTalkSession`` symbol; every
    session the tool opens is appended to ``created`` for assertions.
    """

    created: list[FakeToolSession] = []

    def factory(**overrides: Any) -> Callable[..., FakeToolSession]:
        def ctor(config: Any, *args: Any, **kwargs: Any) -> FakeToolSession:
            session = FakeToolSession(config)
            for key, value in overrides.items():
                setattr(session, key, value)
            created.append(session)
            return session

        return ctor

    return factory, created
