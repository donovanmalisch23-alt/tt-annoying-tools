import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttmedia.models import Track
from ttmedia.player import MediaPlayer


class FakeStreamer:
    def __init__(self, succeed=True):
        self.succeed = succeed
        self.started = []
        self.stops = 0

    def stream(self, url):
        self.started.append(url)
        return self.succeed

    def stop(self):
        self.stops += 1


def make_track(name):
    return Track(title=name, stream_url=f"http://x/{name}", source="test")


def collect_announcer():
    msgs = []
    return msgs, msgs.append


def test_enqueue_starts_immediately():
    streamer = FakeStreamer()
    msgs, announce = collect_announcer()
    p = MediaPlayer(streamer, announce)
    pos = p.enqueue([make_track("a")])
    assert pos == 0
    assert p.is_playing
    assert streamer.started == ["http://x/a"]
    assert any("Now playing" in m for m in msgs)


def test_second_track_queues():
    p = MediaPlayer(FakeStreamer(), lambda m: None)
    p.enqueue([make_track("a")])
    pos = p.enqueue([make_track("b")])
    assert pos == 1
    assert len(p.queue_snapshot()) == 1


def test_finish_advances_queue():
    streamer = FakeStreamer()
    p = MediaPlayer(streamer, lambda m: None)
    p.enqueue([make_track("a"), make_track("b")])
    assert p.current.title == "a"
    p.on_stream_finished(success=True)
    assert p.current.title == "b"
    p.on_stream_finished(success=True)
    assert p.current is None


def test_skip():
    streamer = FakeStreamer()
    p = MediaPlayer(streamer, lambda m: None)
    p.enqueue([make_track("a"), make_track("b")])
    nxt = p.skip()
    assert nxt.title == "b"
    assert streamer.stops == 1


def test_stop_clears():
    streamer = FakeStreamer()
    p = MediaPlayer(streamer, lambda m: None)
    p.enqueue([make_track("a"), make_track("b")])
    p.stop()
    assert p.current is None
    assert p.queue_snapshot() == []


def test_clear_keeps_current():
    p = MediaPlayer(FakeStreamer(), lambda m: None)
    p.enqueue([make_track("a"), make_track("b"), make_track("c")])
    removed = p.clear()
    assert removed == 2
    assert p.current.title == "a"


def test_unplayable_track_is_skipped():
    streamer = FakeStreamer(succeed=False)
    msgs, announce = collect_announcer()
    p = MediaPlayer(streamer, announce)
    p.enqueue([make_track("a")])
    assert p.current is None
    assert any("couldn't play" in m.lower() for m in msgs)


def test_queue_full_raises():
    p = MediaPlayer(FakeStreamer(), lambda m: None, max_queue=1)
    try:
        p.enqueue([make_track("a"), make_track("b")])
    except ValueError as exc:
        assert "full" in str(exc).lower()
    else:
        raise AssertionError("expected ValueError")
