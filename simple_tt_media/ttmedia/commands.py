"""Chat command parsing and dispatch.

Commands arrive as text messages beginning with the configured prefix
(default ``!``). Each handler gets the issuing user's nickname, the argument
string, and a ``reply`` callback. Network-bound work (resolving a query,
loading a playlist) is pushed to a worker thread so the SDK event loop never
stalls.

Anyone may search and queue; the playback *controls* (skip, stop, volume,
loop, saved playlists, ...) are operator-gated when operators are configured.
"""

from __future__ import annotations

import threading
from typing import Callable, List

from .config import Config
from .log import get_logger
from .player import LoopMode, MediaPlayer
from .sources import SourceRegistry
from .storage import PlaylistStore, query_for

log = get_logger(__name__)

Reply = Callable[[str], None]


def parse_timecode(text: str) -> int:
    """Parse '90', '1:30' or '1:02:03' into milliseconds. Raises ValueError."""
    text = text.strip()
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or len(parts) > 3:
        raise ValueError("Use seconds or mm:ss or h:mm:ss.")
    secs = 0
    for part in parts:
        secs = secs * 60 + int(part)
    return secs * 1000


class CommandHandler:
    def __init__(self, cfg: Config, player: MediaPlayer,
                 registry: SourceRegistry, playlists: PlaylistStore) -> None:
        self._cfg = cfg
        self._player = player
        self._registry = registry
        self._playlists = playlists
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
            "pause": self._pause, "resume": self._resume,
            "seek": self._seek,
            "volume": self._volume, "vol": self._volume,
            "loop": self._loop, "shuffle": self._shuffle,
            "autoplay": self._autoplay,
            "save": self._save, "load": self._load,
            "playlists": self._playlists_list, "delete": self._delete,
            "sources": self._sources,
        }
        # Everyone may search/queue and read state; controls are gated.
        self._open = {"help", "h", "play", "p", "queue", "q",
                      "nowplaying", "np", "sources", "playlists"}

    def is_command(self, text: str) -> bool:
        return text.strip().startswith(self._prefix)

    def _is_operator(self, nickname: str) -> bool:
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
        if name not in self._open and not self._is_operator(nickname):
            reply("You don't have permission for that command.")
            return
        try:
            handler(nickname, arg.strip(), reply)
        except Exception as exc:  # noqa: BLE001
            log.exception("Command %s failed", name)
            reply(f"Error: {exc}")

    # ---- search / queue (open) -----------------------------------------

    def _help(self, nickname: str, arg: str, reply: Reply) -> None:
        p = self._prefix
        reply(
            f"Commands: {p}play <link/search>, {p}skip, {p}queue, "
            f"{p}nowplaying, {p}pause, {p}resume, {p}seek <mm:ss>, "
            f"{p}volume <0-300>, {p}loop <off|track|queue>, {p}shuffle, "
            f"{p}autoplay <on|off>, {p}stop, {p}clear, "
            f"{p}save/{p}load/{p}playlists/{p}delete <name>, {p}sources"
        )

    def _play(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}play <link or search terms>")
            return
        reply(f"Searching for: {arg}")
        threading.Thread(
            target=self._resolve_and_queue, args=(arg, nickname, reply),
            daemon=True, name="resolve-play").start()

    def _resolve_and_queue(self, query: str, nickname: str, reply: Reply) -> None:
        result = self._registry.resolve(query, requested_by=nickname)
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
        elif pos != 0:
            reply(f"Queued at #{pos}: {result.tracks[0].display()}")

    def _queue(self, nickname: str, arg: str, reply: Reply) -> None:
        current = self._player.current
        items = self._player.queue_snapshot()
        lines = []
        if current:
            tag = " (paused)" if self._player.is_paused else ""
            lines.append(f"Now: {current.display()}{tag}")
        for i, track in enumerate(items[:10], 1):
            lines.append(f"{i}. {track.display()}")
        if len(items) > 10:
            lines.append(f"...and {len(items) - 10} more")
        reply(" | ".join(lines) if lines else "The queue is empty.")

    def _now(self, nickname: str, arg: str, reply: Reply) -> None:
        current = self._player.current
        if current is None:
            reply("Nothing is playing.")
            return
        extra = f" ({current.webpage_url})" if current.webpage_url else ""
        tag = " [paused]" if self._player.is_paused else ""
        reply(f"Now playing: {current.display()}{tag}{extra}")

    def _sources(self, nickname: str, arg: str, reply: Reply) -> None:
        reply("Enabled sources: " + ", ".join(self._registry.enabled_names())
              + f" | {self._player.modes_summary()}")

    def _playlists_list(self, nickname: str, arg: str, reply: Reply) -> None:
        names = self._playlists.names()
        reply("Saved playlists: " + ", ".join(names) if names
              else "No saved playlists yet.")

    # ---- controls (operator-gated) -------------------------------------

    def _skip(self, nickname: str, arg: str, reply: Reply) -> None:
        self._player.skip()
        if not self._player.is_playing:
            reply("Skipped. Queue is empty.")

    def _stop(self, nickname: str, arg: str, reply: Reply) -> None:
        self._player.stop()
        reply("Stopped and cleared the queue.")

    def _clear(self, nickname: str, arg: str, reply: Reply) -> None:
        reply(f"Cleared {self._player.clear()} queued track(s).")

    def _pause(self, nickname: str, arg: str, reply: Reply) -> None:
        reply("Paused." if self._player.pause() else "Nothing to pause.")

    def _resume(self, nickname: str, arg: str, reply: Reply) -> None:
        reply("Resumed." if self._player.resume() else "Nothing to resume.")

    def _seek(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}seek <seconds or mm:ss>")
            return
        ms = parse_timecode(arg)
        if self._player.seek(ms):
            reply(f"Seeked to {arg}.")
        else:
            reply("Nothing is playing.")

    def _volume(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Volume is {self._player.volume}%.")
            return
        try:
            percent = int(arg.rstrip("%"))
        except ValueError:
            reply("Usage: !volume <0-300>")
            return
        reply(f"Volume set to {self._player.set_volume(percent)}%.")

    def _loop(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Loop is '{self._player.loop.value}'. "
                  f"Use {self._prefix}loop <off|track|queue>.")
            return
        try:
            mode = LoopMode(arg.lower())
        except ValueError:
            reply("Loop mode must be off, track or queue.")
            return
        self._player.set_loop(mode)
        reply(f"Loop set to '{mode.value}'.")

    def _shuffle(self, nickname: str, arg: str, reply: Reply) -> None:
        a = arg.lower()
        if a in ("on", "off"):
            self._player.set_shuffle(a == "on")
            reply(f"Shuffle {a}.")
        else:
            self._player.shuffle_now()
            reply("Shuffled the queue.")

    def _autoplay(self, nickname: str, arg: str, reply: Reply) -> None:
        a = arg.lower()
        if a not in ("on", "off"):
            reply(f"Usage: {self._prefix}autoplay <on|off> "
                  f"(currently {'on' if self._player.autoplay else 'off'})")
            return
        self._player.set_autoplay(a == "on")
        reply(f"Autoplay {a}.")

    # ---- playlists (operator-gated) ------------------------------------

    def _save(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}save <name>")
            return
        tracks: List = []
        if self._player.current:
            tracks.append(self._player.current)
        tracks.extend(self._player.queue_snapshot())
        if not tracks:
            reply("Nothing playing or queued to save.")
            return
        queries = [query_for(t) for t in tracks]
        self._playlists.save(arg, queries)
        reply(f"Saved {len(queries)} track(s) as playlist '{arg}'.")

    def _delete(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}delete <name>")
            return
        reply(f"Deleted playlist '{arg}'." if self._playlists.delete(arg)
              else f"No playlist named '{arg}'.")

    def _load(self, nickname: str, arg: str, reply: Reply) -> None:
        if not arg:
            reply(f"Usage: {self._prefix}load <name>")
            return
        queries = self._playlists.get(arg)
        if not queries:
            reply(f"No playlist named '{arg}'.")
            return
        reply(f"Loading playlist '{arg}' ({len(queries)} tracks)...")
        threading.Thread(
            target=self._load_worker, args=(queries, nickname, reply),
            daemon=True, name="load-playlist").start()

    def _load_worker(self, queries: List[str], nickname: str, reply: Reply) -> None:
        loaded = 0
        for query in queries:
            result = self._registry.resolve(query, requested_by=nickname)
            if result.ok:
                try:
                    self._player.enqueue(result.tracks)
                    loaded += len(result.tracks)
                except ValueError:
                    break
        reply(f"Loaded {loaded} track(s)." if loaded
              else "Couldn't load any tracks from that playlist.")
