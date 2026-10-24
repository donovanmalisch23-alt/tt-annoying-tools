import pytest

from ttmedia.config import Config, dump_config, load_config
from ttmedia.errors import ConfigError
from ttmedia.sdk import bundled_wrapper_dir, resolve_wrapper_dir


def test_config_round_trip(tmp_path):
    cfg = Config()
    cfg.server.host = "h.example"
    cfg.server.username = "bot"
    cfg.bot.admins = ["alice", "bob"]
    cfg.sources.spotify_client_id = "id123"
    p = tmp_path / "config.toml"
    p.write_text(dump_config(cfg))
    loaded = load_config(p)
    assert loaded.server.host == "h.example"
    assert loaded.bot.admins == ["alice", "bob"]
    assert loaded.sources.spotify_client_id == "id123"


def test_env_overrides(tmp_path, monkeypatch):
    cfg = Config()
    cfg.server.host, cfg.server.username = "h", "u"
    p = tmp_path / "c.toml"
    p.write_text(dump_config(cfg))
    monkeypatch.setenv("TTMEDIA_PASSWORD", "secret")
    monkeypatch.setenv("SPOTIFY_CLIENT_ID", "envid")
    loaded = load_config(p)
    assert loaded.server.password == "secret"
    assert loaded.sources.spotify_client_id == "envid"


def test_validate_requires_host():
    with pytest.raises(ConfigError):
        Config().validate()


def test_validate_rejects_bad_channels():
    cfg = Config()
    cfg.server.host, cfg.server.username = "h", "u"
    cfg.audio.channels = 3
    with pytest.raises(ConfigError):
        cfg.validate()


def test_bundled_sdk_layout_is_windows_only():
    wrapper = bundled_wrapper_dir()
    assert (wrapper / "TeamTalk5.py").is_file(), "wrapper missing"
    sdk_root = wrapper.parent
    dll = sdk_root / "TeamTalk_DLL" / "TeamTalk5.dll"
    assert dll.is_file(), "Windows DLL missing"
    # No Linux/mac binaries should ship with the product.
    assert not (sdk_root / "TeamTalk_DLL" / "libTeamTalk5.so").exists()
    assert not (sdk_root / "TeamTalk_DLL" / "libTeamTalk5.dylib").exists()


def test_resolve_wrapper_prefers_bundled():
    assert resolve_wrapper_dir("") == bundled_wrapper_dir()


def test_windows_sibling_dll_folder_exists():
    # The wrapper's win32 branch does add_dll_directory(<wrapper>/../TeamTalk_DLL),
    # which raises if the folder is missing. Prove it exists with the DLL.
    wrapper = bundled_wrapper_dir()
    sibling = (wrapper / ".." / "TeamTalk_DLL").resolve()
    assert sibling.is_dir()
    assert (sibling / "TeamTalk5.dll").is_file()
