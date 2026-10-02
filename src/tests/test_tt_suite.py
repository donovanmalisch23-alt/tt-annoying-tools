"""Tests for tt_suite: whitelist, target parsing, argument validation,
channel resolution, roster merging, bot plumbing, and the execute()/offline
regression where post-cycle sessions were wrongly reported offline.
"""

from __future__ import annotations

import argparse
import threading
import types

import pytest

import tt_suite
import tt_teamtalk
from tt_teamtalk import TeamTalkConfigurationError, TeamTalkError


# --------------------------------------------------------------------------- #
# Host/whitelist helpers
# --------------------------------------------------------------------------- #

class TestNormalizeHost:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Example.COM", "example.com"),
            ("  spaced.test  ", "spaced.test"),
            ("trailing.example.", "trailing.example"),
            ("[::1]", "::1"),
            ("[2001:DB8::1]", "2001:db8::1"),
            ("10.0.0.5", "10.0.0.5"),
        ],
    )
    def test_forms(self, raw, expected):
        assert tt_suite.normalize_host(raw) == expected


class TestWhitelist:
    def test_load_entries(self, tmp_path):
        path = tmp_path / "wl.txt"
        path.write_text("# comment\n\nExample.test  # inline\n10.0.0.1\n", encoding="utf-8")
        assert tt_suite.load_whitelist(path) == {"example.test", "10.0.0.1"}

    def test_empty_file_rejected(self, tmp_path):
        path = tmp_path / "wl.txt"
        path.write_text("# only comments\n", encoding="utf-8")
        with pytest.raises(TeamTalkConfigurationError, match="whitelist .* is empty"):
            tt_suite.load_whitelist(path)

    def test_missing_file(self, tmp_path):
        with pytest.raises(TeamTalkConfigurationError, match="could not read"):
            tt_suite.load_whitelist(tmp_path / "absent.txt")

    def test_ensure_allowed(self, tmp_path):
        path = tmp_path / "wl.txt"
        path.write_text("my.server\n", encoding="utf-8")
        tt_suite.ensure_server_allowed("MY.server.", path)  # must not raise

    def test_ensure_rejected(self, tmp_path):
        path = tmp_path / "wl.txt"
        path.write_text("my.server\n", encoding="utf-8")
        with pytest.raises(TeamTalkConfigurationError, match="not in the whitelist"):
            tt_suite.ensure_server_allowed("evil.server", path)


# --------------------------------------------------------------------------- #
# Message + user-id parsing
# --------------------------------------------------------------------------- #

class TestValidateTestMessage:
    def test_strips_line_endings(self):
        assert tt_suite.validate_test_message("hi\r\n", "label") == "hi"

    def test_empty_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="cannot be empty"):
            tt_suite.validate_test_message("\r\n", "label")

    def test_too_many_bytes_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="4096"):
            tt_suite.validate_test_message("x" * 4097, "label")

    def test_multibyte_counts_bytes(self):
        with pytest.raises(TeamTalkConfigurationError, match="4096"):
            tt_suite.validate_test_message("é" * 2049, "label")  # 4098 bytes

    def test_exactly_4096_ok(self):
        assert len(tt_suite.validate_test_message("x" * 4096, "label")) == 4096


class TestParseUserIds:
    def test_none(self):
        assert tt_suite.parse_user_ids(None) == ([], False)

    def test_single_string(self):
        assert tt_suite.parse_user_ids("5") == ([5], False)

    def test_comma_separated(self):
        assert tt_suite.parse_user_ids(["1,2", "3"]) == ([1, 2, 3], False)

    def test_dedupes(self):
        assert tt_suite.parse_user_ids("5,5,5") == ([5], False)

    def test_all_selector(self):
        assert tt_suite.parse_user_ids(["all"]) == ([], True)

    def test_all_case_insensitive(self):
        assert tt_suite.parse_user_ids("ALL") == ([], True)

    def test_mixed_ids_and_all(self):
        assert tt_suite.parse_user_ids(["7", "all"]) == ([7], True)

    def test_int_input(self):
        assert tt_suite.parse_user_ids(9) == ([9], False)

    def test_empty_segment_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="numeric IDs"):
            tt_suite.parse_user_ids("1,,2")

    def test_negative_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="non-negative"):
            tt_suite.parse_user_ids("-4")

    def test_garbage_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="invalid TeamTalk user ID"):
            tt_suite.parse_user_ids("abc")


class TestParseUserNames:
    def test_none(self):
        assert tt_suite.parse_user_names(None) == []

    def test_single_string(self):
        assert tt_suite.parse_user_names("amy") == ["amy"]

    def test_repeat_and_comma(self):
        assert tt_suite.parse_user_names(["amy,bob", "carol"]) == ["amy", "bob", "carol"]

    def test_strips_whitespace(self):
        assert tt_suite.parse_user_names([" amy , bob "]) == ["amy", "bob"]

    def test_empty_segment_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="one or more usernames"):
            tt_suite.parse_user_names("amy,,bob")


# --------------------------------------------------------------------------- #
# Argument validation matrix
# --------------------------------------------------------------------------- #

def parse(argv):
    return tt_suite.build_parser().parse_args(argv)


