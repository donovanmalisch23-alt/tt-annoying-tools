"""Tests for tt_profile (shared --profile support for every tool)."""

from __future__ import annotations

import pytest

import tt_profile
import tt_spammer
import tt_loic
from tt_teamtalk import TeamTalkConfigurationError


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Keep teamtalk.env / TT_* environment variables out of the defaults."""
    for name in list(__import__("os").environ):
        if name.startswith(("TT_", "TEAMTALK_")):
            monkeypatch.delenv(name, raising=False)


def write_profile(tmp_path, name="test.profile", text=""):
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


class TestNormalizeKey:
    def test_dash_and_underscore_are_equivalent(self):
        assert tt_profile.normalize_key("tcp-port") == "tcp_port"
        assert tt_profile.normalize_key("--TCP-Port") == "tcp_port"
        assert tt_profile.normalize_key("tcp_port") == "tcp_port"


class TestFindProfile:
    def test_bare_name_searches_current_directory(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, "home.profile", "host = h\n")
        assert tt_profile.find_profile("home") == tmp_path / "home.profile"

    def test_suffix_is_optional_in_the_file_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, "lab", "host = h\n")
        assert tt_profile.find_profile("lab") == tmp_path / "lab"

    def test_explicit_path(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        sub = tmp_path / "servers"
        sub.mkdir()
        write_profile(sub, "lab.profile", "host = h\n")
        assert tt_profile.find_profile(str(sub / "lab.profile")) == sub / "lab.profile"

    def test_missing_profile_names_searched_paths(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        with pytest.raises(TeamTalkConfigurationError, match="not found"):
            tt_profile.find_profile("nowhere")


class TestLoadProfile:
    def test_parses_keys_values_and_comments(self, tmp_path):
        path = write_profile(
            tmp_path,
            text="# comment\n\nhost = example.com\nTCP-Port = 1234\npassword =\n",
        )
        settings = tt_profile.load_profile(path)
        assert settings == {
            "host": "example.com",
            "tcp_port": "1234",
            "password": "",
        }

    def test_quotes_are_stripped(self, tmp_path):
        path = write_profile(tmp_path, text='password = "s3cret"\n')
        assert tt_profile.load_profile(path)["password"] == "s3cret"

    def test_line_without_equals_is_an_error(self, tmp_path):
        path = write_profile(tmp_path, text="host example.com\n")
        with pytest.raises(TeamTalkConfigurationError, match="key = value"):
            tt_profile.load_profile(path)

    def test_duplicate_key_is_an_error(self, tmp_path):
        path = write_profile(tmp_path, text="host = a\nhost = b\n")
        with pytest.raises(TeamTalkConfigurationError, match="more than once"):
            tt_profile.load_profile(path)


class TestParseWithProfile:
    def test_applies_settings(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(
            tmp_path,
            text="host = example.com\ntcp-port = 1234\nusername = alice\nencrypted = true\n",
        )
        args = tt_profile.parse_args_with_profile(
            tt_spammer.build_parser(), ["--profile", "test"]
        )
        assert args.host == "example.com"
        assert args.tcp_port == 1234
        assert args.username == "alice"
        assert args.encrypted is True

    def test_explicit_flag_beats_profile(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="host = example.com\n")
        args = tt_profile.parse_args_with_profile(
            tt_spammer.build_parser(),
            ["--profile", "test", "--host", "other.example.com"],
        )
        assert args.host == "other.example.com"

    def test_store_false_via_profile(self, tmp_path, monkeypatch):
        # kick_resistance defaults on; a profile saying false must inject
        # --no-kick-resistance, the flag that actually expresses it.
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="kick_resistance = no\n")
        args = tt_profile.parse_args_with_profile(
            tt_spammer.build_parser(), ["--profile", "test", "--host", "h"]
        )
        assert args.kick_resistance is False

    def test_keys_for_other_tools_are_ignored(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="host = example.com\nthreads = 32\nmode = tcp\n")
        args = tt_profile.parse_args_with_profile(
            tt_spammer.build_parser(), ["--profile", "test"]
        )
        assert args.host == "example.com"
        assert not hasattr(args, "threads")
        # And the loic parser does pick its own keys up from the same file.
        args = tt_profile.parse_args_with_profile(
            tt_loic.build_parser(),
            ["--profile", "test", "--confirm"],
        )
        assert args.threads == 32
        assert args.mode == "tcp"

    def test_unknown_key_is_an_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="hast = typo\n")
        with pytest.raises(TeamTalkConfigurationError, match="unknown setting"):
            tt_profile.parse_args_with_profile(
                tt_spammer.build_parser(), ["--profile", "test"]
            )

    def test_bad_value_uses_the_tools_own_validation(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="tcp_port = not-a-port\n")
        with pytest.raises(TeamTalkConfigurationError, match="tcp_port"):
            tt_profile.parse_args_with_profile(
                tt_spammer.build_parser(), ["--profile", "test"]
            )

    def test_choice_is_validated(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        write_profile(tmp_path, text="mode = carrier-pigeon\n")
        with pytest.raises(TeamTalkConfigurationError, match="carrier-pigeon"):
            tt_profile.parse_args_with_profile(
                tt_loic.build_parser(), ["--profile", "test", "--confirm"]
            )

    def test_no_profile_acts_like_parse_args(self):
        args = tt_profile.parse_args_with_profile(
            tt_spammer.build_parser(), ["--host", "h"]
        )
        assert args.host == "h"


class TestEveryToolHasProfile:
    @pytest.mark.parametrize("module_name", tt_profile.TOOL_MODULES)
    def test_parser_exposes_profile(self, module_name):
        import importlib

        module = importlib.import_module(module_name)
        parser = module.build_parser()
        assert any(
            action.dest == "profile"
            for action in parser._actions
            if action.option_strings
        )


class TestRenderProfile:
    def test_chosen_active_skipped_commented(self):
        text = tt_profile.render_profile(
            "lab", {"host": "example.com", "encrypted": "true"}, ["threads", "mode"]
        )
        assert "host = example.com" in text
        assert "encrypted = true" in text
        assert "# threads =" in text
        assert "# mode =" in text
        # The file must round-trip through the loader.
        tmp = __import__("pathlib").Path("/tmp/_render_roundtrip.profile")
        tmp.write_text(text, encoding="utf-8")
        settings = tt_profile.load_profile(tmp)
        assert settings == {"host": "example.com", "encrypted": "true"}
        tmp.unlink()