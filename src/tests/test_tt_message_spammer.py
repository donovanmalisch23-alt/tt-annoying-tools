"""Tests for tt_message_spammer (channel/private sender)."""

from __future__ import annotations

import types

import pytest

import tt_message_spammer as mod
import tt_teamtalk
from tt_teamtalk import TeamTalkConfigurationError


def parse(argv):
    return mod.build_parser().parse_args(argv)


# --------------------------------------------------------------------------- #
# Message validation / reading
# --------------------------------------------------------------------------- #

class TestValidateMessage:
    def test_strips_crlf(self):
        assert mod.validate_message("hello\r\n") == "hello"

    def test_empty_rejected(self):
        for value in ("", "\n", "\r\n"):
            with pytest.raises(TeamTalkConfigurationError, match="cannot be empty"):
                mod.validate_message(value)

    def test_byte_limit(self):
        with pytest.raises(TeamTalkConfigurationError, match="4096"):
            mod.validate_message("x" * 4097)
        with pytest.raises(TeamTalkConfigurationError, match="4096"):
            mod.validate_message("é" * 2049)
        assert len(mod.validate_message("x" * 4096)) == 4096


class TestReadMessage:
    def test_from_flag(self):
        args = parse(["--host", "h", "--message", "hey"])
        assert mod.read_message(args) == "hey"

    def test_from_file(self, tmp_path):
        path = tmp_path / "msg.txt"
        path.write_text("from file\n", encoding="utf-8")
        args = parse(["--host", "h", "--message-file", str(path)])
        assert mod.read_message(args) == "from file"

    def test_missing_message(self):
        args = parse(["--host", "h"])
        with pytest.raises(TeamTalkConfigurationError, match="provide --message"):
            mod.read_message(args)

    def test_unreadable_file(self, tmp_path):
        args = parse(["--host", "h", "--message-file", str(tmp_path / "absent.txt")])
        with pytest.raises(TeamTalkConfigurationError, match="could not read"):
            mod.read_message(args)


# --------------------------------------------------------------------------- #
# ID / selection parsing
# --------------------------------------------------------------------------- #

class TestParseUserIds:
    def test_none_and_empty(self):
        assert mod.parse_user_ids(None) == []

    def test_combined_repeat_and_comma(self):
        assert mod.parse_user_ids(["1,2", "3"]) == [1, 2, 3]

    def test_duplicates_kept(self):
        # Unlike tt_suite, this parser keeps duplicates (intentional: the user
        # may want to send the same user the message twice per cycle? no — it
        # just doesn't dedupe; pins current behaviour as the contract).
        assert mod.parse_user_ids(["7", "7"]) == [7, 7]

    def test_negative_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="non-negative"):
            mod.parse_user_ids(["-2"])

    def test_nonnumeric_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="invalid TeamTalk user ID"):
            mod.parse_user_ids(["five"])

    def test_empty_segment_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="one or more"):
            mod.parse_user_ids(["1,,2"])


class TestParseUserNames:
    def test_none(self):
        assert mod.parse_user_names(None) == []

    def test_combined_repeat_and_comma(self):
        assert mod.parse_user_names(["amy,bob", "carol"]) == ["amy", "bob", "carol"]

    def test_strips_whitespace(self):
        assert mod.parse_user_names([" amy , bob "]) == ["amy", "bob"]

    def test_case_preserved(self):
        # The name is the identity; casefolding happens at comparison time.
        assert mod.parse_user_names(["Amy"]) == ["Amy"]

    def test_empty_segment_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="one or more usernames"):
            mod.parse_user_names(["amy,,bob"])

    def test_blank_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="one or more usernames"):
            mod.parse_user_names([" "])


class TestParseSelectionIndices:
    def test_valid(self):
        assert mod.parse_selection_indices("1,3,2", 5) == [1, 3, 2]

    def test_dedupes(self):
        assert mod.parse_selection_indices("2,2", 5) == [2]

    def test_range_enforced(self):
        with pytest.raises(TeamTalkConfigurationError, match="between 1 and 3"):
            mod.parse_selection_indices("4", 3)
        with pytest.raises(TeamTalkConfigurationError, match="between 1 and 3"):
            mod.parse_selection_indices("0", 3)

    def test_garbage(self):
        with pytest.raises(TeamTalkConfigurationError, match="invalid recipient"):
            mod.parse_selection_indices("x", 3)

    def test_empty(self):
        with pytest.raises(TeamTalkConfigurationError, match="list numbers"):
            mod.parse_selection_indices(" , ", 3)


