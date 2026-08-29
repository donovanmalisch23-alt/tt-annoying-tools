"""Tests for tt_spammer (login/logout cycler)."""

from __future__ import annotations

import pytest

import tt_spammer
import tt_teamtalk
from tt_teamtalk import TeamTalkConfigurationError


def parse(argv):
    return tt_spammer.build_parser().parse_args(argv)


class TestValidateArgs:
    def test_defaults_pass(self):
        tt_spammer.validate_args(parse(["--host", "h"]))

    def test_cycles_minimum(self):
        with pytest.raises(TeamTalkConfigurationError, match="--cycles"):
            tt_spammer.validate_args(parse(["--host", "h", "--cycles", "0"]))

    def test_negative_interval(self):
        with pytest.raises(TeamTalkConfigurationError, match="--interval"):
            tt_spammer.validate_args(parse(["--host", "h", "--interval", "-1"]))

    @pytest.mark.parametrize("wait", ["-1", "301", "3600"])
    def test_wait_bounds(self, wait):
        with pytest.raises(TeamTalkConfigurationError, match="--wait"):
            tt_spammer.validate_args(parse(["--host", "h", "--wait", wait]))

    def test_wait_max_ok(self):
        tt_spammer.validate_args(parse(["--host", "h", "--wait", "300"]))

    def test_comma_cycles(self):
        args = parse(["--host", "h", "--cycles", "1,000"])
        assert args.cycles == 1000
        tt_spammer.validate_args(args)


class TestLoginOnlyConfig:
    def test_channel_settings_stripped(self):
        args = parse(
            ["--host", "h", "--channel-id", "5", "--channel-password", "pw"]
        )
        config = tt_spammer.login_only_config_from_args(args)
        assert config.channel_id is None
        assert config.channel_path is None
        assert config.channel_password == ""
        assert config.host == "h"

    def test_channel_path_stripped(self):
        args = parse(["--host", "h", "--channel-path", "/Lobby"])
        config = tt_spammer.login_only_config_from_args(args)
        assert config.channel_path is None


class TestRunCycles:
    def test_run_cycles_happy_path(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        monkeypatch.setattr(tt_spammer, "TeamTalkSession", factory())
        config = tt_teamtalk.ConnectionConfig(host="h", accept_sdk_license=True)
        assert tt_spammer.run_cycles(config=config, cycles=2, interval=0, wait=0) == 0
        session = created[0]
        assert session.logout_calls == 2
        assert session.login_calls == 1  # cycle 0 reuses open()'s login

    def test_wait_prints_countdown(self, tool_session_factory, monkeypatch, capsys):
        factory, _created = tool_session_factory
        monkeypatch.setattr(tt_spammer, "TeamTalkSession", factory())
        monkeypatch.setattr(tt_spammer.time, "sleep", lambda s: None)
        config = tt_teamtalk.ConnectionConfig(host="h", accept_sdk_license=True)
        assert tt_spammer.run_cycles(config=config, cycles=1, interval=0, wait=9) == 0
        assert "9 seconds" in capsys.readouterr().out
class TestMain:
    def test_config_error_returns_2(self, capsys):
        assert tt_spammer.main(["--cycles", "5"]) == 2
        assert "--host is required" in capsys.readouterr().err

    def test_validation_error_returns_2(self, capsys):
        assert tt_spammer.main(["--host", "h", "--cycles", "0"]) == 2
        assert "--cycles" in capsys.readouterr().err
