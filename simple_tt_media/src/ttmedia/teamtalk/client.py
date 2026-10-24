"""Thin, robust wrapper around the TeamTalk 5 SDK client instance.

Responsibilities:
  * connect -> login -> join, event-driven, with automatic reconnect;
  * run the SDK message loop on its own thread and dispatch the handful of
    events we care about to an injected handler;
  * send private / channel text messages (auto-split for the 512-char limit);
  * expose audio-block insertion and small lookups the rest of the app needs.

Everything SDK-specific lives here so the player, audio engine, sources and
commands never import the raw ctypes wrapper.
"""

from __future__ import annotations

import threading
import time
from typing import Callable, Optional

from ..config import Config
from ..errors import ConnectionFailed
from ..log import get_logger

log = get_logger(__name__)

CLIENT_NAME = "SimpleTTMedia"
STATUS_ONLINE = 0  # TeamTalk STATUSMODE_ONLINE


class TextMessageEvent:
    __slots__ = ("from_id", "from_username", "nickname", "is_private",
                 "channel_id", "text", "is_admin")

    def __init__(self, from_id, from_username, nickname, is_private,
                 channel_id, text, is_admin):
        self.from_id = from_id
        self.from_username = from_username
        self.nickname = nickname
        self.is_private = is_private
        self.channel_id = channel_id
        self.text = text
        self.is_admin = is_admin