class TestValidateArgs:
    def test_defaults_pass(self):
        args = parse(["--host", "h"])
        assert tt_suite.validate_args(args) == ([], [], False, False)

    def test_message_count_zero(self):
        with pytest.raises(TeamTalkConfigurationError, match="--message-count"):
            tt_suite.validate_args(parse(["--host", "h", "--message-count", "0"]))

    def test_negative_cycles(self):
        for flag in ("--login-cycles", "--join-leave-cycles"):
            with pytest.raises(TeamTalkConfigurationError, match="cannot be negative"):
                tt_suite.validate_args(parse(["--host", "h", flag, "-1"]))

    def test_negative_interval(self):
        with pytest.raises(TeamTalkConfigurationError, match="--interval"):
            tt_suite.validate_args(parse(["--host", "h", "--interval", "-0.5"]))

    def test_negative_sweep_interval(self):
        # Regression: sweep interval was not validated at all.
        with pytest.raises(TeamTalkConfigurationError, match="--sweep-interval"):
            tt_suite.validate_args(parse(["--host", "h", "--sweep-interval", "-1"]))

    def test_private_message_needs_recipients(self):
        with pytest.raises(TeamTalkConfigurationError, match="--user, --user-id, or --all-users"):
            tt_suite.validate_args(parse(["--host", "h", "--private-message", "hi"]))

    def test_private_message_accepts_user_names(self):
        args = parse(
            ["--host", "h", "--private-message", "hi", "--user", "amy,bob", "--confirm"]
        )
        assert tt_suite.validate_args(args) == ([], ["amy", "bob"], False, False)

    def test_channel_message_needs_channel(self):
        with pytest.raises(TeamTalkConfigurationError, match="--channel-id"):
            tt_suite.validate_args(
                parse(["--host", "h", "--channel-message", "hi", "--confirm"])
            )

    def test_action_needs_confirm(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--channel-message", "hi", "--channel-id", "1"]
                )
            )

    def test_dry_run_skips_confirm(self):
        args = parse(
            ["--host", "h", "--channel-message", "hi", "--channel-id", "1", "--dry-run"]
        )
        tt_suite.validate_args(args)  # must not raise

    def test_channel_path_all_shortcut(self):
        args = parse(["--host", "h", "--channel-path", "all", "--channel-message", "x", "--confirm"])
        _ids, _names, _all_users, all_channels = tt_suite.validate_args(args)
        assert args.channel_path is None
        assert all_channels is True

    def test_churn_requires_concurrent(self):
        with pytest.raises(TeamTalkConfigurationError, match="requires --concurrent"):
            tt_suite.validate_args(
                parse(["--host", "h", "--churn-bots", "2", "--churn-cycles", "1"])
            )

    @pytest.mark.parametrize("flag", ["--bot-per-channel", "--bot-per-user"])
    def test_bot_per_requires_concurrent(self, flag):
        argv = ["--host", "h", flag, "--churn-bots", "1"]
        with pytest.raises(TeamTalkConfigurationError, match="requires --concurrent"):
            tt_suite.validate_args(parse(argv))

    def test_concurrent_requires_action(self):
        with pytest.raises(TeamTalkConfigurationError, match="--concurrent requires"):
            tt_suite.validate_args(parse(["--host", "h", "--concurrent"]))

    def test_concurrent_requires_confirm(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            tt_suite.validate_args(
                parse(["--host", "h", "--concurrent", "--churn-bots", "1"])
            )

    def test_concurrent_dry_run_skips_confirm(self):
        args = parse(["--host", "h", "--concurrent", "--churn-bots", "1", "--dry-run"])
        tt_suite.validate_args(args)

    def test_bot_per_channel_needs_channel_action(self):
        with pytest.raises(TeamTalkConfigurationError, match="channel action"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--concurrent", "--bot-per-channel",
                     "--private-message", "m", "--all-users", "--confirm"]
                )
            )

    def test_bot_per_user_needs_private_message(self):
        with pytest.raises(TeamTalkConfigurationError, match="--bot-per-user requires"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--concurrent", "--bot-per-user",
                     "--channel-message", "m", "--channel-id", "1", "--confirm"]
                )
            )

    def test_continuous_scan_needs_concurrent(self):
        with pytest.raises(TeamTalkConfigurationError, match="--continuous-scan requires --concurrent"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--continuous-scan", "--bot-per-channel", "--confirm"]
                )
            )

    def test_continuous_scan_needs_a_bot_kind(self):
        with pytest.raises(TeamTalkConfigurationError, match="--bot-per-channel and/or --bot-per-user"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--concurrent", "--continuous-scan",
                     "--private-message", "m", "--all-users", "--confirm"]
                )
            )

    def test_continuous_scan_channel_bots_may_idle(self):
        # No --channel-message and no --join-leave-cycles: with
        # --continuous-scan the channel bots join and idle instead.
        args = parse(
            ["--host", "h", "--concurrent", "--continuous-scan",
             "--bot-per-channel", "--confirm"]
        )
        ids, names, all_users, all_channels = tt_suite.validate_args(args)
        assert all_users is True and all_channels is True
        assert args.continuous_scan is True

    def test_continuous_scan_defaults_to_two_seconds(self):
        args = parse(["--host", "h", "--concurrent", "--continuous-scan",
                      "--bot-per-channel", "--confirm"])
        assert args.scan_interval == 2.0

    def test_scan_interval_negative_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="--scan-interval"):
            tt_suite.validate_args(
                parse(
                    ["--host", "h", "--concurrent", "--continuous-scan",
                     "--bot-per-channel", "--scan-interval", "-1", "--confirm"]
                )
            )

    def test_comma_counts(self):
        args = parse(["--host", "h", "--message-count", "1,000"])
        assert args.message_count == 1000

    def test_user_id_all_propagates(self):
        args = parse(
            ["--host", "h", "--private-message", "m", "--user-id", "all", "--confirm"]
        )
        ids, names, all_users, _ = tt_suite.validate_args(args)
        assert ids == [] and names == [] and all_users is True


# --------------------------------------------------------------------------- #
# Channel path normalization / resolution
# --------------------------------------------------------------------------- #

class TestNormalizeChannelPath:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", "/"),
            ("/", "/"),
            ("Lobby", "/Lobby"),
            ("/Lobby/", "/Lobby"),
            (" /A/B/ ", "/A/B"),
        ],
    )
    def test_forms(self, raw, expected):
        assert tt_suite.normalize_channel_path(raw) == expected


class TestResolveChannels:
    CHANNELS = [
        {"id": 1, "name": "root", "path": "/"},
        {"id": 2, "name": "Lobby", "path": "/Lobby"},
    ]

    def test_all_channels(self):
        config = types.SimpleNamespace(channel_id=None, channel_path=None)
        assert tt_suite.resolve_channels(self.CHANNELS, config, True) == self.CHANNELS

    def test_by_id(self):
        config = types.SimpleNamespace(channel_id=2, channel_path=None)
        assert tt_suite.resolve_channels(self.CHANNELS, config, False)[0]["id"] == 2

    def test_unknown_id_gets_synthetic_entry(self):
        config = types.SimpleNamespace(channel_id=99, channel_path=None)
        resolved = tt_suite.resolve_channels(self.CHANNELS, config, False)
        assert resolved == [{"id": 99, "name": "99", "path": ""}]

    def test_by_path(self):
        config = types.SimpleNamespace(channel_id=None, channel_path="Lobby/")
        resolved = tt_suite.resolve_channels(self.CHANNELS, config, False)
        assert resolved[0]["id"] == 2

    def test_unknown_path_rejected(self):
        config = types.SimpleNamespace(channel_id=None, channel_path="/Nope")
        with pytest.raises(TeamTalkConfigurationError, match="was not found"):
            tt_suite.resolve_channels(self.CHANNELS, config, False)


