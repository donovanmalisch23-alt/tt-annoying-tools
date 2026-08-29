"""End-to-end-ish tests for TeamTalkSession against the in-memory FakeSDK.

These exercise the event pump, _wait_for, command validation, channel joins,
messaging, kick detection, reconnect bookkeeping, and cleanup — all without a
TeamTalk server or the native library.
"""

from __future__ import annotations

import types

import pytest

import tt_teamtalk
from conftest import FakeClientEvent, FakeTTMessage, make_text_message
from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
)


@pytest.fixture
def session_pair(make_config, fake_sdk):
    """Yield (session, client) opened around the test and closed after it."""

    created = []

    def factory(configure_client=None, **config_overrides):
        sdk, client = fake_sdk(configure_client)
        session = TeamTalkSession(make_config(**config_overrides), sdk=sdk)
        created.append(session)
        return session, client

    yield factory
    for session in created:
        session.close()


class TestConnectLogin:
    def test_open_connects_and_logs_in(self, session_pair):
        session, client = session_pair()
        session.open()
        assert session.connected and session.logged_in
        assert client.connected and client.logged_in

    def test_context_manager(self, session_pair):
        session, client = session_pair()
        with session:
            assert session.is_online()
        assert client.closed

    def test_rejected_connect_raises(self, session_pair):
        session, _client = session_pair(lambda c: setattr(c, "reject_connect", True))
        with pytest.raises(TeamTalkError, match="rejected the connection request"):
            session.open()

    def test_login_requires_connect(self, session_pair):
        session, _client = session_pair()
        with pytest.raises(TeamTalkConfigurationError, match="connect before"):
            session.login()

    def test_login_local_failure(self, session_pair):
        session, client = session_pair()
        session.connected = True  # pretend only the TCP layer is up
        client.connected = True
        client.fail_commands = True
        with pytest.raises(TeamTalkError, match="rejected by TeamTalk locally"):
            session.login()

    def test_open_is_idempotent(self, session_pair):
        session, _client = session_pair()
        session.open()
        session.open()
        assert session.logged_in

    def test_apply_license_called_with_name(self):
        from conftest import make_fake_sdk

        sdk, _client = make_fake_sdk()
        session = TeamTalkSession(
            tt_teamtalk.ConnectionConfig(
                host="h",
                accept_sdk_license=True,
                command_timeout=2.0,
                license_name="Registered Name",
                license_key="KEY-123",
            ),
            sdk=sdk,
        )
        try:
            session.open()
        finally:
            session.close()
        assert sdk.license_calls == [("Registered Name", "KEY-123")]

    def test_apply_license_skipped_without_name(self, session_pair):
        from conftest import make_fake_sdk

        sdk, client = make_fake_sdk()
        session = TeamTalkSession(
            tt_teamtalk.ConnectionConfig(
                host="h", accept_sdk_license=True, command_timeout=2.0
            ),
            sdk=sdk,
        )
        try:
            session.open()
        finally:
            session.close()
        assert sdk.license_calls == []


