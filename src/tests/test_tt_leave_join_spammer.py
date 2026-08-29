"""Tests for tt_leave_join_spammer (channel leave/join cycler)."""

from __future__ import annotations

import pytest

import tt_leave_join_spammer as mod
import tt_teamtalk
from tt_teamtalk import TeamTalkConfigurationError


def parse(argv):
    return mod.build_parser().parse_args(argv)


class TestValidateArgs:
    def test_defaults_need_channel(self):
        with pytest.raises(TeamTalkConfigurationError, match="--channel-id or --channel-path"):
            mod.validate_args(parse(["--host", "h"]))

    def test_channel_id_ok(self):
        mod.validate_args(parse(["--host", "h", "--channel-id", "3"]))

    def test_channel_path_ok(self):
        mod.validate_args(parse(["--host", "h", "--channel-path", "/Lobby"]))

    def test_cycles_minimum(self):
        with pytest.raises(TeamTalkConfigurationError, match="--cycles"):
            mod.validate_args(
                parse(["--host", "h", "--channel-id", "1", "--cycles", "0"])
            )

    def test_negative_interval(self):
        with pytest.raises(TeamTalkConfigurationError, match="--interval"):
            mod.validate_args(
                parse(["--host", "h", "--channel-id", "1", "--interval", "-0.5"])
            )

    @pytest.mark.parametrize("wait", ["-0.1", "300.1"])
    def test_wait_bounds(self, wait):
        with pytest.raises(TeamTalkConfigurationError, match="--wait"):
            mod.validate_args(
                parse(["--host", "h", "--channel-id", "1", "--wait", wait])
            )

    def test_wait_boundary_ok(self):
        mod.validate_args(
            parse(["--host", "h", "--channel-id", "1", "--wait", "300"])
        )


class TestRunCycles:
    def _config(self, **overrides):
        options = {
            "host": "h",
            "accept_sdk_license": True,
            "channel_id": 9,
            "channel_password": "pw",
        }
        options.update(overrides)
        return tt_teamtalk.ConnectionConfig(**options)

    def test_leave_join_sequence(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        session = factory(channel_id=9)
        monkeypatch.setattr(mod, "TeamTalkSession", session)
        config = self._config()
        rc = mod.run_cycles(config=config, cycles=2, interval=0, wait=0)
        assert rc == 0
        bot = created[0]
        assert bot.leaves == 2
        # join by ID each cycle, password forwarded
        assert bot.joins == [(9, "pw")] * 2
        assert bot.rejoin_channel_id == 9
        assert bot.rejoin_channel_password == "pw"

    def test_path_channel_joins_by_path(self, tool_session_factory, monkeypatch):
        factory, created = tool_session_factory
        factory_override = factory(
            channel_id=4, channels=[{"id": 4, "path": "/Lobby"}]
        )
        monkeypatch.setattr(mod, "TeamTalkSession", factory_override)
        config = self._config(channel_id=None, channel_path="/Lobby", channel_password="")
        rc = mod.run_cycles(config=config, cycles=1, interval=0, wait=0)
        assert rc == 0
        assert created[0].joins == [(4, "")]

    def test_wait_countdown(self, tool_session_factory, monkeypatch, capsys):
        factory, _created = tool_session_factory
        monkeypatch.setattr(mod, "TeamTalkSession", factory(channel_id=9))
        slept: list[float] = []
        monkeypatch.setattr(mod.time, "sleep", slept.append)
        rc = mod.run_cycles(config=self._config(), cycles=1, interval=0, wait=7)
        assert rc == 0
        assert "7 seconds" in capsys.readouterr().out
        assert 7 in slept
class TestMain:
    def test_config_error_returns_2(self, capsys):
        assert mod.main(["--cycles", "2"]) == 2
        assert "--host is required" in capsys.readouterr().err

    def test_missing_channel_returns_2(self, capsys):
        assert mod.main(["--host", "h"]) == 2
        assert "--channel-id or --channel-path" in capsys.readouterr().err

    def test_bad_cycles_returns_2(self, capsys):
        assert mod.main(["--host", "h", "--channel-id", "1", "--cycles", "0"]) == 2
        assert "--cycles" in capsys.readouterr().err