class TestSelectTargets:
    ROSTER = [
        {"id": 5, "username": "amy", "nickname": "Amy", "display_name": "Amy"},
        {"id": 6, "username": "bob", "nickname": "Bobby", "display_name": "Bobby"},
    ]

    def test_all_users_targets_everyone_by_name(self):
        targets = tt_suite._select_targets(self.ROSTER, True, [], [])
        assert [key for key, _display in targets] == ["amy", "bob"]

    def test_selection_by_id_yields_username_keys(self):
        # An ID selects the user, but what the run keeps is the username.
        targets = tt_suite._select_targets(self.ROSTER, False, [6], [])
        assert targets == [("bob", tt_suite._user_display(self.ROSTER[1]))]

    def test_selection_by_name(self):
        targets = tt_suite._select_targets(self.ROSTER, False, [], ["amy"])
        assert [key for key, _display in targets] == ["amy"]

    def test_selection_by_name_matches_nickname(self):
        targets = tt_suite._select_targets(self.ROSTER, False, [], ["Bobby"])
        assert [key for key, _display in targets] == ["bob"]

    def test_selection_by_name_is_case_insensitive(self):
        targets = tt_suite._select_targets(self.ROSTER, False, [], ["AMY"])
        assert [key for key, _display in targets] == ["amy"]

    def test_unmatched_id_and_name_reported(self, capsys):
        targets = tt_suite._select_targets(self.ROSTER, False, [99], ["carol"])
        assert targets == []
        out = capsys.readouterr().out
        assert "User ID(s) [99] are not online" in out
        assert "'carol' are not online" in out

    def test_no_missing_reports_in_all_users_mode(self, capsys):
        tt_suite._select_targets(self.ROSTER, True, [99], ["carol"])
        assert "not online" not in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #

class TestSmallHelpers:
    def test_pause_zero_is_noop(self):
        tt_suite.pause(0)  # must return immediately
        tt_suite.pause(0.0)

    def test_bot_pause_zero_reflects_stop(self):
        assert tt_suite._bot_pause(0, threading.Event()) is False
        stopped = threading.Event()
        stopped.set()
        assert tt_suite._bot_pause(5.0, stopped) is True

    def test_bot_config_strips_channel(self):
        base = tt_teamtalk.ConnectionConfig(
            host="h", nickname="base", channel_id=3, channel_path="/x",
            channel_password="pw",
        )
        bot = tt_suite._bot_config(base, "base-users")
        assert bot.nickname == "base-users"
        assert bot.channel_id is None and bot.channel_path is None
        assert bot.channel_password == ""
        assert base.nickname == "base"  # original untouched

    def test_merge_channel_roster_dedupes(self):
        users = [{"id": 5, "display_name": "zed"}]
        roster = {
            1: [
                {"id": 2, "display_name": "amy"},
                {"id": 5, "display_name": "zed-duplicate"},
            ]
        }
        merged = tt_suite._merge_channel_roster(users, roster)
        assert [u["id"] for u in merged] == [2, 5]
        assert merged[1]["display_name"] == "zed"  # server roster wins

    def test_spawn_bot_thread_kinds(self):
        config = tt_teamtalk.ConnectionConfig(host="h", nickname="n")
        stop = threading.Event()
        user_job = ("user", [("amy", "Amy")], False, "m", 1, 0.0, 0.5, "Amy")
        channel_job = ("channel", [{"id": 1}], "", "m", 1, 1, 0.0, "lobby", False)
        churn_job = ("churn", 1, 1, 2, 0.0)
        for job in (user_job, channel_job, churn_job):
            thread = tt_suite._spawn_bot_thread(config, job, stop)
            assert isinstance(thread, threading.Thread)
            assert thread.daemon is True

    def test_spawn_bot_thread_unknown_kind(self):
        config = tt_teamtalk.ConnectionConfig(host="h")
        with pytest.raises(TeamTalkConfigurationError, match="unknown bot job kind"):
            tt_suite._spawn_bot_thread(config, ("mystery",), threading.Event())


class TestMaxConcurrentBots:
    def test_windows_style_fallback(self, monkeypatch):
        monkeypatch.setattr(tt_suite, "resource", None)
        assert tt_suite._max_concurrent_bots() == (1024 - 16) // 4

    def test_low_rlimit_shrinks_ceiling(self, monkeypatch):
        fake = types.SimpleNamespace(
            RLIMIT_NOFILE=7,
            getrlimit=lambda what: (128, 128),
            setrlimit=lambda what, limits: None,
        )
        monkeypatch.setattr(tt_suite, "resource", fake)
        assert tt_suite._max_concurrent_bots() == (128 - 16) // 4

    def test_broken_rlimit_falls_back(self, monkeypatch):
        def explode(what):
            raise OSError("nope")

        fake = types.SimpleNamespace(
            RLIMIT_NOFILE=7, getrlimit=explode, setrlimit=lambda *a: None
        )
        monkeypatch.setattr(tt_suite, "resource", fake)
        assert tt_suite._max_concurrent_bots() == (1024 - 16) // 4

    def test_never_below_one(self, monkeypatch):
        fake = types.SimpleNamespace(
            RLIMIT_NOFILE=7,
            getrlimit=lambda what: (16, 16),
            setrlimit=lambda what, limits: None,
        )
        monkeypatch.setattr(tt_suite, "resource", fake)
        assert tt_suite._max_concurrent_bots() == 1


# --------------------------------------------------------------------------- #
# Print discovery (pure output)
# --------------------------------------------------------------------------- #

class TestPrintDiscovery:
    def test_lists_both(self, capsys):
        # Users are listed by name (nickname, plus @username when the nickname
        # differs), never by their server-assigned ID.
        channels = [{"id": 1, "path": "/Lobby"}]
        users = [
            {
                "id": 3, "username": "amy", "nickname": "Amy",
                "display_name": "Amy", "channel_path": "/Lobby",
            },
            {
                "id": 4, "username": "bobby", "nickname": "Bobby Tables",
                "display_name": "Bobby Tables", "channel_path": "/Lobby",
            },
            {
                "id": 5, "username": "zed", "nickname": "",
                "display_name": "zed", "channel_path": "",
            },
        ]
        tt_suite.print_discovery(channels, users)
        out = capsys.readouterr().out
        assert "Discovered 1 channel(s)" in out
        assert "channel 1: /Lobby" in out
        # nickname matching the username: no redundant @suffix
        assert "Amy — /Lobby" in out
        # nickname differing from the username: both are shown
        assert "Bobby Tables (@bobby) — /Lobby" in out
        assert "zed" in out
        assert "user 3" not in out and "user 4" not in out and "user 5" not in out


