"""Application wiring: build every part and run the bot.

Construction order matters because the pieces reference each other:
SDK -> client -> audio engine -> sources -> player -> command dispatcher.
The engine's "track finished" callback and the player's "announce" and
"resolve" callbacks are small shims onto this object so there are no import
cycles between the layers.
"""

from __future__ import annotations

from .commands import build_dispatcher
from .config import Config
from .log import get_logger
from .models import Track
from .player import Player
from .sdk import load_sdk
from .sources import SourceRegistry
from .teamtalk import AudioEngine, TeamTalkClient, TextMessageEvent

log = get_logger(__name__)


class TTMediaApp:
    def __init__(self, cfg: Config) -> None:
        cfg.validate()
        self._cfg = cfg

        tt = load_sdk(cfg.sdk.path)
        self._client = TeamTalkClient(cfg, tt)

        from .tools import find_deno, find_ffmpeg
        ffmpeg = find_ffmpeg(cfg.tools.ffmpeg_path)
        if not ffmpeg:
            log.warning("ffmpeg was not found; playback will not work. Run Setup.")
        self._engine = AudioEngine(self._client, cfg.audio, self._on_finished)
        self._engine.set_ffmpeg_path(ffmpeg)

        # Resolve Deno once and feed the discovered path to the sources.
        cfg.tools.deno_path = find_deno(cfg.tools.deno_path)
        self._sources = SourceRegistry(cfg)
        self._player = Player(
            self._engine, announce=self._announce,
            resolve_fn=self._sources.resolve, max_queue=cfg.sources.max_playlist_tracks)
        self._dispatcher = build_dispatcher(
            self._player, self._sources, self._client, cfg)

        self._client.on_ready = self._on_ready
        self._client.on_text_message = self._on_text_message

    def run(self) -> None:
        log.info("Simple TT Media starting; search service: %s; sources: %s",
                 self._sources.service, ", ".join(self._sources.enabled_names()))
        self._client.run_forever()

    def shutdown(self) -> None:
        try:
            self._engine.shutdown()
        finally:
            self._client.shutdown()

    # ---- callbacks ------------------------------------------------------

    def _on_ready(self) -> None:
        log.info("Ready. Private-message the bot 'h' for commands.")

    def _on_finished(self, track: Track, reason: str) -> None:
        self._player.on_track_finished(track, reason)

    def _announce(self, text: str) -> None:
        if self._cfg.bot.announce_now_playing:
            self._client.send_channel(text)

    def _on_text_message(self, event: TextMessageEvent) -> None:
        text = event.text.strip()
        if event.is_private:
            reply = lambda m: self._client.reply_private(event.from_id, m)  # noqa: E731
        else:
            prefix = self._cfg.bot.channel_prefix
            if not prefix or not text.startswith(prefix):
                return
            text = text[len(prefix):].strip()
            reply = lambda m: self._client.send_channel(m, event.channel_id)  # noqa: E731
        if not text:
            return
        self._dispatcher.handle(
            text=text, requester=event.nickname, user_id=event.from_id,
            is_admin=event.is_admin, is_private=event.is_private, reply=reply)