class TestChannels:
    def test_join_sets_channel(self, session_pair):
        session, client = session_pair(lambda c: c.channels.update({7: "/Games"}))
        session.open()
        assert session.join_channel(7) == 7
        assert session.channel_id == 7
        assert client.channel_id == 7

    def test_join_current_channel_is_noop(self, session_pair):
        session, client = session_pair()
        session.open()
        session.join_channel(3)
        before = client._cmd_counter
        session.join_channel(3)
        assert client._cmd_counter == before  # no command issued

    def test_join_negative_id_rejected(self, session_pair):
        session, _client = session_pair()
        session.open()
        with pytest.raises(TeamTalkConfigurationError, match="cannot be negative"):
            session.join_channel(-1)

    def test_join_local_failure(self, session_pair):
        session, client = session_pair()
        session.open()
        client.fail_commands = True
        with pytest.raises(TeamTalkError, match="rejected by TeamTalk locally"):
            session.join_channel(9)

    def test_join_command_error_carries_message(self, session_pair):
        session, client = session_pair()
        session.open()
        client.join_error = (2004, "incorrect channel password")
        with pytest.raises(TeamTalkError, match="incorrect channel password"):
            session.join_channel(9)

    def test_join_channel_path(self, session_pair):
        session, _client = session_pair(
            lambda c: c.channels.update({12: "/Lobby/Games"})
        )
        session.open()
        assert session.join_channel_path("/Lobby/Games") == 12
        assert session.channel_id == 12

    def test_join_unknown_channel_path_times_out(self, session_pair):
        session, _client = session_pair(command_timeout=0.2)
        session.open()
        with pytest.raises(TeamTalkError, match="was not found"):
            session.join_channel_path("/Nowhere")

    def test_leave_channel_clears_state(self, session_pair):
        session, client = session_pair()
        session.open()
        session.join_channel(5)
        session.leave_channel()
        assert session.channel_id is None
        assert client.channel_id == 0

    def test_auto_join_configured_channel_on_open(self, session_pair):
        session, client = session_pair(channel_id=4)
        session.open()
        assert client.channel_id == 4
        assert session.channel_id == 4

    def test_auto_join_by_path_on_open(self, session_pair):
        session, client = session_pair(
            lambda c: c.channels.update({8: "/Deep"}), channel_path="/Deep"
        )
        session.open()
        assert client.channel_id == 8

    def test_current_channel_id_uses_cache_then_client(self, session_pair):
        session, client = session_pair()
        session.open()
        client.channel_id = 11
        assert session.current_channel_id() == 11
        assert session.channel_id == 11

    def test_current_channel_id_without_channel_raises(self, session_pair):
        session, _client = session_pair()
        session.open()
        with pytest.raises(TeamTalkConfigurationError, match="not in a channel"):
            session.current_channel_id()

    def test_channel_password_required_enforced(self, session_pair):
        def configure(client):
            client.channels[22] = "/Locked"
            client.channel_passwords[22] = "pw"

        session, _client = session_pair(configure)
        session.open()
        with pytest.raises(TeamTalkError, match="incorrect channel password"):
            session.join_channel(22, "wrong")
        assert session.join_channel(22, "pw") == 22


class TestMessaging:
    def test_send_channel_message(self, session_pair):
        session, client = session_pair(channel_id=2)
        session.open()
        session.send_channel_message("hello channel")
        assert len(client.sent_messages) == 1
        msg = client.sent_messages[0]
        assert msg.szMessage == "hello channel"
        assert msg.nMsgType == 2  # MSGTYPE_CHANNEL
        assert msg.nChannelID == 2

    def test_send_private_message(self, session_pair):
        session, client = session_pair()
        session.open()
        session.send_private_message("psst", 42)
        msg = client.sent_messages[0]
        assert msg.nMsgType == 1  # MSGTYPE_USER
        assert msg.nToUserID == 42

    def test_empty_message_rejected(self, session_pair):
        session, _client = session_pair(channel_id=2)
        session.open()
        with pytest.raises(TeamTalkConfigurationError, match="cannot be empty"):
            session.send_channel_message("")

    def test_exactly_one_target_required(self, session_pair):
        session, _client = session_pair()
        session.open()
        with pytest.raises(TeamTalkConfigurationError, match="exactly one"):
            session.send_text("x")
        with pytest.raises(TeamTalkConfigurationError, match="exactly one"):
            session.send_text("x", channel_id=1, user_id=2)

    def test_negative_user_id_rejected(self, session_pair):
        session, _client = session_pair()
        session.open()
        with pytest.raises(TeamTalkConfigurationError, match="cannot be negative"):
            session.send_private_message("x", -3)


class TestListing:
    def test_list_channels_sorted_and_shaped(self, session_pair):
        def configure(client):
            client.channels.update({5: "/Beta", 3: "/Alpha"})

        session, _client = session_pair(configure)
        session.open()
        channels = session.list_channels()
        paths = [c["path"] for c in channels]
        assert paths == ["/", "/Alpha", "/Beta"]
        assert channels[1]["id"] == 3
        assert channels[1]["password_required"] is False

    def test_list_channels_requires_login(self, session_pair):
        session, _client = session_pair()
        with pytest.raises(TeamTalkConfigurationError, match="log in"):
            session.list_channels()

    def test_list_users_excludes_self_by_default(self, session_pair):
        def configure(client):
            client.users = [
                {"id": 100, "nickname": "me"},  # the fake session's own id
                {"id": 7, "nickname": "zed", "channel_id": 1},
                {"id": 2, "username": "amy", "channel_id": 1},
            ]

        session, _client = session_pair(configure)
        session.open()
        users = session.list_users()
        assert [u["id"] for u in users] == [2, 7]  # sorted by display name
        assert users[0]["display_name"] == "amy"

    def test_list_users_can_include_self(self, session_pair):
        def configure(client):
            client.users = [{"id": 100, "nickname": "me"}]

        session, _client = session_pair(configure)
        session.open()
        users = session.list_users(include_self=True)
        assert [u["id"] for u in users] == [100]