# --------------------------------------------------------------------------- #
# Sequential operation loops (fake sessions)
# --------------------------------------------------------------------------- #

class TestRunLoginLogoutCycles:
    def test_completes_all_cycles(self, tool_session_factory):
        factory, created = tool_session_factory
        session = factory()(None)
        assert tt_suite.run_login_logout_cycles(session, 3, 0) is True
        assert session.logout_calls == 3
        # direct sessions start logged out, so each cycle re-logs in
        assert session.login_calls == 3
class TestRunChannelOperations:
    CHANNELS = [
        {"id": 1, "name": "A", "path": "/A", "password_required": False},
        {"id": 2, "name": "B", "path": "/B", "password_required": True},
    ]

    def test_join_message_leave_sequence(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        tt_suite.run_channel_operations(
            session,
            self.CHANNELS,
            channel_password="pw",
            join_leave_cycles=2,
            channel_message="hello",
            message_count=2,
            interval=0,
        )
        # 2 channels x 2 cycles
        assert session.joins == [(1, "pw"), (1, "pw"), (2, "pw"), (2, "pw")]
        assert session.leaves == 4
        # 2 channels x 2 cycles x 2 messages
        assert len(session.channel_messages) == 8
        assert all(msg == "hello" for msg, _cid in session.channel_messages)

    def test_no_cycles_no_channel_message_is_noop(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        tt_suite.run_channel_operations(
            session,
            self.CHANNELS,
            channel_password="",
            join_leave_cycles=0,
            channel_message=None,
            message_count=1,
            interval=0,
        )
        assert session.joins == []

    def test_message_alone_runs_single_cycle(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        tt_suite.run_channel_operations(
            session,
            [self.CHANNELS[0]],
            channel_password="",
            join_leave_cycles=0,
            channel_message="hi",
            message_count=1,
            interval=0,
        )
        assert session.joins == [(1, "")]
        assert session.leaves == 1

    def test_password_required_notice(self, tool_session_factory, capsys):
        factory, _created = tool_session_factory
        session = factory()(None)
        tt_suite.run_channel_operations(
            session,
            [self.CHANNELS[1]],
            channel_password="",
            join_leave_cycles=1,
            channel_message=None,
            message_count=1,
            interval=0,
        )
        assert "requires a password" in capsys.readouterr().out

    def test_failure_with_reconnect_continues(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory(fail_join_ids={1})(None)
        tt_suite.run_channel_operations(
            session,
            [self.CHANNELS[0]],
            channel_password="",
            join_leave_cycles=2,
            channel_message=None,
            message_count=1,
            interval=0,
        )
        # every join fails, every failure reconnects, next cycle retries
        assert session.reconnect_calls == 2  # once per cycle

    def test_failed_reconnect_aborts(self, tool_session_factory, capsys):
        factory, _created = tool_session_factory
        session = factory(fail_join_ids={1}, reconnect_result=False)(None)
        tt_suite.run_channel_operations(
            session,
            self.CHANNELS,
            channel_password="",
            join_leave_cycles=3,
            channel_message=None,
            message_count=1,
            interval=0,
        )
        assert "Could not reconnect" in capsys.readouterr().out
        assert all(cid != 2 for cid, _ in session.joins)  # B never attempted


class TestRunPrivateOperations:
    def _roster(self, *entries):
        return [
            {"id": user_id, "username": name, "nickname": name.title(),
             "display_name": name.title(), "channel_path": "/"}
            for user_id, name in entries
        ]

    def test_sends_to_all_recipients(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"), (7, "bob"))
        assert tt_suite.run_private_operations(
            session, [("amy", "Amy"), ("bob", "Bob")],
            message="ping", message_count=2, interval=0,
        ) is True
        assert session.private_messages == [
            ("ping", 5), ("ping", 7), ("ping", 5), ("ping", 7)
        ]

    def test_kick_reresolves_fresh_id_and_keeps_exact_count(
        self, tool_session_factory, capsys
    ):
        # The user's request, end to end: protection (a kick) fires mid-send.
        # The interrupted message is retried against the recipient's fresh
        # server ID (looked up again by username), and the delivered total
        # stays exactly ``message_count`` — no restart, no extra messages.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"))

        real_reconnect = session.check_and_reconnect

        def kicked_then_relogged():
            ok = real_reconnect()
            # everyone relogged: amy now has a brand-new server ID
            session.users = self._roster((55, "amy"))
            session.fail_private = False
            return ok

        session.check_and_reconnect = kicked_then_relogged
        session.fail_private = True  # the first send attempt is kicked

        assert tt_suite.run_private_operations(
            session, [("amy", "Amy")],
            message="ping", message_count=2, interval=0,
        ) is True
        # exactly two deliveries, both against the fresh ID — the kicked
        # attempt delivered nothing and was neither counted nor repeated
        assert session.private_messages == [("ping", 55), ("ping", 55)]
        assert session.reconnect_calls == 1

    def test_relog_mid_run_retargets_fresh_id(self, tool_session_factory):
        # Continuous re-scan with no kick involved: amy relogs on her own
        # between sends and the server hands her a brand-new ID.  The roster
        # is re-read by name before every send, so the new ID becomes the
        # target and the delivered total stays exactly message_count.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"))

        real_send = session.send_private_message

        def send_then_relog(message, user_id):
            result = real_send(message, user_id)
            if user_id == 5:  # her first login session just ended
                session.users = self._roster((55, "amy"))
            return result

        session.send_private_message = send_then_relog

        assert tt_suite.run_private_operations(
            session, [("amy", "Amy")],
            message="ping", message_count=2, interval=0,
        ) is True
        assert session.private_messages == [("ping", 5), ("ping", 55)]

    def test_offline_recipient_is_skipped_not_counted(self, tool_session_factory, capsys):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((7, "bob"))  # amy never online
        assert tt_suite.run_private_operations(
            session, [("amy", "Amy"), ("bob", "Bob")],
            message="ping", message_count=1, interval=0,
        ) is True
        assert session.private_messages == [("ping", 7)]
        assert "not online; skipping" in capsys.readouterr().out

    def test_logout_mid_run_waits_for_return(
        self, tool_session_factory, monkeypatch, capsys
    ):
        # amy is online, then logs out after her first message and comes back
        # with a brand-new ID: the run parks on the server for the username
        # instead of skipping her, then delivers the rest against the fresh ID.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"))
        monkeypatch.setattr(tt_suite, "USER_RETURN_POLL_SECONDS", 0.0)

        real_send = session.send_private_message
        real_users = session.list_users
        misses = {"n": 0}

        def send_then_logout(message, user_id):
            result = real_send(message, user_id)
            session.users = []  # she logged out right after this message
            return result

        def users_until_return(include_self=False):
            if session.users:
                return real_users(include_self)
            misses["n"] += 1
            if misses["n"] >= 2:  # she logs back in while we wait
                session.users = self._roster((55, "amy"))
            return real_users(include_self)

        session.send_private_message = send_then_logout
        session.list_users = users_until_return

        assert tt_suite.run_private_operations(
            session, [("amy", "Amy")],
            message="ping", message_count=2, interval=0,
        ) is True
        assert session.private_messages == [("ping", 5), ("ping", 55)]
        out = capsys.readouterr().out
        assert "logged out" in out and "is back as user ID 55" in out
        assert "skipping" not in out


# --------------------------------------------------------------------------- #
# Bots
# --------------------------------------------------------------------------- #

class TestUserBot:
    def _base_config(self):
        return tt_teamtalk.ConnectionConfig(
            host="h", nickname="suite", accept_sdk_license=True
        )

    def _roster(self, *entries):
        return [
            {"id": user_id, "username": name, "nickname": name.title(),
             "display_name": name.title(), "channel_id": 1, "channel_path": "/a"}
            for user_id, name in entries
        ]

    def test_finite_mode_messages_each_user(
        self, tool_session_factory, monkeypatch
    ):
        factory, created = tool_session_factory
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(users=self._roster((5, "amy"), (6, "bob")))
        )
        tt_suite._user_bot(
            self._base_config(), [("amy", "Amy"), ("bob", "Bob")],
            False, "hey", 2, 0, 0.5, threading.Event(),
        )
        assert created[0].private_messages == [("hey", 5), ("hey", 5), ("hey", 6), ("hey", 6)]

    def test_finite_mode_respects_stop(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=self._roster((5, "amy"))))
        stop = threading.Event()
        stop.set()
        tt_suite._user_bot(
            self._base_config(), [("amy", "Amy")], False, "hey", 1, 0, 0.5, stop
        )  # returns fast; assertion is "no hang"
        assert created[0].private_messages == []

    def test_continuous_mode_messages_existing_then_idles(
        self, tool_session_factory, monkeypatch
    ):
        users = self._roster((5, "amy"))
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=users))
        stop = threading.Event()

        real_send = tt_suite._send_to_user

        # Stop the bot after the first sweep completes its sends.
        def patched_send(session, key, display, message, count, interval, stop_event):
            result = real_send(
                session, key, display, message, count, interval, stop_event
            )
            stop_event.set()
            return result

        monkeypatch.setattr(tt_suite, "_send_to_user", patched_send)
        tt_suite._user_bot(
            self._base_config(), [], True, "hey", 1, 0, 0.05, stop
        )
        # The message went to amy's current ID, resolved from the roster by
        # username at send time.
        assert created[0].private_messages == [("hey", 5)]

    def test_send_to_user_counts_and_stop(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"))
        stop = threading.Event()
        assert tt_suite._send_to_user(session, "amy", "Amy", "m", 3, 0, stop) is True
        assert session.private_messages == [("m", 5)] * 3
        stop.set()
        assert tt_suite._send_to_user(session, "amy", "Amy", "m", 1, 0, stop) is False

    def test_send_to_user_failure_reconnect_then_give_up(self, tool_session_factory):
        factory, _created = tool_session_factory
        session = factory(fail_private=True)(None)
        session.users = self._roster((5, "amy"))
        stop = threading.Event()
        with pytest.raises(tt_suite._BotStop):
            session.reconnect_result = False
            tt_suite._send_to_user(session, "amy", "Amy", "m", 1, 0, stop)

    def test_send_to_user_recovers_via_fresh_id(
        self, tool_session_factory, capsys
    ):
        # A kick interrupts the send; after the reconnect the user relogged
        # with a brand-new ID.  The interrupted message is re-sent against the
        # fresh ID (looked up again by username) and the delivered total stays
        # exactly ``count`` — never restarted, never exceeded.
        factory, _created = tool_session_factory
        session = factory(fail_private=True)(None)
        session.users = self._roster((5, "amy"))

        real_reconnect = session.check_and_reconnect

        def kicked_then_relogged():
            ok = real_reconnect()
            session.users = self._roster((55, "amy"))
            session.fail_private = False
            return ok

        session.check_and_reconnect = kicked_then_relogged

        assert tt_suite._send_to_user(
            session, "amy", "Amy", "m", 2, 0, threading.Event()
        ) is True
        assert session.private_messages == [("m", 55), ("m", 55)]
        assert session.reconnect_calls == 1

    def test_send_to_user_waits_out_a_logout(
        self, tool_session_factory, monkeypatch, capsys
    ):
        # A user who was online and then logs out does not cost the bot its
        # run: sending pauses on the server until the username comes back,
        # then the remaining messages go to the fresh ID.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((5, "amy"))
        monkeypatch.setattr(tt_suite, "USER_RETURN_POLL_SECONDS", 0.0)

        real_send = session.send_private_message
        real_users = session.list_users
        misses = {"n": 0}

        def send_then_logout(message, user_id):
            result = real_send(message, user_id)
            session.users = []
            return result

        def users_until_return(include_self=False):
            if session.users:
                return real_users(include_self)
            misses["n"] += 1
            if misses["n"] >= 2:
                session.users = self._roster((55, "amy"))
            return real_users(include_self)

        session.send_private_message = send_then_logout
        session.list_users = users_until_return

        assert tt_suite._send_to_user(
            session, "amy", "Amy", "m", 2, 0, threading.Event()
        ) is True
        assert session.private_messages == [("m", 5), ("m", 55)]
        assert "is back as user ID 55" in capsys.readouterr().out

    def test_send_to_user_never_online_is_dropped(
        self, tool_session_factory, capsys
    ):
        # A target that was never online this run (a mistyped name, say) has
        # no logout to wait out: the bot drops it instead of parking forever.
        factory, _created = tool_session_factory
        session = factory()(None)
        session.users = self._roster((7, "bob"))  # amy never online
        assert tt_suite._send_to_user(
            session, "amy", "Amy", "m", 2, 0, threading.Event()
        ) is False
        assert session.private_messages == []
        assert "dropping the 2 message(s)" in capsys.readouterr().out

    def test_send_to_user_gives_up_after_consecutive_failures(
        self, tool_session_factory, capsys
    ):
        # The target is permanently unreachable: retries stay bounded (no
        # reconnect/retry storm against the server) and the bot gives up
        # cleanly without delivering anything.
        factory, _created = tool_session_factory
        session = factory(fail_private=True, reconnect_result=True)(None)
        session.users = self._roster((5, "amy"))
        assert tt_suite._send_to_user(
            session, "amy", "Amy", "m", 5, 0, threading.Event()
        ) is False
        assert session.private_messages == []
        assert session.reconnect_calls == tt_suite.MAX_CONSECUTIVE_FAILURES - 1
        assert "giving up on Amy" in capsys.readouterr().out


class TestChannelBot:
    def test_cycles_and_messages(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory())
        channels = [{"id": 4, "name": "A", "path": "/A"}]
        tt_suite._channel_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            channels, "", "msg", 2, 2, 0, threading.Event(),
        )
        session = created[0]
        assert session.joins == [(4, ""), (4, "")]
        assert session.leaves == 2
        assert len(session.channel_messages) == 4

    def test_join_failure_skips_channel(self, tool_session_factory, monkeypatch, capsys):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(fail_join_ids={4}))
        channels = [{"id": 4, "name": "A", "path": "/A"}]
        tt_suite._channel_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            channels, "", None, 1, 3, 0, threading.Event(),
        )
        assert created[0].joins == []
        out = capsys.readouterr().out
        assert "could not join" in out

    def test_nick_suffix_names_the_bot(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory())
        tt_suite._channel_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            [{"id": 4, "name": "lobby", "path": "/lobby"}],
            "", "msg", 1, 1, 0, threading.Event(), nick_suffix="lobby",
        )
        assert created[0].config.nickname == "n-lobby"

    def test_idle_mode_joins_and_parks(self, tool_session_factory, monkeypatch, capsys):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory())
        stop = threading.Event()
        threading.Timer(0.05, stop.set).start()
        tt_suite._channel_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            [{"id": 4, "name": "lobby", "path": "/lobby"}],
            "", None, 1, 0, 0, stop, nick_suffix="lobby", idle_when_done=True,
        )
        session = created[0]
        assert session.joins == [(4, "")]
        assert session.leaves == 0  # it parks; it does not cycle out
        assert session.config.nickname == "n-lobby"
        assert "idling as n-lobby" in capsys.readouterr().out

    def test_idle_mode_join_failure_moves_on(self, tool_session_factory, monkeypatch, capsys):
        factory, _created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(fail_join_ids={4}))
        tt_suite._channel_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            [{"id": 4, "name": "A", "path": "/A"}],
            "", None, 1, 0, 0, threading.Event(), nick_suffix="A", idle_when_done=True,
        )
        out = capsys.readouterr().out
        assert "could not join" in out