class TestPromptNumberedIndex:
    def test_empty_options_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="no options"):
            mod.prompt_numbered_index("Pick", [])

    def test_lists_and_returns_zero_based(self, monkeypatch, capsys):
        monkeypatch.setattr("builtins.input", lambda prompt="": "2")
        assert mod.prompt_numbered_index("Pick", ["a", "b", "c"]) == 1
        out = capsys.readouterr().out
        assert "1. a" in out and "3. c" in out


class TestPromptPrivateRecipients:
    """The interactive picker selects users by NAME, never by their IDs."""

    def _session(self, users):
        return types.SimpleNamespace(list_users=lambda: list(users))

    def _script_input(self, monkeypatch, answers):
        answers = iter(answers)
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))

    def test_returns_names_not_ids(self, monkeypatch, capsys):
        users = [
            {"id": 5, "username": "amy", "nickname": "Amy",
             "display_name": "Amy", "channel_path": "/Lobby"},
            {"id": 6, "username": "", "nickname": "Bobby",
             "display_name": "Bobby", "channel_path": ""},
        ]
        self._script_input(monkeypatch, ["1", "y", "2", "n"])
        names, anonymous_ids = mod.prompt_private_recipients(self._session(users))
        assert names == ["amy", "Bobby"]
        assert anonymous_ids == []
        out = capsys.readouterr().out
        # listed by name — the IDs never appear
        assert "1. Amy — /Lobby" in out
        assert "2. Bobby" in out
        assert "user 5" not in out and "user 6" not in out

    def test_anonymous_user_falls_back_to_id(self, monkeypatch):
        users = [
            {"id": 9, "username": "", "nickname": "",
             "display_name": "", "channel_path": ""},
        ]
        self._script_input(monkeypatch, ["1", "n"])
        names, anonymous_ids = mod.prompt_private_recipients(self._session(users))
        # no name to track: the raw ID is the only identity available
        assert names == []
        assert anonymous_ids == [9]

    def test_no_users_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="no online"):
            mod.prompt_private_recipients(self._session([]))


# --------------------------------------------------------------------------- #
# Argument validation
# --------------------------------------------------------------------------- #

class TestValidateArgs:
    def test_channel_default_ok(self):
        assert mod.validate_args(parse(["--host", "h", "--message", "m"])) == []

    def test_private_requires_user_id(self):
        with pytest.raises(TeamTalkConfigurationError, match="at least one --user or --user-id"):
            mod.validate_args(
                parse(["--host", "h", "--target", "private", "--message", "m"])
            )

    def test_user_id_rejected_for_channel(self):
        with pytest.raises(TeamTalkConfigurationError, match="only valid with --target private"):
            mod.validate_args(parse(["--host", "h", "--user-id", "5"]))

    def test_user_names_rejected_for_channel(self):
        with pytest.raises(TeamTalkConfigurationError, match="only valid with --target private"):
            mod.validate_args(parse(["--host", "h", "--user", "amy"]))

    def test_user_names_satisfy_private(self):
        assert mod.validate_args(
            parse(["--host", "h", "--target", "private", "--message", "m", "--user", "amy"])
        ) == []

    def test_recipient_cap(self):
        ids = ["--user-id", ",".join(str(i) for i in range(21))]
        with pytest.raises(TeamTalkConfigurationError, match="at most 20"):
            mod.validate_args(
                parse(["--host", "h", "--target", "private"] + ["--user-id", ",".join(str(i) for i in range(21))])
            )

    def test_recipient_cap_counts_names_and_ids(self):
        argv = ["--host", "h", "--target", "private", "--user-id", "1,2"]
        argv += ["--user", ",".join(f"u{i}" for i in range(19))]
        with pytest.raises(TeamTalkConfigurationError, match="at most 20"):
            mod.validate_args(parse(argv))

    def test_recipient_cap_boundary(self):
        twenty = ",".join(str(i) for i in range(20))
        user_ids = mod.validate_args(
            parse(["--host", "h", "--target", "private", "--user-id", twenty])
        )
        assert len(user_ids) == 20

    def test_count_minimum(self):
        with pytest.raises(TeamTalkConfigurationError, match="--count"):
            mod.validate_args(parse(["--host", "h", "--count", "0"]))

    def test_negative_interval(self):
        with pytest.raises(TeamTalkConfigurationError, match="--interval"):
            mod.validate_args(parse(["--host", "h", "--interval", "-0.1"]))

    @pytest.mark.parametrize("wait", ["-0.1", "300.1"])
    def test_wait_bounds(self, wait):
        with pytest.raises(TeamTalkConfigurationError, match="--wait"):
            mod.validate_args(parse(["--host", "h", "--wait", wait]))


