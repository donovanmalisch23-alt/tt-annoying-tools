from ttmedia.models import RepeatMode, Track
from ttmedia.player import Player


class FakeEngine:
    def __init__(self):
        self.played = []
        self.stopped = 0
        self._paused = False
        self._playing = False
        self._vol = 100
        self._elapsed = 0.0

    def play(self, track):
        self.played.append(track)
        self._playing = True
        self._paused = False

    def stop(self):
        self.stopped += 1
        self._playing = False
        self._paused = False

    def pause(self):
        if self._playing and not self._paused:
            self._paused = True
            return True
        return False

    def resume(self):
        if self._playing and self._paused:
            self._paused = False
            return True
        return False

    def seek(self, s):
        self._elapsed = s
        return self._playing

    def set_volume(self, p):
        self._vol = max(0, min(200, p))
        return round(self._vol)

    @property
    def is_playing(self):
        return self._playing

    @property
    def is_paused(self):
        return self._paused

    @property
    def volume_percent(self):
        return round(self._vol)

    def elapsed(self):
        return self._elapsed


def track(name, **kw):
    return Track(title=name, playback_url=f"http://x/{name}", source="test", **kw)


def make(resolve=lambda t: True):
    eng = FakeEngine()
    announces = []
    p = Player(eng, announce=announces.append, resolve_fn=resolve)
    return p, eng, announces


def test_enqueue_starts_immediately():
    p, eng, ann = make()
    assert p.enqueue([track("a")]) == 0
    assert eng.played[0].title == "a"
    assert any("Now playing" in m for m in ann)


def test_second_track_queues():
    p, eng, _ = make()
    p.enqueue([track("a")])
    assert p.enqueue([track("b")]) == 1
    assert len(p.queue_snapshot()) == 1


def test_finish_advances():
    p, eng, _ = make()
    p.enqueue([track("a"), track("b")])
    cur = p.current
    p.on_track_finished(cur, "finished")
    assert p.current.title == "b"
    p.on_track_finished(p.current, "finished")
    assert p.current is None


def test_repeat_track():
    p, eng, _ = make()
    p.set_repeat(RepeatMode.TRACK)
    p.enqueue([track("a")])
    cur = p.current
    p.on_track_finished(cur, "finished")
    assert p.current.title == "a"


def test_repeat_queue_cycles():
    p, eng, _ = make()
    p.set_repeat(RepeatMode.QUEUE)
    p.enqueue([track("a"), track("b")])
    p.on_track_finished(p.current, "finished")
    assert p.current.title == "b"
    p.on_track_finished(p.current, "finished")
    assert p.current.title == "a"


def test_error_reresolves_once_then_skips():
    attempts = {"n": 0}

    def resolve(t):
        attempts["n"] += 1
        return False  # never resolves -> should give up after one retry

    p, eng, ann = make(resolve=resolve)
    t = Track(title="bad", query="something", playback_url="http://x",
              source="youtube")
    p.enqueue([t])                      # plays immediately (already has url)
    p.on_track_finished(p.current, "error")   # schedule a retry (needs resolve)
    # retry attempt resolves -> False -> skipped
    assert p.current is None
    assert any("Couldn't play" in m or "couldn't load" in m.lower() for m in ann)


def test_stop_then_late_callback_ignored():
    p, eng, _ = make()
    p.enqueue([track("a"), track("b")])
    cur = p.current
    p.stop()
    # a late finish for the stopped track must not resurrect playback
    p.on_track_finished(cur, "finished")
    assert p.current is None
    assert p.queue_snapshot() == []


def test_skip_and_previous():
    p, eng, _ = make()
    p.enqueue([track("a"), track("b")])
    p.skip()
    assert p.current.title == "b"
    p.previous()
    assert p.current.title == "a"


def test_select_drops_earlier():
    p, eng, _ = make()
    p.enqueue([track("a"), track("b"), track("c"), track("d")])
    # playing a; queue b,c,d. select 2 -> play c
    p.select(2)
    assert p.current.title == "c"


def test_volume_and_shuffle_flags():
    p, eng, _ = make()
    assert p.set_volume(250) == 200  # capped by fake engine
    p.set_shuffle(True)
    assert p.shuffle is True


def test_queue_full_raises():
    p, eng, _ = make()
    p2 = Player(eng, announce=lambda m: None, resolve_fn=lambda t: True, max_queue=1)
    try:
        p2.enqueue([track("a"), track("b")])
    except ValueError as e:
        assert "full" in str(e).lower()
    else:
        raise AssertionError("expected ValueError")
