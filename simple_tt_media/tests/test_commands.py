from ttmedia.commands import build_dispatcher
from ttmedia.errors import SourceError
from ttmedia.models import Track


class FakePlayer:
    def __init__(self):
        self.current = None
        self._q = []

    def enqueue(self, tracks):
        if self.current is None:
            self.current = tracks[0]
            return 0
        self._q.extend(tracks)
        return len(self._q)

    def queue_snapshot(self):
        return list(self._q)

    @property
    def is_paused(self):
        return False

    def toggle_pause(self):
        return True

    def stop(self):
        self.current = None
        self._q.clear()


class FakeSources:
    service = "youtube"

    def is_url(self, t):
        return t.startswith("http")

    def services(self):
        return ["youtube", "soundcloud", "apple"]

    def set_service(self, n):
        if n not in self.services():
            raise SourceError(f"Unknown service '{n}'.")
        FakeSources.service = n

    def from_input(self, text, requested_by=""):
        return [Track(title="linked", playback_url="http://x", source="url")]

    def search(self, query, requested_by="", limit=None):
        return [Track(title=f"{query} #{i}", source="youtube",
                      query=f"q{i}", needs_resolve=True) for i in range(1, 4)]


class FakeClient:
    def __init__(self):
        self.channel_msgs = []
        self.stopped = False

    def send_channel(self, text, channel_id=None):
        self.channel_msgs.append(text)

    def shutdown(self):
        self.stopped = True


class Cfg:
    class audio:
        max_volume = 200
        max_track_seconds = 0

    class bot:
        command_workers = 4
        rate_limit_per_10s = 1000
        max_user_queue = 0


def run(dispatcher, text, user_id=5, is_admin=False):
    out = []
    dispatcher.handle(text=text, requester="Tester", user_id=user_id,
                      is_admin=is_admin, is_private=True, reply=out.append)
    # commands run on a worker thread; wait for it
    import time
    for _ in range(200):
        if out:
            break
        time.sleep(0.01)
    return out


def make():
    player, sources, client = FakePlayer(), FakeSources(), FakeClient()
    return build_dispatcher(player, sources, client, Cfg()), player, sources, client


def test_unknown_command_errors():
    d, *_ = make()
    out = run(d, "frobnicate the wizzle")
    assert out and "Unknown command" in out[0]


def test_help_lists_commands():
    d, *_ = make()
    out = run(d, "h")
    assert out and "Playback" in out[0] and "np" in out[0]


def test_help_for_one_command():
    d, *_ = make()
    out = run(d, "h seek")
    assert out and "Usage:" in out[0]


def test_search_then_pick_by_number():
    d, player, *_ = make()
    run(d, "search daft punk")
    out = run(d, "p 2")       # bare number plays result #2
    assert player.current is not None
    assert "#2" in player.current.title


def test_pick_command_alias():
    d, player, *_ = make()
    run(d, "find daft punk")
    run(d, "pick 1")
    assert player.current is not None and "#1" in player.current.title


def test_admin_only_blocked():
    d, *_ = make()
    out = run(d, "quit", is_admin=False)
    assert out and "administrators only" in out[0].lower()


def test_admin_quit_allowed():
    d, player, sources, client = make()
    run(d, "quit", is_admin=True)
    import time
    for _ in range(200):
        if client.stopped:
            break
        time.sleep(0.01)
    assert client.stopped


def test_service_switch_and_unknown():
    d, player, sources, client = make()
    out = run(d, "sv soundcloud")
    assert out and "soundcloud" in out[0]
    out = run(d, "sv nope")
    assert out and "Unknown service" in out[0]


def test_volume_usage_error():
    d, *_ = make()
    out = run(d, "vol loud")
    assert out and "Usage" in out[0]


def test_rate_limit_blocks_floods():
    player, sources, client = FakePlayer(), FakeSources(), FakeClient()

    class RLCfg(Cfg):
        class bot(Cfg.bot):
            rate_limit_per_10s = 3
    d = build_dispatcher(player, sources, client, RLCfg())
    outs = [run(d, "about", user_id=99) for _ in range(5)]
    assert any("too fast" in (o[0].lower() if o else "") for o in outs)
