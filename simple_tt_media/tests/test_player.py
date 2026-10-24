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
        self.updates = []

    def stream(self, url, gain):
        self.started.append((url, gain))
        return self.succeed

    def update(self, offset_ms, paused, gain):
        self.updates.append((offset_ms, paused, gain))
        return True

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
    assert streamer.started[0][0] == "http://x/a"
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


def test_volume_maps_to_gain():
    streamer = FakeStreamer()
    from ttmedia.player import percent_to_gain
    p = MediaPlayer(streamer, lambda m: None, default_volume=100)
    p.enqueue([make_track("a")])
    # started with gain for 100%
    assert p.volume == 100
    pct = p.set_volume(50)
    assert pct == 50
    assert streamer.updates[-1][2] == percent_to_gain(50)


def test_volume_clamped():
    p = MediaPlayer(FakeStreamer(), lambda m: None)
    assert p.set_volume(100000) <= 300 * 10  # within gain ceiling as percent


def test_pause_resume():
    streamer = FakeStreamer()
    p = MediaPlayer(streamer, lambda m: None)
    p.enqueue([make_track("a")])
    assert p.pause() is True
    assert p.is_paused
    assert streamer.updates[-1][1] is True
    assert p.resume() is True
    assert not p.is_paused


def test_loop_track_requeues():
    from ttmedia.player import LoopMode
    p = MediaPlayer(FakeStreamer(), lambda m: None, loop=LoopMode.TRACK)
    p.enqueue([make_track("a")])
    assert p.current.title == "a"
    p.on_stream_finished(success=True)
    assert p.current.title == "a"  # replayed


def test_loop_queue_cycles():
    from ttmedia.player import LoopMode
    p = MediaPlayer(FakeStreamer(), lambda m: None, loop=LoopMode.QUEUE)
    p.enqueue([make_track("a"), make_track("b")])
    assert p.current.title == "a"
    p.on_stream_finished(success=True)
    assert p.current.title == "b"
    p.on_stream_finished(success=True)
    assert p.current.title == "a"  # 'a' cycled back


def test_seek_passes_offset():
    streamer = FakeStreamer()
    p = MediaPlayer(streamer, lambda m: None)
    p.enqueue([make_track("a")])
    assert p.seek(90000) is True
    assert streamer.updates[-1][0] == 90000


def test_autoplay_triggers_fn_when_idle():
    calls = []
    p = MediaPlayer(FakeStreamer(), lambda m: None, autoplay=True,
                    autoplay_fn=lambda seed: calls.append(seed))
    p.enqueue([make_track("a")])
    p.on_stream_finished(success=True)  # queue empties -> autoplay fires
    assert len(calls) == 1
    assert calls[0].title == "a"