class TestBotNicknames:
    def test_channel_nick_suffix_prefers_name(self):
        assert (
            tt_suite._channel_nick_suffix({"id": 4, "name": "lobby", "path": "/lobby"})
            == "lobby"
        )

    def test_channel_nick_suffix_falls_back_to_path_then_id(self):
        assert tt_suite._channel_nick_suffix({"id": 4, "name": "", "path": "/deep/room"}) == "deep/room"
        assert tt_suite._channel_nick_suffix({"id": 7, "name": "", "path": ""}) == "7"

    def test_user_nick_suffix_strips_display_decoration(self):
        assert tt_suite._user_nick_suffix("Amy (@amy)") == "Amy"
        assert tt_suite._user_nick_suffix("Amy") == "Amy"
        assert tt_suite._user_nick_suffix("@amy") == "amy"
        assert tt_suite._user_nick_suffix("  ") == "user"

    def test_is_own_bot_matches_username_and_nick_prefix(self):
        config = tt_teamtalk.ConnectionConfig(
            host="h", nickname="suite", username="op"
        )
        by_username = {"username": "op", "nickname": "whatever"}
        by_prefix = {"username": "", "nickname": "Suite-scanner"}
        stranger = {"username": "amy", "nickname": "Amy"}
        assert tt_suite._is_own_bot(by_username, config) is True
        assert tt_suite._is_own_bot(by_prefix, config) is True
        assert tt_suite._is_own_bot(stranger, config) is False