# --------------------------------------------------------------------------- #
# send_messages plumbing
# --------------------------------------------------------------------------- #

class TestSendMessages:
    def _config(self):
        return tt_teamtalk.ConnectionConfig(host="h", accept_sdk_license=True)

    def test_user_id_and_user_ids_conflict(self):
        with pytest.raises(TeamTalkConfigurationError, match="not both"):
            mod.send_messages(
                config=self._config(), message="m", count=1, interval=0, wait=0,
                target="private", user_id=1, user_ids=[2],
            )

    def test_private_needs_recipients(self):
        with pytest.raises(TeamTalkConfigurationError, match="at least one recipient"):
            mod.send_messages(
                config=self._config(), message="m", count=1, interval=0, wait=0,
                target="private",
            )

    def test_private_negative_recipient(self):
        with pytest.raises(TeamTalkConfigurationError, match="non-negative"):
            mod.send_messages(
                config=self._config(), message="m", count=1, interval=0, wait=0,
                target="private", user_ids=[-1],
            )

    def test_private_recipient_cap(self):
        with pytest.raises(TeamTalkConfigurationError, match="at most 20"):
            mod.send_messages(
                config=self._config(), message="m", count=1, interval=0, wait=0,
                target="private", user_ids=list(range(21)),
            )

    def test_channel_rejects_recipients(self):
        with pytest.raises(TeamTalkConfigurationError, match="only valid with --target private"):
            mod.send_messages(
                config=self._config(), message="m", count=1, interval=0, wait=0,
                target="channel", user_ids=[5],
            )


# --------------------------------------------------------------------------- #
# send_messages_on_session against a fake session
# --------------------------------------------------------------------------- #

