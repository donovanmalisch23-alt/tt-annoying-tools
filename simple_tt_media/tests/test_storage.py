import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttmedia.models import Track
from ttmedia.storage import PlaylistStore, query_for


def test_query_for_prefers_url():
    t = Track(title="Song", artist="Artist", stream_url="x",
              webpage_url="http://page")
    assert query_for(t) == "http://page"


def test_query_for_falls_back_to_artist_title():
    t = Track(title="Song", artist="Artist", stream_url="x")
    assert query_for(t) == "Artist Song"


def test_save_get_delete(tmp_path):
    store = PlaylistStore(str(tmp_path / "pl.json"))
    assert store.names() == []
    store.save("Party", ["a", "b", "c"])
    assert store.get("party") == ["a", "b", "c"]   # case-insensitive
    assert "party" in store.names()
    assert store.delete("PARTY") is True
    assert store.names() == []
    assert store.delete("missing") is False


def test_corrupt_file_is_tolerated(tmp_path):
    p = tmp_path / "pl.json"
    p.write_text("not json {{{")
    store = PlaylistStore(str(p))
    assert store.names() == []  # fails soft