class TestContinuousScan:
    def _config(self):
        return tt_teamtalk.ConnectionConfig(
            host="h", nickname="suite", username="op", accept_sdk_license=True
        )

    def _args(self, **overrides):
        options = dict(
            bot_per_channel=True,
            bot_per_user=True,
            private_message="hi",
            channel_message=None,
            join_leave_cycles=0,
            message_count=1,
            interval=0.0,
            sweep_interval=0.05,
            scan_interval=0.05,
            dry_run=False,
            confirm=True,
        )
        options.update(overrides)
        return argparse.Namespace(**options)

    def _channel(self, cid, name):
        return {
            "id": cid, "name": name, "path": f"/{name}",
            "password_required": False, "parent_id": 1, "hidden": False,
        }

    def _user(self, uid, username, nickname):
        return {
            "id": uid, "username": username, "nickname": nickname,
            "display_name": nickname or username, "channel_id": 1,
            "channel_path": "/",
        }

    def _stop_after_passes(self, monkeypatch, passes):
        """Patch _drain_events so the scanner stops after N passes."""
        real_drain = tt_suite._drain_events
        calls = {"n": 0}

        def drain(session, stop_event, window_s):
            result = real_drain(session, stop_event, window_s)
            calls["n"] += 1
            if calls["n"] >= passes:
                stop_event.set()
            return result

        monkeypatch.setattr(tt_suite, "_drain_events", drain)

    def test_spawns_named_bots_for_each_target_once(
        self, tool_session_factory, monkeypatch, capsys
    ):
        factory, created = tool_session_factory
        channels = [self._channel(4, "lobby")]
        users = [
            self._user(5, "amy", "Amy"),
            # The scanner's own bots: it must never spawn message bots for them.
            self._user(8, "op", "someone"),
            self._user(9, "", "suite-scanner"),
        ]
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(channels=channels, users=users)
        )
        self._stop_after_passes(monkeypatch, passes=2)

        rc = tt_suite._run_continuous_scan(self._config(), self._args())
        assert rc == 0
        out = capsys.readouterr().out
        assert "spawned channel-bot suite-lobby" in out
        assert "spawned message-bot suite-Amy" in out
        # Two passes over the same inventory: still exactly one of each bot.
        assert out.count("spawned channel-bot") == 1
        assert out.count("spawned message-bot") == 1
        nicknames = sorted(session.config.nickname for session in created)
        assert nicknames == ["suite-Amy", "suite-lobby", "suite-scanner"]
        # The message bot delivered to amy's ID; the channel bot joined lobby.
        message_bot = next(s for s in created if s.config.nickname == "suite-Amy")
        channel_bot = next(s for s in created if s.config.nickname == "suite-lobby")
        assert message_bot.private_messages == [("hi", 5)]
        assert channel_bot.joins == [(4, "")]

    def test_bot_ceiling_caps_spawning(
        self, tool_session_factory, monkeypatch, capsys
    ):
        factory, _created = tool_session_factory
        channels = [self._channel(4, "lobby"), self._channel(5, "other")]
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(channels=channels, users=[])
        )
        monkeypatch.setattr(tt_suite, "_max_concurrent_bots", lambda: 2)
        self._stop_after_passes(monkeypatch, passes=2)

        rc = tt_suite._run_continuous_scan(
            self._config(), self._args(bot_per_user=False, private_message=None)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "spawned channel-bot suite-lobby" in out
        assert "suite-other" not in out
        assert "bot ceiling" in out

    def test_dry_run_spawns_nothing(
        self, tool_session_factory, monkeypatch, capsys
    ):
        factory, created = tool_session_factory
        channels = [self._channel(4, "lobby")]
        users = [self._user(5, "amy", "Amy")]
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(channels=channels, users=users)
        )

        rc = tt_suite._run_continuous_scan(self._config(), self._args(dry_run=True))
        assert rc == 0
        assert len(created) == 1  # the scan session only; no bots
        out = capsys.readouterr().out
        assert "Dry run complete" in out
        assert "spawned channel-bot" not in out
        assert "spawned message-bot" not in out


