"""Tests for ttbot_the_offender (trigger-based response bot).

Focus: user tracking moved from server-assigned IDs to usernames.  The
allowlist matches by name, and the per-user cooldown keeps applying across a
user's reconnect (which hands them a brand-new ID).
"""

from __future__ import annotations

import argparse
import types

import pytest

import ttbot_the_offender as bot
import tt_teamtalk
from conftest import FakeClientEvent, make_text_message
from tt_teamtalk import TeamTalkConfigurationError


def namespace(**overrides):
    """An args namespace matching the bot's parser defaults."""

    args = dict(
        trigger="!hello",
        response="Hi {username}, thanks for your message!",
        allow_user=[],
        allow_user_id=[],
        allow_all=False,
        cooldown=30.0,
        max_responses=100,
        confirm=False,
        channel_id=1,
        channel_path=None,
    )
    args.update(overrides)
    return argparse.Namespace(**args)


# --------------------------------------------------------------------------- #
# Name parsing / sender identity
# --------------------------------------------------------------------------- #

class TestParseAllowUsers:
    def test_none(self):
        assert bot.parse_allow_users(None) == []

    def test_repeat_and_comma(self):
        assert bot.parse_allow_users(["amy,bob", "carol"]) == ["amy", "bob", "carol"]

    def test_strips(self):
        assert bot.parse_allow_users([" amy "]) == ["amy"]

    def test_empty_segment_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="one or more usernames"):
            bot.parse_allow_users(["amy,,bob"])


class TestSenderKey:
    def test_username_casefolded(self):
        incoming = {"from_username": "Amy", "from_user_id": 5}
        assert bot._sender_key(incoming) == "amy"

    def test_same_username_same_key_across_ids(self):
        # The whole point: a relog changes the ID but not the identity.
        assert bot._sender_key({"from_username": "amy", "from_user_id": 5}) == \
            bot._sender_key({"from_username": "amy", "from_user_id": 77})

    def test_anonymous_falls_back_to_id(self):
        assert bot._sender_key({"from_username": "", "from_user_id": 9}) == "user-9"


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #

