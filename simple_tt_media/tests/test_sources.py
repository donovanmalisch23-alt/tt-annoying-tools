import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttmedia.config import Config
from ttmedia.models import ResolveResult, Track
from ttmedia.sources import SourceRegistry
from ttmedia.sources.base import Source
from ttmedia.sources.local import LocalSource


def test_local_matches_file(tmp_path):
    f = tmp_path / "song.mp3"
    f.write_bytes(b"\x00")
    src = LocalSource()
    assert src.matches(str(f))
    result = src.resolve(str(f))
    assert result.ok
    assert result.tracks[0].title == "song"


def test_local_matches_direct_url():
    src = LocalSource()
    assert src.matches("https://cdn.example.com/a.mp3")
    assert not src.matches("https://example.com/watch?v=abc")


class FakeAudio(Source):
    name = "fake"
    is_audio_provider = True

    def matches(self, query):
        return True

    def resolve(self, query, requested_by=""):
        return ResolveResult(tracks=[Track(
            title="resolved", stream_url="http://audio/x", source="fake")])

    def resolve_search(self, query, requested_by=""):
        return Track(title=f"yt:{query}", stream_url="http://audio/found",
                    source="fake")


class FakeMeta(Source):
    name = "meta"
    is_audio_provider = False

    def matches(self, query):
        return "meta://" in query

    def resolve(self, query, requested_by=""):
        return ResolveResult(tracks=[Track(
            title="Song", artist="Artist", stream_url="",
            source="meta", search_query="Artist Song")])


def test_metadata_backfilled_via_audio():
    registry = SourceRegistry([FakeMeta(), FakeAudio()], audio_provider=FakeAudio())
    result = registry.resolve("meta://thing")
    assert result.ok
    track = result.tracks[0]
    # keeps metadata title, gains a playable URL
    assert track.title == "Song"
    assert track.stream_url == "http://audio/found"
    assert track.playable


def test_metadata_without_audio_provider_errors():
    registry = SourceRegistry([FakeMeta()], audio_provider=None)
    result = registry.resolve("meta://thing")
    assert not result.ok


def test_registry_from_config_orders_youtube_last():
    cfg = Config()
    cfg.sources.youtube_enabled = True
    cfg.sources.applemusic_enabled = True
    registry = SourceRegistry.from_config(cfg)
    names = registry.enabled_names()
    assert names[-1] == "youtube"
    assert "local" == names[0]
    assert "applemusic" in names


def test_windows_dll_dir_flat(tmp_path):
    from ttmedia.sdk import _windows_dll_dir
    (tmp_path / "TeamTalk5.py").write_text("")
    (tmp_path / "TeamTalk5.dll").write_bytes(b"\x00")
    assert _windows_dll_dir(tmp_path) == tmp_path


def test_windows_dll_dir_sibling(tmp_path):
    from ttmedia.sdk import _windows_dll_dir
    py = tmp_path / "TeamTalkPy"
    dll = tmp_path / "TeamTalk_DLL"
    py.mkdir(); dll.mkdir()
    (py / "TeamTalk5.py").write_text("")
    (dll / "TeamTalk5.dll").write_bytes(b"\x00")
    assert _windows_dll_dir(py) == dll


def test_windows_dll_dir_missing(tmp_path):
    from ttmedia.sdk import _windows_dll_dir
    assert _windows_dll_dir(tmp_path) is None