class TestMidRunDiscovery:
    """Users who log in while a concurrent run is already active must be found.

    Both continuous discovery paths re-read the roster on every pass: the
    shared --all-users user-bot sweeps every --sweep-interval, and the
    --continuous-scan scanner re-lists every --scan-interval.  A joiner that
    appears between two passes is therefore picked up by the next one — a
    path that only snapshots the roster at startup (or never starts the bot
    at all) leaves the joiner unhandled for the whole run.  These tests
    inject a login between passes and require the joiner to be acted on
    before the run ends.
    """

    def _config(self):
        return tt_teamtalk.ConnectionConfig(
            host="h", nickname="suite", username="op", accept_sdk_license=True
        )

    def _user(self, uid, username, nickname):
        return {
            "id": uid, "username": username, "nickname": nickname,
            "display_name": nickname or username, "channel_id": 1,
            "channel_path": "/",
        }

    def _joiners_then_stop(self, monkeypatch, join_on_pass, joiners, stop_after):
        """Patch ``_drain_events`` so ``joiners`` appear mid-run and the bot
        stops later: joiners are added to the roster during pass
        ``join_on_pass`` (after the previous pass's roster snapshot), and the
        shared stop event fires after pass ``stop_after``."""
        real_drain = tt_suite._drain_events
        calls = {"n": 0}

        def drain(session, stop_event, window_s):
            result = real_drain(session, stop_event, window_s)
            calls["n"] += 1
            if calls["n"] == join_on_pass:
                session.users.extend(joiners)
            if calls["n"] >= stop_after:
                stop_event.set()
            return result

        monkeypatch.setattr(tt_suite, "_drain_events", drain)

    def test_all_users_user_bot_messages_mid_run_joiner(
        self, tool_session_factory, monkeypatch, capsys
    ):
        # --concurrent --all-users: the shared user-bot runs continuously.
        # amy is online when the run starts; bob logs in after the first
        # sweep's roster snapshot.  Both must be messaged exactly once each
        # (no repeats) before the run is interrupted.
        factory, created = tool_session_factory
        users = [self._user(5, "amy", "Amy")]
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=users))
        self._joiners_then_stop(
            monkeypatch, join_on_pass=2,
            joiners=[self._user(7, "bob", "Bob")], stop_after=3,
        )
        args = argparse.Namespace(
            private_message="hi", message_count=1, interval=0.0,
            sweep_interval=0.05, bot_per_user=False, bot_per_channel=False,
            churn_bots=0, churn_cycles=1, channel_message=None,
            join_leave_cycles=0, dry_run=False, continuous_scan=False,
            confirm=True,
        )

        assert tt_suite._run_concurrent(
            self._config(), args, [], [], True, False
        ) == 0
        # created[0] is the startup discovery session; the user-bot is the
        # only bot this plan spawns, and it must have opened its own session.
        assert len(created) == 2
        bot = created[1]
        assert bot.config.nickname == "suite-users"
        # amy from the startup roster, then the mid-run joiner: one message
        # each, delivered against the ID current at send time, never repeated.
        assert bot.private_messages == [("hi", 5), ("hi", 7)]
        out = capsys.readouterr().out
        assert "watching for users" in out

    def test_continuous_scan_spawns_bot_for_mid_run_joiner(
        self, tool_session_factory, monkeypatch, capsys
    ):
        # --continuous-scan: the scanner sees amy on its first pass; bob logs
        # in between passes.  A message bot must be spawned for each new
        # user, exactly once.
        factory, created = tool_session_factory
        channels = [
            {"id": 4, "name": "lobby", "path": "/lobby", "password_required": False}
        ]
        users = [self._user(5, "amy", "Amy")]
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(channels=channels, users=users)
        )
        self._joiners_then_stop(
            monkeypatch, join_on_pass=2,
            joiners=[self._user(7, "bob", "Bob")], stop_after=3,
        )
        args = argparse.Namespace(
            bot_per_channel=False, bot_per_user=True, private_message="hi",
            channel_message=None, join_leave_cycles=0, message_count=1,
            interval=0.0, sweep_interval=0.05, scan_interval=0.05,
            dry_run=False, confirm=True,
        )

        assert tt_suite._run_continuous_scan(self._config(), args) == 0
        out = capsys.readouterr().out
        assert out.count("spawned message-bot suite-Amy") == 1
        assert out.count("spawned message-bot suite-Bob") == 1
        # Both spawned bots delivered their one message to their own user.
        amy_bot = next(s for s in created if s.config.nickname == "suite-Amy")
        bob_bot = next(s for s in created if s.config.nickname == "suite-Bob")
        assert amy_bot.private_messages == [("hi", 5)]
        assert bob_bot.private_messages == [("hi", 7)]