class TeamTalkClient:
    def __init__(self, cfg: Config, tt_module) -> None:
        self._cfg = cfg
        self._tt = tt_module
        self._inst = tt_module.TeamTalk()
        self._ev = tt_module.ClientEvent
        self._running = False
        self._stop = False
        self._my_user_id = 0
        self._logged_in = threading.Event()
        self._in_channel = threading.Event()
        self._send_lock = threading.Lock()

        # Handlers (set by the application before start()).
        self.on_ready: Callable[[], None] = lambda: None
        self.on_text_message: Callable[[TextMessageEvent], None] = lambda e: None
        self.on_disconnected: Callable[[], None] = lambda: None
        self.on_audio_input: Callable[[int, int, int], None] = lambda s, q, e: None

    # ---- lifecycle ------------------------------------------------------

    @property
    def my_user_id(self) -> int:
        return self._my_user_id

    def run_forever(self) -> None:
        """Connect and service events until :meth:`shutdown`, reconnecting."""
        self._running = True
        delay = max(1, self._cfg.bot.reconnect_seconds)
        tt = self._tt
        while self._running and not self._stop:
            try:
                self._connect()
                self._loop()
            except ConnectionFailed as exc:
                log.warning("%s", exc)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error in client loop")
            if self._stop or not self._running:
                break
            self._safe_disconnect()
            log.info("Reconnecting in %ss ...", delay)
            self._sleep(delay)
        self._safe_disconnect()
        self._inst.closeTeamTalk()
        log.info("Client stopped.")

    def shutdown(self) -> None:
        self._stop = True
        self._running = False

    def _sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self._stop:
            time.sleep(0.2)

    def _connect(self) -> None:
        s = self._cfg.server
        tt = self._tt
        self._logged_in.clear()
        self._in_channel.clear()
        log.info("Connecting to %s:%s ...", s.host, s.tcp_port)
        ok = self._inst.connect(tt.ttstr(s.host), s.tcp_port, s.udp_port,
                                0, 0, s.encrypted)
        if not ok:
            raise ConnectionFailed("connect() failed (check host/port).")

    def _safe_disconnect(self) -> None:
        try:
            self._inst.disconnect()
        except Exception:  # noqa: BLE001
            pass

    # ---- event loop -----------------------------------------------------

    def _loop(self) -> None:
        tt = self._tt
        ev = self._ev
        while self._running and not self._stop:
            msg = self._inst.getMessage(500)
            e = msg.nClientEvent
            if e == ev.CLIENTEVENT_NONE:
                continue
            if e == ev.CLIENTEVENT_CON_SUCCESS:
                self._do_login()
            elif e in (ev.CLIENTEVENT_CON_FAILED, ev.CLIENTEVENT_CON_LOST):
                raise ConnectionFailed("Connection to the server was lost.")
            elif e == ev.CLIENTEVENT_CMD_MYSELF_LOGGEDIN:
                self._my_user_id = msg.nSource
                self._logged_in.set()
                self._join_channel()
            elif e == ev.CLIENTEVENT_CMD_ERROR:
                self._handle_cmd_error(msg)
            elif e == ev.CLIENTEVENT_CMD_USER_TEXTMSG:
                self._dispatch_text(msg.textmessage)
            elif e == ev.CLIENTEVENT_CMD_SUCCESS:
                # Joining the channel resolves asynchronously; confirm by id.
                if not self._in_channel.is_set() and self._inst.getMyChannelID() > 0:
                    self._in_channel.set()
                    self._announce_ready()
            elif e == ev.CLIENTEVENT_AUDIOINPUT:
                p = msg.audioinputprogress
                try:
                    self.on_audio_input(msg.nSource, p.uQueueMSec, p.uElapsedMSec)
                except Exception:  # noqa: BLE001
                    log.exception("on_audio_input handler failed")

    def _do_login(self) -> None:
        s = self._cfg.server
        tt = self._tt
        log.info("Connected; logging in as %s", s.username)
        self._inst.doLogin(tt.ttstr(s.nickname), tt.ttstr(s.username),
                           tt.ttstr(s.password), tt.ttstr(CLIENT_NAME))

    def _join_channel(self) -> None:
        s = self._cfg.server
        tt = self._tt
        if self._my_user_id <= 0:
            raise ConnectionFailed("Login was rejected (bad username/password?).")
        path = s.channel or "/"
        chan_id = self._inst.getChannelIDFromPath(tt.ttstr(path))
        if chan_id <= 0:
            log.error("Channel %r not found; joining root instead.", path)
            chan_id = self._inst.getChannelIDFromPath(tt.ttstr("/"))
        self._inst.doJoinChannelByID(chan_id, tt.ttstr(s.channel_password))
        self._inst.doChangeStatus(STATUS_ONLINE, tt.ttstr(s.status_message))

    def _announce_ready(self) -> None:
        log.info("Joined channel %s; ready.", self._inst.getMyChannelID())
        try:
            self.on_ready()
        except Exception:  # noqa: BLE001
            log.exception("on_ready handler failed")

    def _handle_cmd_error(self, msg) -> None:
        try:
            text = self._tt.ttstr(msg.clienterrormsg.szErrorMsg)
            no = msg.clienterrormsg.nErrorNo
        except Exception:  # noqa: BLE001
            text, no = "?", -1
        log.warning("Server command error %s: %s", no, text)

    # ---- incoming text --------------------------------------------------

    def _dispatch_text(self, tm) -> None:
        tt = self._tt
        if tm.nFromUserID == self._my_user_id:
            return
        msg_type = tm.nMsgType
        is_private = msg_type == tt.TextMsgType.MSGTYPE_USER
        is_channel = msg_type == tt.TextMsgType.MSGTYPE_CHANNEL
        if not (is_private or is_channel):
            return
        event = TextMessageEvent(
            from_id=tm.nFromUserID,
            from_username=tt.ttstr(tm.szFromUsername),
            nickname=self._nickname_of(tm.nFromUserID, tt.ttstr(tm.szFromUsername)),
            is_private=is_private,
            channel_id=tm.nChannelID,
            text=tt.ttstr(tm.szMessage),
            is_admin=self._is_admin(tm.nFromUserID, tt.ttstr(tm.szFromUsername)),
        )
        try:
            self.on_text_message(event)
        except Exception:  # noqa: BLE001
            log.exception("on_text_message handler failed")

    def _nickname_of(self, user_id: int, fallback: str) -> str:
        try:
            user = self._inst.getUser(user_id)
            nick = self._tt.ttstr(user.szNickname)
            return nick or fallback or f"user{user_id}"
        except Exception:  # noqa: BLE001
            return fallback or f"user{user_id}"

    def _is_admin(self, user_id: int, username: str) -> bool:
        if username and username in self._cfg.bot.admins:
            return True
        try:
            user = self._inst.getUser(user_id)
            return bool(user.uUserType & self._tt.UserType.USERTYPE_ADMIN)
        except Exception:  # noqa: BLE001
            return False

    # ---- outgoing text --------------------------------------------------

    def reply_private(self, user_id: int, text: str) -> None:
        self._send_text(self._tt.TextMsgType.MSGTYPE_USER, user_id, 0, text)

    def send_channel(self, text: str, channel_id: Optional[int] = None) -> None:
        cid = channel_id if channel_id is not None else self._inst.getMyChannelID()
        if cid and cid > 0:
            self._send_text(self._tt.TextMsgType.MSGTYPE_CHANNEL, 0, cid, text)

    def _send_text(self, msg_type, to_user, to_channel, text: str) -> None:
        tt = self._tt
        # buildTextMessage splits long content into bMore-chained parts.
        parts = tt.buildTextMessage(text or "", msg_type,
                                    nToUserID=to_user, nChannelID=to_channel)
        with self._send_lock:
            for part in parts:
                try:
                    self._inst.doTextMessage(part)
                except Exception:  # noqa: BLE001
                    log.exception("Failed to send a text message")
                    break

    # ---- audio ----------------------------------------------------------

    def insert_audio_block(self, audio_block) -> bool:
        try:
            return bool(self._inst.insertAudioBlock(audio_block))
        except Exception:  # noqa: BLE001
            log.exception("insertAudioBlock failed")
            return False

    def end_audio_input(self) -> None:
        try:
            self._inst.insertAudioBlock(None)
        except Exception:  # noqa: BLE001
            pass

    # ---- misc lookups ---------------------------------------------------

    def my_channel_id(self) -> int:
        try:
            return self._inst.getMyChannelID()
        except Exception:  # noqa: BLE001
            return 0

    @property
    def tt(self):
        return self._tt