class TestSendOnSession:
    def test_channel_sends_count_times(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.channel_id = 7
        rc = mod.send_messages_on_session(
            session=session, message="hi", count=3, interval=0, wait=0,
            target="channel", recipient_ids=(),
        )
        assert rc == 0
        assert session.channel_messages == [("hi", 7)] * 3
        assert session.rejoin_channel_id == 7

    def test_channel_without_channel_stops_after_bounded_retries(
        self, tool_session_factory, capsys
    ):
        # A client that never joined a channel cannot deliver a channel message;
        # the run retries a bounded number of times and stops instead of
        # hammering the server (the interrupted send is never counted).
        factory, _created = tool_session_factory
        session = factory()(None)

        def raise_no_channel():
            raise tt_teamtalk.TeamTalkConfigurationError("not in a channel")

        session.current_channel_id = raise_no_channel
        rc = mod.send_messages_on_session(
            session=session, message="hi", count=1, interval=0, wait=0,
            target="channel", recipient_ids=(),
        )
        assert rc == 1
        assert session.channel_messages == []
        # one reconnect per failed attempt, capped by MAX_CONSECUTIVE_FAILURES
        assert session.reconnect_calls == mod.MAX_CONSECUTIVE_FAILURES - 1
        assert "Too many failed sends" in capsys.readouterr().out

    def test_private_multi_recipient(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        rc = mod.send_messages_on_session(
            session=session, message="psst", count=2, interval=0, wait=0,
            target="private", recipient_ids=[4, 6],
        )
        assert rc == 0
        assert session.private_messages == [("psst", 4), ("psst", 6)] * 2

    def test_private_by_username_needs_no_roster(self, tool_session_factory):
        # --user recipients are keyed by name from the start: no ID is ever
        # stored, so no startup roster lookup is needed to send.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [{"id": 9, "username": "amy", "nickname": "Amy"}]
        rc = mod.send_messages_on_session(
            session=session, message="psst", count=1, interval=0, wait=0,
            target="private", recipient_names=["amy"],
        )
        assert rc == 0
        assert session.private_messages == [("psst", 9)]

    def test_private_kick_reresolves_fresh_id_and_keeps_exact_count(
        self, tool_session_factory, capsys
    ):
        # The user's request, end to end: protection (a kick) fires mid-send.
        # The interrupted message is retried against the recipient's fresh
        # server ID (looked up again by username), and the delivered total
        # stays exactly ``count`` — the numbering never restarts and no extra
        # message is sent.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [{"id": 4, "username": "amy", "nickname": "Amy"}]

        real_reconnect = session.check_and_reconnect

        def kicked_then_relogged():
            ok = real_reconnect()
            # The reconnect relogged everyone: amy now has a brand-new ID.
            session.users = [{"id": 44, "username": "amy", "nickname": "Amy"}]
            session.fail_private = False
            return ok

        session.check_and_reconnect = kicked_then_relogged
        session.fail_private = True  # the first send attempt is kicked

        rc = mod.send_messages_on_session(
            session=session, message="psst", count=2, interval=0, wait=0,
            target="private", recipient_names=["amy"],
        )
        assert rc == 0
        # exactly two deliveries, the second against the fresh ID 44 — the
        # kicked attempt delivered nothing and was not counted or repeated
        assert session.private_messages == [("psst", 44), ("psst", 44)]
        assert session.reconnect_calls == 1

    def test_private_relog_mid_run_retargets_fresh_id(
        self, tool_session_factory
    ):
        # Continuous re-scan with no kick involved: amy relogs on her own
        # between sends and the server hands her a brand-new ID.  The roster
        # is re-read by name before every send, so her new ID becomes the
        # target — the count neither restarts nor overruns.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [{"id": 4, "username": "amy", "nickname": "Amy"}]

        real_send = session.send_private_message

        def send_then_relog(message, user_id):
            result = real_send(message, user_id)
            if user_id == 4:  # her first login session just ended
                session.users = [{"id": 44, "username": "amy", "nickname": "Amy"}]
            return result

        session.send_private_message = send_then_relog

        rc = mod.send_messages_on_session(
            session=session, message="psst", count=2, interval=0, wait=0,
            target="private", recipient_names=["amy"],
        )
        assert rc == 0
        assert session.private_messages == [("psst", 4), ("psst", 44)]

    def test_private_relog_matched_by_nickname(self, tool_session_factory):
        # Same relog retargeting for a user with no username: the nickname is
        # the identity, so the same nickname with a fresh ID is the target.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [{"id": 4, "username": "", "nickname": "Amy"}]

        real_send = session.send_private_message

        def send_then_relog(message, user_id):
            result = real_send(message, user_id)
            if user_id == 4:
                session.users = [{"id": 77, "username": "", "nickname": "Amy"}]
            return result

        session.send_private_message = send_then_relog

        rc = mod.send_messages_on_session(
            session=session, message="psst", count=2, interval=0, wait=0,
            target="private", recipient_names=["Amy"],
        )
        assert rc == 0
        assert session.private_messages == [("psst", 4), ("psst", 77)]

    def test_name_display_enriched_from_roster(self, tool_session_factory, capsys):
        # A name-keyed recipient prints with its roster label, not the raw
        # typed text.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [
            {"id": 9, "username": "amy", "nickname": "Amy Sunshine",
             "display_name": "Amy Sunshine"}
        ]
        rc = mod.send_messages_on_session(
            session=session, message="psst", count=1, interval=0, wait=0,
            target="private", recipient_names=["amy"],
        )
        assert rc == 0
        assert "Amy Sunshine (@amy)" in capsys.readouterr().out

    def test_private_kick_gives_up_after_consecutive_failures(
        self, tool_session_factory, capsys
    ):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = [{"id": 4, "username": "amy", "nickname": "Amy"}]
        session.fail_private = True  # every attempt is kicked
        rc = mod.send_messages_on_session(
            session=session, message="psst", count=5, interval=0, wait=0,
            target="private", recipient_names=["amy"],
        )
        assert rc == 1
        assert session.private_messages == []
        assert session.reconnect_calls == mod.MAX_CONSECUTIVE_FAILURES - 1
        assert "Too many failed sends" in capsys.readouterr().out

    def test_wait_message(self, tool_session_factory, monkeypatch, capsys):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.channel_id = 1
        monkeypatch.setattr(mod.time, "sleep", lambda s: None)
        mod.send_messages_on_session(
            session=session, message="m", count=1, interval=0, wait=5,
            target="channel", recipient_ids=(),
        )
        assert "5 seconds" in capsys.readouterr().out
# --------------------------------------------------------------------------- #
# main()
# --------------------------------------------------------------------------- #

class TestMain:
    def test_missing_host(self, capsys):
        assert mod.main(["--message", "x"]) == 2
        assert "--host is required" in capsys.readouterr().err

    def test_missing_message(self, capsys):
        assert mod.main(["--host", "h"]) == 2
        assert "provide --message" in capsys.readouterr().err

    def test_validation_error(self, capsys):
        assert mod.main(["--host", "h", "--target", "private", "--message", "m"]) == 2
        assert "at least one --user or --user-id" in capsys.readouterr().err
