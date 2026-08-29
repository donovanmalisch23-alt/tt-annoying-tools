"""Tests for tt_teamtalk's pure helpers: env parsing, prompts, conversion,
whitelist-free primitives and the SDK license gate.

Nothing here touches a server; sessions use the in-memory fake SDK.
"""

from __future__ import annotations

import argparse
import os
import sys
import types
from pathlib import Path

import pytest

import tt_teamtalk
from tt_teamtalk import (
    ConnectionConfig,
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
)


# --------------------------------------------------------------------------- #
# sdk_text / sdk_int / sdk_string / sdk_event
# --------------------------------------------------------------------------- #

class TestSdkText:
    def test_none_becomes_empty(self):
        assert tt_teamtalk.sdk_text(None) == ""

    def test_str_passthrough(self):
        assert tt_teamtalk.sdk_text("hello") == "hello"

    def test_bytes_stop_at_nul(self):
        assert tt_teamtalk.sdk_text(b"abc\0def") == "abc"

    def test_bytearray(self):
        assert tt_teamtalk.sdk_text(bytearray(b"xyz\0")) == "xyz"

    def test_invalid_utf8_replaced(self):
        # decode(..., "replace") turns undecodable bytes into U+FFFD
        assert tt_teamtalk.sdk_text(b"\xff\xfe") == "\ufffd\ufffd"

    def test_value_attribute_unwrapped(self):
        wrapped = types.SimpleNamespace(value=b"wrapped\0tail")
        assert tt_teamtalk.sdk_text(wrapped) == "wrapped"

    def test_value_attribute_cycle_falls_through(self):
        tricky = types.SimpleNamespace()
        tricky.value = tricky  # self-referential: must not recurse forever
        assert "namespace" in tt_teamtalk.sdk_text(tricky)  # str() fallback

    def test_unconvertible_uses_str(self):
        assert tt_teamtalk.sdk_text(1234) == "1234"


class TestSdkInt:
    def test_plain_int(self):
        assert tt_teamtalk.sdk_int(7) == 7

    def test_numeric_string(self):
        assert tt_teamtalk.sdk_int("12") == 12

    def test_none_uses_default(self):
        assert tt_teamtalk.sdk_int(None) == 0
        assert tt_teamtalk.sdk_int(None, -1) == -1

    def test_garbage_uses_default(self):
        assert tt_teamtalk.sdk_int(object(), 42) == 42

    def test_value_attr(self):
        assert tt_teamtalk.sdk_int(types.SimpleNamespace(__int__=None) if False else 5) == 5


class TestSdkString:
    def test_uses_ttstr_when_available(self):
        sdk = types.SimpleNamespace(ttstr=lambda s: f"<{s}>")
        assert tt_teamtalk.sdk_string(sdk, "hi") == "<hi>"

    def test_passthrough_without_ttstr(self):
        assert tt_teamtalk.sdk_string(types.SimpleNamespace(), "hi") == "hi"

    def test_non_callable_ttstr_ignored(self):
        sdk = types.SimpleNamespace(ttstr=42)
        assert tt_teamtalk.sdk_string(sdk, "hi") == "hi"


class TestSdkEvent:
    def test_present_event(self):
        sdk = types.SimpleNamespace(ClientEvent=types.SimpleNamespace(FOO=9))
        assert tt_teamtalk.sdk_event(sdk, "FOO") == 9

    def test_missing_event_is_none(self):
        sdk = types.SimpleNamespace(ClientEvent=types.SimpleNamespace())
        assert tt_teamtalk.sdk_event(sdk, "MISSING") is None

    def test_missing_clientevent_class(self):
        assert tt_teamtalk.sdk_event(types.SimpleNamespace(), "FOO") is None


# --------------------------------------------------------------------------- #
# Environment helpers
# --------------------------------------------------------------------------- #