class TestWaitForAndFailures:
    def test_wait_timeout_raises(self, session_pair):
        session, _client = session_pair(command_timeout=0.2)
        session.open()
        with pytest.raises(TeamTalkError, match="timed out waiting for logout"):
            # Ask for a logout confirmation that will never arrive.
            session._wait_for(
                {FakeClientEvent.CLIENTEVENT_CMD_MYSELF_LOGGEDOUT}, "logout", 999
            )

    def test_connection_failure_event_resets_flags(self, session_pair):
        session, client = session_pair()
        session.open()
        session.channel_id = 5
        message = FakeTTMessage(FakeClientEvent.CLIENTEVENT_CON_LOST)
        assert session.is_connection_failure(message) is True
        assert session.connected is False
        assert session.logged_in is False
        assert session.channel_id is None

    def test_non_failure_event_keeps_flags(self, session_pair):
        session, _client = session_pair()
        session.open()
        message = FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_SUCCESS)
        assert session.is_connection_failure(message) is False
        assert session.connected and session.logged_in

    def test_failure_event_during_wait_raises(self, session_pair):
        session, client = session_pair(command_timeout=1.0)
        session.open()
        client.inject_event(FakeTTMessage(FakeClientEvent.CLIENTEVENT_CON_FAILED))
        with pytest.raises(TeamTalkError, match="connection"):
            session._wait_for({77}, "mystery command", 1234)
class TestKickResistance:
    def test_check_reconnect_reports_false_when_disabled(self, session_pair):
        session, client = session_pair(kick_resistance=False)
        session.open()
        session.connected = False
        session.logged_in = False
        client.logged_in = False
        assert session.check_and_reconnect() is False

    def test_check_reconnect_rejoins_working_channel(self, session_pair, capsys):
        session, client = session_pair(channel_id=9)
        session.open()
        client.channel_id = 0  # kicked out of the channel, still online
        session.channel_id = None
        assert session.check_and_reconnect() is True
        assert client.channel_id == 9
        assert "rejoining" in capsys.readouterr().out

    def test_online_session_needs_no_action(self, session_pair):
        session, _client = session_pair()
        session.open()
        assert session.check_and_reconnect() is True


class TestCleanup:
    def test_close_releases_client(self, session_pair):
        session, client = session_pair()
        session.open()
        session.close()
        assert client.closed
        assert session.connected is False
        assert session.logged_in is False

    def test_close_is_idempotent(self, session_pair):
        session, _client = session_pair()
        session.open()
        session.close()
        session.close()  # must not raise

    def test_close_on_never_opened_session(self, session_pair):
        session, _client = session_pair()
        session.close()

    def test_close_survives_partial_init(self):
        # Regression: __del__ funnels here even when __init__ raised before
        # the bookkeeping attributes were assigned (e.g. license declined).
        incomplete = object.__new__(TeamTalkSession)
        incomplete.close()  # must not raise AttributeError

    def test_deleted_session_closes_cleanly(self, session_pair):
        session, client = session_pair()
        session.open()
        del session
        import gc

        gc.collect()  # __del__ must not explode during GC


class TestEventBuffer:
    def test_next_event_returns_placeholder_on_timeout(self, session_pair):
        session, _client = session_pair()
        session.open()
        message = session.poll(30)
        assert getattr(message, "nClientEvent", -1) == 0

    def test_buffered_event_delivered(self, session_pair):
        session, client = session_pair()
        session.open()
        client.inject_event(
            FakeTTMessage(
                FakeClientEvent.CLIENTEVENT_CMD_USER_TEXTMSG,
                textmessage=make_text_message("hello", msg_type=2),
            )
        )
        deadline = tt_teamtalk.time.monotonic() + 2.0
        seen = None
        while tt_teamtalk.time.monotonic() < deadline:
            message = session.poll(50)
            if getattr(message, "nClientEvent", 0):
                seen = message
                break
        assert seen is not None
        assert seen.textmessage.szMessage == "hello"

    def test_unsolicited_events_do_not_satisfy_waits(self, session_pair):
        session, client = session_pair(command_timeout=0.3)
        session.open()
        # An event for a *different* command id must not match our wait.
        client.inject_event(FakeTTMessage(FakeClientEvent.CLIENTEVENT_CMD_SUCCESS, nSource=1))
        with pytest.raises(TeamTalkError, match="timed out"):
            session._wait_for({FakeClientEvent.CLIENTEVENT_CMD_SUCCESS}, "other", 9999)