class TestValidateArgs:
    def test_allow_user_accepted(self):
        bot.validate_args(namespace(allow_user=["amy"]))  # must not raise

    def test_allow_user_id_accepted(self):
        bot.validate_args(namespace(allow_user_id=[5]))  # must not raise

    def test_neither_names_nor_ids_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="--allow-user"):
            bot.validate_args(namespace())

    def test_malformed_name_rejected_before_connecting(self):
        # A trailing comma is a configuration error, so it must surface from
        # validate_args() — which runs before the SDK connection is opened —
        # instead of only after the bot has already tried to connect.
        with pytest.raises(TeamTalkConfigurationError, match="one or more usernames"):
            bot.validate_args(namespace(allow_user=["amy,"]))

    def test_negative_id_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="cannot be negative"):
            bot.validate_args(namespace(allow_user_id=[-1]))

    def test_empty_trigger_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="trigger"):
            bot.validate_args(namespace(allow_all=True, trigger=""))

    def test_channel_required(self):
        with pytest.raises(TeamTalkConfigurationError, match="channel"):
            bot.validate_args(namespace(allow_all=True, channel_id=None))

    def test_unlimited_needs_confirm(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            bot.validate_args(namespace(allow_all=True, max_responses=0))


# --------------------------------------------------------------------------- #
# Allowlist resolution
# --------------------------------------------------------------------------- #

class TestResolveAllowedUsers:
    def _session(self, users):
        return types.SimpleNamespace(list_users=lambda include_self=False: list(users))

    def test_online_id_resolves_to_username(self, capsys):
        session = self._session(
            [{"id": 5, "username": "amy", "nickname": "Amy"}]
        )
        names, unresolved = bot._resolve_allowed_users(session, [5])
        assert names == {"amy"}
        assert unresolved == set()
        assert "resolves to 'amy'" in capsys.readouterr().out

    def test_offline_id_stays_literal(self, capsys):
        session = self._session([])
        names, unresolved = bot._resolve_allowed_users(session, [5])
        assert names == set()
        assert unresolved == {5}

    def test_nickname_used_when_username_blank(self):
        session = self._session([{"id": 5, "username": "", "nickname": "Amy"}])
        names, unresolved = bot._resolve_allowed_users(session, [5])
        assert names == {"amy"}
        assert unresolved == set()

    def test_no_ids_is_empty(self):
        names, unresolved = bot._resolve_allowed_users(self._session([]), [])
        assert names == set() and unresolved == set()


# --------------------------------------------------------------------------- #
# run(): cooldown and allowlist keyed by username
# --------------------------------------------------------------------------- #

def _text_event(from_user_id, from_username, text="!hello", channel_id=1):
    return types.SimpleNamespace(
        nClientEvent=FakeClientEvent.CLIENTEVENT_CMD_USER_TEXTMSG,
        textmessage=make_text_message(
            text, from_user_id=from_user_id, from_username=from_username,
            channel_id=channel_id,
        ),
    )


class TestRun:
    def _config(self):
        return tt_teamtalk.ConnectionConfig(
            host="h", accept_sdk_license=True, channel_id=1,
            reconnect_delay=10_000.0,  # keep the watchdog out of the way
        )

    def _run_with_clock(self, monkeypatch, tool_session_factory, events, times, args):
        """Run the bot against scripted events arriving at scripted times.

        ``times[i]`` is the monotonic clock value while ``events[i]`` is
        processed, so cooldown behaviour is deterministic.
        """

        clock = {"now": 0.0}
        monkeypatch.setattr(bot.time, "monotonic", lambda: clock["now"])

        def scheduled_poll(wait_ms=1000):
            index = scheduled_poll.index
            if index >= len(events):
                return types.SimpleNamespace(nClientEvent=0)
            message = events[index]
            clock["now"] = times[index]
            scheduled_poll.index += 1
            return message

        scheduled_poll.index = 0

        factory, created = tool_session_factory
        monkeypatch.setattr(
            bot, "TeamTalkSession", factory(channel_id=1, poll=scheduled_poll)
        )
        rc = bot.run(args, config=self._config())
        return rc, created[0]

    def test_cooldown_survives_user_id_change(self, monkeypatch, tool_session_factory):
        # amy triggers a reply, reconnects (new ID 77), and triggers again
        # inside the cooldown window: the cooldown must still apply because it
        # is keyed on her username, not the server's fresh ID.
        events = [
            _text_event(5, "amy"),
            _text_event(77, "amy"),   # relogged: fresh ID, same person
            _text_event(77, "amy"),   # past the cooldown now
        ]
        times = [1000.0, 1001.0, 1100.0]
        args = namespace(allow_all=True, cooldown=30.0, max_responses=2)
        rc, session = self._run_with_clock(
            monkeypatch, tool_session_factory, events, times, args
        )
        assert rc == 0
        # exactly two replies: the mid-cooldown message was suppressed even
        # though it arrived under a brand-new user ID
        assert len(session.channel_messages) == 2

    def test_allow_user_matches_by_name_across_ids(self, monkeypatch, tool_session_factory):
        # --allow-user amy: amy gets replies under any ID; bob never does.
        events = [
            _text_event(5, "amy"),
            _text_event(6, "bob"),    # not allowlisted
            _text_event(77, "amy"),   # amy again, fresh ID, past cooldown
        ]
        times = [1000.0, 1050.0, 1100.0]
        args = namespace(allow_user=["amy"], cooldown=30.0, max_responses=2)
        rc, session = self._run_with_clock(
            monkeypatch, tool_session_factory, events, times, args
        )
        assert rc == 0
        assert len(session.channel_messages) == 2
        replies = [text for text, _cid in session.channel_messages]
        assert all("bob" not in reply for reply in replies)
        assert any("amy" in reply.casefold() for reply in replies)

    def test_trigger_prefix_required(self, monkeypatch, tool_session_factory):
        events = [
            _text_event(5, "amy", text="!hello"),
            _text_event(5, "amy", text="plain chatter"),  # no trigger: ignored
            _text_event(5, "amy", text="!hello"),
        ]
        times = [1000.0, 1050.0, 1100.0]
        args = namespace(allow_all=True, cooldown=30.0, max_responses=2)
        rc, session = self._run_with_clock(
            monkeypatch, tool_session_factory, events, times, args
        )
        assert rc == 0
        assert len(session.channel_messages) == 2