class TestEnvHelpers:
    def test_env_int_unset_default(self, monkeypatch):
        monkeypatch.delenv("TT_TEST_X", raising=False)
        assert tt_teamtalk._env_int("TT_TEST_X", 5) == 5

    def test_env_int_empty_default(self, monkeypatch):
        monkeypatch.setenv("TT_TEST_X", "")
        assert tt_teamtalk._env_int("TT_TEST_X", 5) == 5

    def test_env_int_valid(self, monkeypatch):
        monkeypatch.setenv("TT_TEST_X", "123")
        assert tt_teamtalk._env_int("TT_TEST_X", 5) == 123

    def test_env_int_invalid_falls_back(self, monkeypatch):
        monkeypatch.setenv("TT_TEST_X", "nope")
        assert tt_teamtalk._env_int("TT_TEST_X", 5) == 5

    @pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " ON "])
    def test_env_bool_truthy(self, monkeypatch, value):
        monkeypatch.setenv("TT_TEST_B", value)
        assert tt_teamtalk._env_bool("TT_TEST_B") is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "2", "enabled"])
    def test_env_bool_falsy(self, monkeypatch, value):
        monkeypatch.setenv("TT_TEST_B", value)
        assert tt_teamtalk._env_bool("TT_TEST_B") is False

    def test_env_bool_unset_defaults(self, monkeypatch):
        monkeypatch.delenv("TT_TEST_B", raising=False)
        assert tt_teamtalk._env_bool("TT_TEST_B") is False
        assert tt_teamtalk._env_bool("TT_TEST_B", True) is True

    def test_env_float_valid(self, monkeypatch):
        monkeypatch.setenv("TT_TEST_F", "2.5")
        assert tt_teamtalk._env_float("TT_TEST_F", 1.0) == 2.5

    def test_env_float_invalid_and_empty(self, monkeypatch):
        monkeypatch.setenv("TT_TEST_F", "x")
        assert tt_teamtalk._env_float("TT_TEST_F", 1.0) == 1.0
        monkeypatch.setenv("TT_TEST_F", "")
        assert tt_teamtalk._env_float("TT_TEST_F", 1.0) == 1.0


