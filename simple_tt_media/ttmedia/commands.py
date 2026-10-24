"""Chat command parsing and dispatch.

Commands arrive as text messages beginning with the configured prefix
(default ``!``). Each handler gets the issuing user's nickname, the argument
string, and a ``reply`` callback. Network-bound work (resolving a query) is
pushed to a worker thread so the SDK event loop never stalls.
"""

from __future__ import annotations

import threading
from typing import Callable

from .config import Config
from .log import get_logger
from .models import format_duration
from .player import MediaPlayer
from .sources import SourceRegistry

log = get_logger(__name__)

Reply = Callable[[str], None]


class CommandHandler:
    def __init__(self, cfg: Config, player: MediaPlayer, registry: SourceRegistry) -> None:
        self._cfg = cfg
        self._player = player
        self._registry = registry
        self._prefix = cfg.bot.command_prefix
        self._ops = {n.lower() for n in cfg.bot.operators}
        self._handlers = {
            "help": self._help, "h": self._help,
            "play": self._play, "p": self._play,
            "skip": self._skip, "s": self._skip, "next": self._skip,
            "stop": self._stop,
            "clear": self._clear,
            "queue": self._queue, "q": self._queue,
            "nowplaying": self._now, "np": self._now,
            "sources": self._sources,
        }
        self._privileged = {"stop", "clear"}

    def is_command(self, text: str) -> bool:
        return text.strip().startswith(self._prefix)

    def _is_operator(self, nickname: str) -> bool:
        # Empty operator list => everyone is allowed.
        return not self._ops or nickname.lower() in self._ops

    def handle(self, nickname: str, text: str, reply: Reply) -> None:
        body = text.strip()[len(self._prefix):].strip()
        if not body:
            return
        name, _, arg = body.partition(" ")
        name = name.lower()
        handler = self._handlers.get(name)
        if handler is None:
            reply(f"Unknown command '{name}'. Try {self._prefix}help")
            return
        if name in self._privileged and not self._is_operator(nickname):
            reply("You don't have permission for that command.")
            return
        try:
            handler(nickname, arg.strip(), reply)
        except Exception as exc:  # noqa: BLE001
            log.exception("Command %s failed", name)
            reply(f"Error: {exc}")

    # ---- handlers -------------------------------------------------------

    def _help(self, nickname: str, arg: str, reply: Reply) -> None:
        p = self._prefix
        reply(
            f"Commands: {p}play <link or search>, {p}skip, {p}queue, "
            f"{p}nowplaying, {p}stop, {p}clear, {p}sources, {p}help"
        )

    def _play(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}play <link or search terms>")
            return
        reply(f"Searching for: {arg}")

        def worker() -> None:
            result = self._registry.resolve(arg, requested_by=nickname)
            if not result.ok:
                reply(result.error or "Nothing found.")
                return
            try:
                pos = self._player.enqueue(result.tracks)
            except ValueError as exc:
                reply(str(exc))
                return
            if len(result.tracks) > 1:
                reply(f"Queued {len(result.tracks)} tracks.")
            elif pos == 0:
                pass  # "Now playing" is announced by the player
            else:
                reply(f"Queued at #{pos}: {result.tracks[0].display()}")

        threading.Thread(target=worker, daemon=True,
                         name="resolve-play").start()

    def _skip(self, nickname: str, arg: str, reply: Reply) -> None:
        nxt = self._player.skip()
        if nxt is None and not self._player.is_playing:
            reply("Skipped. Queue is empty.")

    def _stop(self, nickname: str, arg: str, reply: Reply) -> None:
        self._player.stop()
        reply("Stopped and cleared the queue.")

    def _clear(self, nickname: str, arg: str, reply: Reply) -> None:
        n = self._player.clear()
        reply(f"Cleared {n} queued track(s).")

    def _queue(self, nickname: str, arg: str, reply: Reply) -> None:
        current = self._player.current
        items = self._player.queue_snapshot()
        lines = []
        if current:
            lines.append(f"Now: {current.display()}")
        if items:
            shown = items[:10]
            for i, track in enumerate(shown, 1):
                lines.append(f"{i}. {track.display()}")
            if len(items) > len(shown):
                lines.append(f"...and {len(items) - len(shown)} more")
        if not lines:
            reply("The queue is empty.")
        else:
            reply(" | ".join(lines))

    def _now(self, nickname: str, arg: str, reply: Reply) -> None:
        current = self._player.current
        if current is None:
            reply("Nothing is playing.")
            return
        extra = f" ({current.webpage_url})" if current.webpage_url else ""
        reply(f"Now playing: {current.display()}{extra}")

    def _sources(self, nickname: str, arg: str, reply: Reply) -> None:
        reply("Enabled sources: " + ", ".join(self._registry.enabled_names()))