class TestChurnBot:
    def test_cycle_count(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory())
        tt_suite._churn_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            1, 1, 3, 0, threading.Event(),
        )
        assert created[0].logout_calls == 3
        assert created[0].login_calls == 2  # first login comes from open()

    def test_failure_without_reconnect_stops(self, tool_session_factory, monkeypatch, capsys):
        factory, _created = tool_session_factory
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession",
            factory(fail_login=True, logged_in=False, reconnect_result=False),
        )
        tt_suite._churn_bot(
            tt_teamtalk.ConnectionConfig(host="h", nickname="n", accept_sdk_license=True),
            1, 2, 5, 0, threading.Event(),
        )
        assert "could not reconnect" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# execute() — including the post-login-cycle offline regression
# --------------------------------------------------------------------------- #

def _execute_args(tmp_path, extra, host="whitelisted.local", wl_host=None):
    whitelist = tmp_path / "whitelist.txt"
    whitelist.write_text((wl_host or host) + "\n", encoding="utf-8")
    argv = ["--host", host, "--whitelist", str(whitelist)] + extra
    return tt_suite.build_parser().parse_args(argv)


class TestExecute:
    def _roster(self):
        return [
            {"id": 5, "username": "amy", "nickname": "Amy", "display_name": "Amy",
             "channel_path": "/Lobby", "channel_id": 1},
        ]

    def test_private_messages_with_explicit_users(
        self, tool_session_factory, tmp_path, monkeypatch
    ):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=self._roster()))
        args = _execute_args(
            tmp_path,
            ["--private-message", "ping", "--user-id", "5", "--confirm", "--interval", "0"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 0
        assert created[0].private_messages == [("ping", 5)]

    def test_private_messages_with_user_names(
        self, tool_session_factory, tmp_path, monkeypatch
    ):
        # --user amy: the target is selected (and later resolved) by username,
        # so it survives amy relogging with a different server-assigned ID.
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=self._roster()))
        args = _execute_args(
            tmp_path,
            ["--private-message", "ping", "--user", "amy", "--confirm", "--interval", "0"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 0
        assert created[0].private_messages == [("ping", 5)]

    def test_dry_run_sends_nothing(self, tool_session_factory, tmp_path, monkeypatch, capsys):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=self._roster()))
        args = _execute_args(
            tmp_path,
            ["--private-message", "ping", "--user", "amy", "--dry-run"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 0
        # The target was found and named, but a dry run sends nothing.
        assert created[0].private_messages == []
        out = capsys.readouterr().out
        assert "Dry run complete" in out
        assert "Amy" in out

    def test_login_cycles_then_private_messages(
        self, tool_session_factory, tmp_path, monkeypatch
    ):
        """Regression: after login cycles the session is logged OUT but still
        connected; execute() must log back in instead of aborting as offline."""
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(users=self._roster()))
        args = _execute_args(
            tmp_path,
            ["--login-cycles", "2", "--private-message", "ping",
             "--user", "amy", "--confirm", "--interval", "0"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 0
        session = created[0]
        assert session.private_messages == [("ping", 5)]
        assert session.login_calls == 2  # one mid-ramp re-login + one post-cycle
        assert session.logout_calls == 2

    def test_truly_offline_session_aborts(
        self, tool_session_factory, tmp_path, monkeypatch, capsys
    ):
        factory, created = tool_session_factory
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession", factory(auto_connect=False)
        )
        args = _execute_args(
            tmp_path,
            ["--private-message", "ping", "--user-id", "5", "--confirm"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 1
        assert "Session is offline" in capsys.readouterr().out
        assert created[0].private_messages == []

    def test_channel_message_flow(self, tool_session_factory, tmp_path, monkeypatch):
        channels = [{"id": 2, "name": "Lobby", "path": "/Lobby", "password_required": False}]
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(channels=channels))
        args = _execute_args(
            tmp_path,
            ["--channel-message", "announce", "--channel-path", "/Lobby",
             "--confirm", "--interval", "0"],
        )
        config = tt_teamtalk.config_from_args(args)
        assert tt_suite.execute(config, args) == 0
        session = created[0]
        assert (2, "") in session.joins
        assert ("announce", 2) in session.channel_messages

    def test_unknown_channel_path_errors(
        self, tool_session_factory, tmp_path, monkeypatch
    ):
        factory, _created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory(channels=[]))
        args = _execute_args(
            tmp_path,
            ["--channel-message", "x", "--channel-path", "/Missing", "--confirm"],
        )
        config = tt_teamtalk.config_from_args(args)
        with pytest.raises(TeamTalkConfigurationError, match="was not found"):
            tt_suite.execute(config, args)

    def test_host_not_whitelisted(self, tool_session_factory, tmp_path, monkeypatch):
        factory, _created = tool_session_factory
        monkeypatch.setattr(tt_suite, "TeamTalkSession", factory())
        args = _execute_args(
            tmp_path,
            ["--private-message", "ping", "--user-id", "5", "--confirm"],
            host="whitelisted.local",
            wl_host="allowed.local",
        )
        config = tt_teamtalk.config_from_args(args)  # host = whitelisted.local
        with pytest.raises(TeamTalkConfigurationError, match="not in the whitelist"):
            tt_suite.execute(config, args)


# --------------------------------------------------------------------------- #
# main() wiring
# --------------------------------------------------------------------------- #

class TestMain:
    def test_config_error_returns_2(self, capsys):
        assert tt_suite.main(["--private-message", "x"]) == 2
        assert "Error:" in capsys.readouterr().err

    def test_missing_host_returns_2(self, capsys):
        assert tt_suite.main(["--login-cycles", "1"]) == 2
        assert "--host is required" in capsys.readouterr().err

    def test_run_concurrent_dry_run(
        self, tool_session_factory, tmp_path, monkeypatch, capsys
    ):
        factory, created = tool_session_factory
        monkeypatch.setattr(
            tt_suite, "TeamTalkSession",
            factory(users=[{"id": 3, "display_name": "u3"}, {"id": 4, "display_name": "u4"}]),
        )
        args = _execute_args(
            tmp_path,
            ["--concurrent", "--private-message", "hi", "--user-id", "3,4",
             "--dry-run"],
        )
        assert tt_suite.main(_argv_of(tmp_path)) == 0
        out = capsys.readouterr().out
        assert "Concurrent bot plan" in out


def _argv_of(tmp_path):
    # main() needs argv, not a parsed namespace; rebuild a minimal one.
    return [
        "--host", "whitelisted.local",
        "--whitelist", str(tmp_path / "whitelist.txt"),
        "--concurrent", "--private-message", "hi",
        "--user-id", "3,4", "--dry-run",
    ]