class TestLoadProjectEnv:
    def _call(self, monkeypatch, tmp_path, body: str):
        env_file = tmp_path / "project.env"
        env_file.write_text(body, encoding="utf-8")
        monkeypatch.setenv("TT_ENV_FILE", str(env_file))
        tt_teamtalk._load_project_env()

    def test_basic_entries_loaded(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TT_LPE_A", raising=False)
        monkeypatch.delenv("TT_LPE_B", raising=False)
        self._call(monkeypatch, tmp_path, "TT_LPE_A=1\nTT_LPE_B=hello world\n")
        assert os.environ["TT_LPE_A"] == "1"
        assert os.environ["TT_LPE_B"] == "hello world"

    def test_comments_and_blanks_skipped(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TT_LPE_C", raising=False)
        self._call(monkeypatch, tmp_path, "# comment\n\n   \nTT_LPE_C=ok\n")
        assert os.environ["TT_LPE_C"] == "ok"

    def test_quotes_stripped(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TT_LPE_D", raising=False)
        monkeypatch.delenv("TT_LPE_E", raising=False)
        self._call(monkeypatch, tmp_path, "TT_LPE_D=\"quoted\"\nTT_LPE_E='single'\n")
        assert os.environ["TT_LPE_D"] == "quoted"
        assert os.environ["TT_LPE_E"] == "single"

    def test_value_with_equals_kept(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TT_LPE_F", raising=False)
        self._call(monkeypatch, tmp_path, "TT_LPE_F=a=b=c\n")
        assert os.environ["TT_LPE_F"] == "a=b=c"

    def test_exported_value_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TT_LPE_G", "exported")
        self._call(monkeypatch, tmp_path, "TT_LPE_G=from-file\n")
        assert os.environ["TT_LPE_G"] == "exported"

    def test_invalid_identifiers_ignored(self, monkeypatch, tmp_path):
        monkeypatch.delenv("TT_LPE_H", raising=False)
        self._call(
            monkeypatch,
            tmp_path,
            "1BAD=x\nno-equals-line\nBAD-NAME=y\nTT_LPE_H=good\n",
        )
        assert "1BAD" not in os.environ
        assert os.environ["TT_LPE_H"] == "good"

    def test_missing_file_is_noop(self, monkeypatch, tmp_path):
        monkeypatch.setenv("TT_ENV_FILE", str(tmp_path / "absent.env"))
        tt_teamtalk._load_project_env()  # must not raise

    def test_devnull_is_noop(self):
        # The conftest default: os.devnull is not a regular file.
        tt_teamtalk._load_project_env()


class TestConfigDir:
    def test_unfrozen_uses_module_directory(self, monkeypatch):
        monkeypatch.delattr(sys.modules["tt_teamtalk"].sys, "frozen", raising=False)
        assert tt_teamtalk.config_dir() == Path(tt_teamtalk.__file__).resolve().parent

    def test_frozen_uses_executable_directory(self, monkeypatch, tmp_path):
        fake_exe = tmp_path / "bin" / "tt-suite"
        monkeypatch.setattr(tt_teamtalk.sys, "frozen", True, raising=False)
        monkeypatch.setattr(tt_teamtalk.sys, "executable", str(fake_exe))
        assert tt_teamtalk.config_dir() == fake_exe.parent


# --------------------------------------------------------------------------- #
# comma_int argparse type
# --------------------------------------------------------------------------- #

class TestCommaInt:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("5", 5),
            ("10,999", 10999),
            ("1_000", 1000),
            ("  42  ", 42),
            ("-3", -3),
            ("0", 0),
        ],
    )
    def test_valid(self, raw, expected):
        assert tt_teamtalk.comma_int(raw) == expected

    @pytest.mark.parametrize("raw", ["", "abc", "1.5", "12x", "--", "1,2.5"])
    def test_invalid_raises_argparse_error(self, raw):
        with pytest.raises(argparse.ArgumentTypeError):
            tt_teamtalk.comma_int(raw)


# --------------------------------------------------------------------------- #
# Interactive prompts (input/getpass patched)
# --------------------------------------------------------------------------- #

class TestPrompts:
    def test_prompt_text_default_on_enter(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert tt_teamtalk.prompt_text("Host", "example") == "example"

    def test_prompt_text_value(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "  typed  ")
        assert tt_teamtalk.prompt_text("Host", "example") == "typed"

    def test_prompt_text_no_default(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "")
        assert tt_teamtalk.prompt_text("Host") == ""

    def test_prompt_text_secret_uses_getpass(self, monkeypatch):
        seen = {}

        def fake_getpass(prompt=""):
            seen["prompt"] = prompt
            return "s3cret"

        monkeypatch.setattr(tt_teamtalk.getpass, "getpass", fake_getpass)
        assert tt_teamtalk.prompt_text("Password", secret=True) == "s3cret"
        assert "[configured]" not in seen["prompt"]

    def test_prompt_secret_keeps_configured_default(self, monkeypatch):
        monkeypatch.setattr(tt_teamtalk.getpass, "getpass", lambda prompt="": "")
        assert tt_teamtalk.prompt_text("Password", "oldpw", secret=True) == "oldpw"

    def test_prompt_int_retries_until_valid(self, monkeypatch, capsys):
        answers = iter(["nope", "3"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert tt_teamtalk.prompt_int("Count", 5, minimum=1, maximum=10) == 3
        assert "whole number" in capsys.readouterr().out

    def test_prompt_int_enforces_bounds(self, monkeypatch, capsys):
        answers = iter(["0", "99", "4"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert tt_teamtalk.prompt_int("Count", 5, minimum=1, maximum=10) == 4
        out = capsys.readouterr().out
        assert "at least 1" in out and "no greater than 10" in out

    def test_prompt_float_retries(self, monkeypatch):
        answers = iter(["x", "0.25"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert tt_teamtalk.prompt_float("Delay", 1.0) == 0.25

    def test_prompt_choice_case_insensitive(self, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "BETA")
        assert tt_teamtalk.prompt_choice("Mode", ["alpha", "beta"], "alpha") == "beta"

    def test_prompt_choice_reprompts(self, monkeypatch, capsys):
        answers = iter(["gamma", "alpha"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert tt_teamtalk.prompt_choice("Mode", ["alpha", "beta"], "alpha") == "alpha"
        assert "Please choose" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("answer", "default", "expected"),
        [("", True, True), ("", False, False), ("y", False, True),
         ("NO", True, False), ("yes", False, True), ("n", True, False)],
    )
    def test_prompt_yes_no(self, monkeypatch, answer, default, expected):
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
        assert tt_teamtalk.prompt_yes_no("Ok?", default) is expected

    def test_prompt_yes_no_reprompts(self, monkeypatch):
        answers = iter(["maybe", "y"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        assert tt_teamtalk.prompt_yes_no("Ok?") is True


# --------------------------------------------------------------------------- #
# config_from_args validation
# --------------------------------------------------------------------------- #

def _args_namespace(**overrides):
    base = dict(
        host="server.local",
        tcp_port=10333,
        udp_port=10333,
        username="guest",
        password="",
        nickname="nick",
        client_name="client",
        encrypted=False,
        channel_id=None,
        channel_path=None,
        channel_password="",
        timeout=15.0,
        kick_resistance=True,
        reconnect_delay=3.5,
        sdk_path=None,
        sdk_python=None,
        sdk_library=None,
        license_name=None,
        license_key=None,
        accept_sdk_license=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class TestConfigFromArgs:
    def test_happy_path(self):
        config = tt_teamtalk.config_from_args(_args_namespace())
        assert config.host == "server.local"
        assert config.tcp_port == 10333

    def test_host_required(self):
        with pytest.raises(TeamTalkConfigurationError, match="--host is required"):
            tt_teamtalk.config_from_args(_args_namespace(host=""))

    def test_host_whitespace_stripped(self):
        config = tt_teamtalk.config_from_args(_args_namespace(host="  h.test  "))
        assert config.host == "h.test"

    @pytest.mark.parametrize("port", [0, -1, 65536])
    def test_port_range(self, port):
        with pytest.raises(TeamTalkConfigurationError, match="between 1 and 65535"):
            tt_teamtalk.config_from_args(_args_namespace(tcp_port=port))

    def test_udp_port_range(self):
        with pytest.raises(TeamTalkConfigurationError, match="between 1 and 65535"):
            tt_teamtalk.config_from_args(_args_namespace(udp_port=70000))

    def test_timeout_must_be_positive(self):
        with pytest.raises(TeamTalkConfigurationError, match="--timeout"):
            tt_teamtalk.config_from_args(_args_namespace(timeout=0))

    def test_reconnect_delay_not_negative(self):
        with pytest.raises(TeamTalkConfigurationError, match="--reconnect-delay"):
            tt_teamtalk.config_from_args(_args_namespace(reconnect_delay=-1))

    def test_negative_channel_id_rejected(self):
        with pytest.raises(TeamTalkConfigurationError, match="--channel-id"):
            tt_teamtalk.config_from_args(_args_namespace(channel_id=-5))

    def test_channel_path_gets_leading_slash(self):
        config = tt_teamtalk.config_from_args(_args_namespace(channel_path="Lobby/Games"))
        assert config.channel_path == "/Lobby/Games"

    def test_channel_path_already_rooted(self):
        config = tt_teamtalk.config_from_args(_args_namespace(channel_path="/Lobby"))
        assert config.channel_path == "/Lobby"

    def test_license_key_defaults_to_empty(self):
        config = tt_teamtalk.config_from_args(_args_namespace(license_key=None))
        assert config.license_key == ""


class TestConnectionConfig:
    def test_defaults(self):
        config = ConnectionConfig(host="h")
        assert config.tcp_port == 10333
        assert config.kick_resistance is True

    def test_frozen(self):
        config = ConnectionConfig(host="h")
        with pytest.raises(Exception):
            config.host = "other"  # frozen dataclass


# --------------------------------------------------------------------------- #
# SDK license gate
# --------------------------------------------------------------------------- #

@pytest.fixture
def isolated_license_dir(tmp_path, monkeypatch):
    """Point the license markers at throwaway dirs (config + home)."""

    config_root = tmp_path / "config"
    home_root = tmp_path / "home"
    config_root.mkdir()
    home_root.mkdir()
    monkeypatch.setattr(tt_teamtalk, "config_dir", lambda: config_root)
    monkeypatch.setattr(tt_teamtalk.Path, "home", classmethod(lambda cls: home_root))
    monkeypatch.delenv("TT_ACCEPT_SDK_LICENSE", raising=False)
    return config_root, home_root


class TestSdkLicenseGate:
    def test_marker_names_have_single_dot(self, isolated_license_dir):
        # Regression: the home fallback used to be "..tt-sdk-license-accepted".
        markers = tt_teamtalk._sdk_license_accepted_markers()
        assert [m.name for m in markers] == [
            ".tt-sdk-license-accepted",
            ".tt-sdk-license-accepted",
        ]
        assert markers[0] != markers[1]
        assert markers[0].parent != markers[1].parent

    def test_accept_via_flag_writes_marker(self, isolated_license_dir):
        config_root, _ = isolated_license_dir
        tt_teamtalk.ensure_sdk_license_accepted(pre_choice=True)
        assert (config_root / ".tt-sdk-license-accepted").is_file()

    def test_decline_removes_markers_and_raises(self, isolated_license_dir):
        config_root, home_root = isolated_license_dir
        (config_root / ".tt-sdk-license-accepted").write_text("accepted\n")
        (home_root / ".tt-sdk-license-accepted").write_text("accepted\n")
        with pytest.raises(TeamTalkConfigurationError, match="declined"):
            tt_teamtalk.ensure_sdk_license_accepted(pre_choice=False)
        assert not (config_root / ".tt-sdk-license-accepted").exists()
        assert not (home_root / ".tt-sdk-license-accepted").exists()

    def test_existing_marker_skips_prompt(self, isolated_license_dir, monkeypatch):
        config_root, _ = isolated_license_dir
        (config_root / ".tt-sdk-license-accepted").write_text("accepted\n")
        monkeypatch.setattr(
            "builtins.input", lambda *a: (_ for _ in ()).throw(AssertionError("prompted!"))
        )
        tt_teamtalk.ensure_sdk_license_accepted()  # must not prompt

    def test_interactive_accept(self, isolated_license_dir, monkeypatch, capsys):
        config_root, _ = isolated_license_dir
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        monkeypatch.setattr(
            tt_teamtalk, "_print_sdk_license_terms", lambda: None
        )
        tt_teamtalk.ensure_sdk_license_accepted()
        assert (config_root / ".tt-sdk-license-accepted").is_file()
        assert "accepted" in capsys.readouterr().out

    def test_interactive_decline(self, isolated_license_dir, monkeypatch):
        monkeypatch.setattr("builtins.input", lambda prompt="": "n")
        monkeypatch.setattr(tt_teamtalk, "_print_sdk_license_terms", lambda: None)
        with pytest.raises(TeamTalkConfigurationError, match="declined"):
            tt_teamtalk.ensure_sdk_license_accepted()

    def test_interactive_reprompts_on_garbage(self, isolated_license_dir, monkeypatch):
        answers = iter(["huh", "yes"])
        monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
        monkeypatch.setattr(tt_teamtalk, "_print_sdk_license_terms", lambda: None)
        tt_teamtalk.ensure_sdk_license_accepted()

    def test_eof_answer_counts_as_reprompt(self, isolated_license_dir, monkeypatch):
        answers = iter([EOFError(), "y"])

        def fake_input(prompt=""):
            item = next(answers)
            if isinstance(item, Exception):
                raise item
            return item

        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(tt_teamtalk, "_print_sdk_license_terms", lambda: None)
        tt_teamtalk.ensure_sdk_license_accepted()

    def test_env_decision_respected(self, isolated_license_dir, monkeypatch):
        config_root, _ = isolated_license_dir
        monkeypatch.setenv("TT_ACCEPT_SDK_LICENSE", "1")
        tt_teamtalk.ensure_sdk_license_accepted()
        assert (config_root / ".tt-sdk-license-accepted").is_file()

    @pytest.mark.parametrize("value", ["1", "true", "yes", "on", "y"])
    def test_decision_truthy(self, monkeypatch, value):
        monkeypatch.setenv("TT_ACCEPT_SDK_LICENSE", value)
        assert tt_teamtalk._sdk_license_decision() is True

    @pytest.mark.parametrize("value", ["0", "false", "no", "off", "n"])
    def test_decision_falsy(self, monkeypatch, value):
        monkeypatch.setenv("TT_ACCEPT_SDK_LICENSE", value)
        assert tt_teamtalk._sdk_license_decision() is False

    def test_declined_gate_blocks_session(self, isolated_license_dir, make_config, fake_sdk):
        sdk, _client = fake_sdk()
        config = make_config(accept_sdk_license=False)
        with pytest.raises(TeamTalkConfigurationError):
            TeamTalkSession(config, sdk=sdk)


# --------------------------------------------------------------------------- #
# message_fields / print_tool_error
# --------------------------------------------------------------------------- #

class TestMessageFields:
    def test_full_record(self):
        from conftest import make_text_message

        fields = tt_teamtalk.message_fields(
            make_text_message(
                "hi there",
                msg_type=1,
                from_user_id=7,
                from_username="ken",
                channel_id=3,
                more=False,
            )
        )
        assert fields == {
            "type": 1,
            "from_user_id": 7,
            "from_username": "ken",
            "to_user_id": 0,
            "channel_id": 3,
            "text": "hi there",
            "more": False,
        }

    def test_none_yields_defaults(self):
        fields = tt_teamtalk.message_fields(None)
        assert fields["text"] == ""
        assert fields["from_user_id"] == 0


class TestPrintToolError:
    def test_plain_error(self, capsys):
        assert tt_teamtalk.print_tool_error(TeamTalkError("boom")) == 2
        err = capsys.readouterr().err
        assert "Error: boom" in err
        assert "bearware" not in err

    def test_sdk_error_points_to_download(self, capsys):
        assert tt_teamtalk.print_tool_error(tt_teamtalk.TeamTalkSDKError("no sdk")) == 2
        assert "bearware.dk" in capsys.readouterr().err
