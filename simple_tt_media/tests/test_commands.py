import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ttmedia.commands import CommandHandler
from ttmedia.config import Config
from ttmedia.models import ResolveResult, Track
from ttmedia.player import MediaPlayer


class FakeStreamer:
    def stream(self, url):
        return True

    def stop(self):
        pass


class FakeRegistry:
    def __init__(self):
        self.last_query = None

    def enabled_names(self):
        return ["local", "youtube"]

    def resolve(self, query, requested_by=""):
        self.last_query = query
        return ResolveResult(tracks=[Track(
            title=query, stream_url="http://x", source="youtube",
            requested_by=requested_by)])


def build(ops=None):
    cfg = Config()
    cfg.bot.operators = ops or []
    player = MediaPlayer(FakeStreamer(), lambda m: None)
    registry = FakeRegistry()
    return CommandHandler(cfg, player, registry), player, registry


def test_is_command():
    h, _, _ = build()
    assert h.is_command("!play x")
    assert not h.is_command("hello")


def test_unknown_command_replies():
    h, _, _ = build()
    out = []
    h.handle("user", "!frobnicate", out.append)
    assert out and "Unknown" in out[0]


def test_play_resolves_and_queues():
    h, player, registry = build()
    out = []
    h.handle("user", "!play never gonna give you up", out.append)
    # worker thread resolves; join by polling player state
    import time
    for _ in range(50):
        if player.is_playing:
            break
        time.sleep(0.01)
    assert player.is_playing
    assert registry.last_query == "never gonna give you up"


def test_privileged_blocked_for_non_operator():
    h, player, _ = build(ops=["admin"])
    out = []
    h.handle("randomuser", "!stop", out.append)
    assert out and "permission" in out[0].lower()


def test_privileged_allowed_for_operator():
    h, player, _ = build(ops=["admin"])
    out = []
    h.handle("admin", "!stop", out.append)
    assert out and "Stopped" in out[0]
