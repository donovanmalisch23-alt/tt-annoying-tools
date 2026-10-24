import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from ttmedia.config import Config, ConfigError, dump_config, load_config


def test_round_trip(tmp_path):
    cfg = Config()
    cfg.server.host = "host.example"
    cfg.server.username = "bot"
    cfg.bot.operators = ["alice", "bob"]
    cfg.sources.applemusic_enabled = True
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg))
    loaded = load_config(path)
    assert loaded.server.host == "host.example"
    assert loaded.server.username == "bot"
    assert loaded.bot.operators == ["alice", "bob"]
    assert loaded.sources.applemusic_enabled is True


def test_env_override(tmp_path, monkeypatch):
    cfg = Config()
    cfg.server.host = "h"
    cfg.server.username = "u"
    path = tmp_path / "config.toml"
    path.write_text(dump_config(cfg))
    monkeypatch.setenv("TT_PASSWORD", "secret-from-env")
    loaded = load_config(path)
    assert loaded.server.password == "secret-from-env"


def test_validate_requires_host():
    cfg = Config()
    with pytest.raises(ConfigError):
        cfg.validate()


def test_validate_spotify_needs_keys():
    cfg = Config()
    cfg.server.host = "h"
    cfg.server.username = "u"
    cfg.sources.spotify_enabled = True
    with pytest.raises(ConfigError):
        cfg.validate()


def test_missing_file(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")
