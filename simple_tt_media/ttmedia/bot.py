"""The TeamTalk media bot.

This is the transport layer. It owns the connection to the server and the
native SDK client, implements the :class:`~ttmedia.player.Streamer` protocol
the player needs (start/stop a media stream), and turns incoming channel/
private text messages into command-handler calls.

Design notes
------------
* The SDK ships a ``TeamTalk`` class whose ``runEventLoop`` dispatches to
  overridable ``on*`` hooks. We build a thin subclass at runtime (the SDK is
  only importable once located) that forwards those hooks to this bot.
* All SDK interaction happens on one thread (the event loop). Only
  query-resolution is offloaded to worker threads (see commands.py), and the
  player serialises its own state with a lock, so we never touch the SDK
  concurrently.
* The connect → login → join sequence is event-driven: each step kicks off
  the next in response to the SDK event confirming the previous one. Any
  disconnect bubbles up and the outer ``start`` loop reconnects.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

import threading as _threading

from .commands import CommandHandler
from .config import Config
from .log import get_logger
from .player import LoopMode, MediaPlayer
from .sdk import load_sdk
from .sources import SourceRegistry
from .storage import PlaylistStore

log = get_logger(__name__)

CLIENT_NAME = "SimpleTTMedia"
STATUS_ONLINE = 0  # TeamTalk STATUSMODE_ONLINE


class MediaBot:
    def __init__(self, cfg: Config) -> None:
        cfg.validate()
        self._cfg = cfg
        self._tt_mod = load_sdk(cfg.sdk.path)
        self._client = self._build_client()
        self._registry = SourceRegistry.from_config(cfg)
        self._player = MediaPlayer(
            streamer=self,
            announce=self._announce,
            max_queue=cfg.playback.max_queue,
            announce_now_playing=cfg.playback.announce_now_playing,
            default_volume=cfg.playback.default_volume,
            loop=LoopMode(cfg.playback.loop),
            shuffle=cfg.playback.shuffle,
            autoplay=cfg.playback.autoplay,
            autoplay_fn=self._autoplay,
        )
        self._playlists = PlaylistStore(cfg.playback.playlists_file)
        self._commands = CommandHandler(
            cfg, self._player, self._registry, self._playlists)
        self._my_user_id = 0
        self._running = False
        self._connected = threading.Event()
        self._stop_requested = False

    # ---- public lifecycle ----------------------------------------------

    def run_forever(self) -> None:
        """Connect and service events, reconnecting on failure until stopped."""
        self._running = True
        delay = max(1, self._cfg.bot.reconnect_seconds)
        while self._running and not self._stop_requested:
            try:
                self._connect_sequence()
                self._event_loop()
            except KeyboardInterrupt:
                log.info("Interrupted; shutting down.")
                break
            except Exception as exc:  # noqa: BLE001 - keep the bot alive
                log.error("Session ended: %s", exc)
            if self._stop_requested or not self._running:
                break
            log.info("Reconnecting in %ss...", delay)
            self._safe_disconnect()
            time.sleep(delay)
        self._safe_disconnect()
        self._client.closeTeamTalk()
        log.info("Bot stopped.")

    def shutdown(self) -> None:
        self._stop_requested = True
        self._running = False

    # ---- Streamer protocol (used by the player) ------------------------

    def _video_codec(self):
        tt = self._tt_mod
        codec = tt.VideoCodec()
        codec.nCodec = tt.Codec.NO_CODEC
        return codec

    def _playback(self, offset_ms, paused: bool, gain: int):
        """Build a MediaFilePlayback with gain applied via the preprocessor."""
        tt = self._tt_mod
        pb = tt.MediaFilePlayback()
        pb.uOffsetMSec = (tt.TT_MEDIAPLAYBACK_OFFSET_IGNORE
                          if offset_ms is None else int(offset_ms))
        pb.bPaused = bool(paused)
        ap = tt.AudioPreprocessor()
        ap.nPreprocessor = tt.AudioPreprocessorType.TEAMTALK_AUDIOPREPROCESSOR
        ap.ttpreprocessor.nGainLevel = int(gain)
        ap.ttpreprocessor.bMuteLeftSpeaker = False
        ap.ttpreprocessor.bMuteRightSpeaker = False
        pb.audioPreprocessor = ap
        return pb

    def stream(self, url: str, gain: int) -> bool:
        tt = self._tt_mod
        pb = self._playback(0, False, gain)
        try:
            ok = tt._StartStreamingMediaFileToChannelEx(
                self._client._tt, tt.ttstr(url), pb, self._video_codec())
        except Exception as exc:  # noqa: BLE001
            log.warning("start streaming failed: %s", exc)
            return False
        if not ok:
            log.warning("Server refused media stream for %s", url)
        return bool(ok)

    def update(self, offset_ms, paused: bool, gain: int) -> bool:
        tt = self._tt_mod
        pb = self._playback(offset_ms, paused, gain)
        try:
            return bool(tt._UpdateStreamingMediaFileToChannel(
                self._client._tt, pb, self._video_codec()))
        except Exception as exc:  # noqa: BLE001
            log.debug("update streaming failed: %s", exc)
            return False

    def stop(self) -> None:  # noqa: F811 - part of Streamer protocol
        try:
            self._client.stopStreamingMediaFileToChannel()
        except Exception as exc:  # noqa: BLE001
            log.debug("stopStreamingMediaFileToChannel: %s", exc)

    # ---- autoplay (runs off the event loop) ----------------------------

    def _autoplay(self, seed) -> None:
        def worker() -> None:
            try:
                tracks = self._registry.related(
                    seed, limit=3, exclude=self._player.recent_urls())
                if tracks:
                    self._player.enqueue(tracks)
                else:
                    self._player.clear_autoplay_pending()
            except Exception as exc:  # noqa: BLE001
                log.warning("autoplay worker failed: %s", exc)
                self._player.clear_autoplay_pending()
        _threading.Thread(target=worker, daemon=True, name="autoplay").start()

    # ---- connection sequence -------------------------------------------

    def _connect_sequence(self) -> None:
        s = self._cfg.server
        log.info("Connecting to %s:%s ...", s.host, s.tcp_port)
        self._connected.clear()
        ok = self._client.connect(
            self._tt_mod.ttstr(s.host), s.tcp_port, s.udp_port,
            0, 0, s.encrypted,
        )
        if not ok:
            raise RuntimeError("connect() call failed (bad host/port?)")

    def _event_loop(self) -> None:
        # Block up to 1s per message so KeyboardInterrupt / stop is responsive.
        while self._running and not self._stop_requested:
            self._client.runEventLoop(1000)

    def _safe_disconnect(self) -> None:
        try:
            self.stop()
            self._client.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ---- SDK event hooks (called from the event loop) ------------------

    def _on_connect_success(self) -> None:
        s = self._cfg.server
        log.info("Connected; logging in as %s", s.username)
        self._client.doLogin(
            self._tt_mod.ttstr(self._cfg.bot.nickname),
            self._tt_mod.ttstr(s.username),
            self._tt_mod.ttstr(s.password),
            self._tt_mod.ttstr(CLIENT_NAME),
        )

    def _on_connect_failed(self) -> None:
        log.error("Connection failed.")
        raise RuntimeError("connection failed")

    def _on_connection_lost(self) -> None:
        log.warning("Connection lost.")
        raise RuntimeError("connection lost")

    def _on_logged_in(self, user_id: int) -> None:
        self._my_user_id = user_id
        if user_id <= 0:
            log.error("Login rejected (check username/password).")
            raise RuntimeError("login rejected")
        log.info("Logged in (user id %s); joining %s", user_id,
                 self._cfg.server.channel)
        self._join_channel()
        self._client.doChangeStatus(
            STATUS_ONLINE, self._tt_mod.ttstr(self._cfg.bot.status_message))

    def _on_cmd_error(self, clienterrormsg) -> None:
        msg = self._tt_mod.ttstr(clienterrormsg.szErrorMsg)
        log.warning("Server command error: %s", msg)

    def _join_channel(self) -> None:
        path = self._cfg.server.channel or "/"
        chan_id = self._client.getChannelIDFromPath(self._tt_mod.ttstr(path))
        if chan_id <= 0:
            log.error("Channel path %r not found; staying in root.", path)
            return
        self._client.doJoinChannelByID(
            chan_id, self._tt_mod.ttstr(self._cfg.server.channel_password))

    def _on_text_message(self, textmessage) -> None:
        tt = self._tt_mod
        if textmessage.nFromUserID == self._my_user_id:
            return  # ignore our own messages
        text = tt.ttstr(textmessage.szMessage)
        if not self._commands.is_command(text):
            return
        nickname = tt.ttstr(textmessage.szFromUsername)
        msg_type = textmessage.nMsgType
        from_user = textmessage.nFromUserID
        channel_id = textmessage.nChannelID

        if msg_type == tt.TextMsgType.MSGTYPE_CHANNEL:
            reply = lambda m: self._send_channel_message(channel_id, m)  # noqa: E731
        else:
            reply = lambda m: self._send_user_message(from_user, m)  # noqa: E731
        self._commands.handle(nickname, text, reply)

    def _on_stream_media_file(self, mediafileinfo) -> None:
        tt = self._tt_mod
        status = mediafileinfo.nStatus
        if status == tt.MediaFileStatus.MFS_FINISHED:
            self._player.on_stream_finished(success=True)
        elif status in (tt.MediaFileStatus.MFS_ERROR, tt.MediaFileStatus.MFS_ABORTED):
            self._player.on_stream_finished(success=False)
        # STARTED / PLAYING / PAUSED need no queue action.

    # ---- outgoing messages ---------------------------------------------

    def _announce(self, text: str) -> None:
        chan_id = self._client.getMyChannelID()
        if chan_id > 0:
            self._send_channel_message(chan_id, text)

    def _new_text_message(self):
        return self._tt_mod.TextMessage()

    def _send(self, build: Callable) -> None:
        """Send one message, splitting into SDK chunks if it's too long."""
        tt = self._tt_mod
        chunks = _split(text=build.__text__, limit=TT_CHUNK)
        for i, chunk in enumerate(chunks):
            msg = build()
            msg.szMessage = tt.ttstr(chunk)
            msg.bMore = i < len(chunks) - 1
            try:
                self._client.doTextMessage(msg)
            except Exception as exc:  # noqa: BLE001
                log.debug("doTextMessage failed: %s", exc)
                break

    def _send_channel_message(self, channel_id: int, text: str) -> None:
        tt = self._tt_mod

        def build():
            msg = tt.TextMessage()
            msg.nMsgType = tt.TextMsgType.MSGTYPE_CHANNEL
            msg.nChannelID = channel_id
            return msg
        build.__text__ = text
        self._send(build)

    def _send_user_message(self, user_id: int, text: str) -> None:
        tt = self._tt_mod

        def build():
            msg = tt.TextMessage()
            msg.nMsgType = tt.TextMsgType.MSGTYPE_USER
            msg.nToUserID = user_id
            return msg
        build.__text__ = text
        self._send(build)

    # ---- runtime subclass wiring ---------------------------------------

    def _build_client(self):
        tt = self._tt_mod
        bot = self

        class _Client(tt.TeamTalk):
            def onConnectSuccess(self):
                bot._on_connect_success()

            def onConnectFailed(self):
                bot._on_connect_failed()

            def onConnectionLost(self):
                bot._on_connection_lost()

            def onCmdMyselfLoggedIn(self, userid, useraccount):
                bot._on_logged_in(userid)

            def onCmdError(self, cmdid, clienterrormsg):
                bot._on_cmd_error(clienterrormsg)

            def onCmdUserTextMessage(self, textmessage):
                bot._on_text_message(textmessage)

            def onStreamMediaFile(self, mediafileinfo):
                bot._on_stream_media_file(mediafileinfo)

        return _Client()


# TeamTalk's TT_STRLEN is 512; leave headroom for multi-byte UTF-8.
TT_CHUNK = 450


def _split(text: str, limit: int) -> list:
    text = text or ""
    if len(text) <= limit:
        return [text]
    return [text[i:i + limit] for i in range(0, len(text), limit)]
