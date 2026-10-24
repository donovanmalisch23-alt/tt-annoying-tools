"""Playback queue and state machine.

The player owns the queue and the notion of "what's playing now", plus the
playback modes (loop, shuffle, autoplay) and live controls (pause/resume,
seek, volume). It talks to the TeamTalk server through a small
:class:`Streamer` protocol (implemented by the bot) and announces changes
through a callback. Keeping those as injected dependencies makes the whole
thing unit-testable without a live server or the SDK.

Volume is expressed to the outside world as a percentage; internally it maps
to the SDK gain scale where 1000 == 100%.
"""

from __future__ import annotations

import random
import threading
from collections import deque
from enum import Enum
from typing import Callable, Deque, List, Optional, Protocol

from .log import get_logger
from .models import Track

log = get_logger(__name__)

GAIN_AT_100_PERCENT = 1000      # SDK SOUND_GAIN_DEFAULT
GAIN_MAX = 32000                # SDK SOUND_GAIN_MAX
MAX_VOLUME_PERCENT = 300        # sane ceiling we expose to users


class LoopMode(str, Enum):
    OFF = "off"
    TRACK = "track"
    QUEUE = "queue"


def percent_to_gain(percent: float) -> int:
    gain = round((percent / 100.0) * GAIN_AT_100_PERCENT)
    return max(0, min(GAIN_MAX, gain))


def gain_to_percent(gain: int) -> int:
    return round((gain / GAIN_AT_100_PERCENT) * 100)


class Streamer(Protocol):
    """What the player needs from the transport layer (the bot)."""

    def stream(self, url: str, gain: int) -> bool: ...
    def update(self, offset_ms: Optional[int], paused: bool, gain: int) -> bool: ...
    def stop(self) -> None: ...


class MediaPlayer:
    def __init__(
        self,
        streamer: Streamer,
        announce: Callable[[str], None],
        max_queue: int = 200,
        announce_now_playing: bool = True,
        default_volume: int = 100,
        loop: LoopMode = LoopMode.OFF,
        shuffle: bool = False,
        autoplay: bool = False,
        autoplay_fn: Optional[Callable[[Track], None]] = None,
    ) -> None:
        self._streamer = streamer
        self._announce = announce
        self._max_queue = max_queue
        self._announce_now_playing = announce_now_playing
        self._queue: Deque[Track] = deque()
        self._current: Optional[Track] = None
        self._history: List[Track] = []
        self._gain = percent_to_gain(default_volume)
        self._paused = False
        self._loop = LoopMode(loop)
        self._shuffle = shuffle
        self._autoplay = autoplay
        self._autoplay_fn = autoplay_fn
        self._autoplay_pending = False
        self._lock = threading.RLock()

    # ---- queries --------------------------------------------------------

    @property
    def current(self) -> Optional[Track]:
        return self._current

    @property
    def is_playing(self) -> bool:
        return self._current is not None

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def volume(self) -> int:
        return gain_to_percent(self._gain)

    @property
    def loop(self) -> LoopMode:
        return self._loop

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def autoplay(self) -> bool:
        return self._autoplay

    def queue_snapshot(self) -> List[Track]:
        with self._lock:
            return list(self._queue)

    def modes_summary(self) -> str:
        return (f"volume {self.volume}% | loop {self._loop.value} | "
                f"shuffle {'on' if self._shuffle else 'off'} | "
                f"autoplay {'on' if self._autoplay else 'off'}"
                + (" | paused" if self._paused else ""))

    # ---- queue mutations ------------------------------------------------

    def enqueue(self, tracks: List[Track]) -> int:
        """Add tracks; start playback if idle. Returns the 1-based queued
        position of the first added track, or 0 if it started immediately."""
        with self._lock:
            if len(self._queue) + len(tracks) > self._max_queue:
                raise ValueError(f"Queue is full (max {self._max_queue}).")
            self._autoplay_pending = False
            start_now = not self.is_playing and not self._queue
            self._queue.extend(tracks)
            if start_now:
                self._play_next_locked()
                return 0
            return len(self._queue) - len(tracks) + 1

    def skip(self) -> Optional[Track]:
        """Skip the current track (ignores per-track loop). Returns next."""
        with self._lock:
            self._stop_stream()
            if self._current is not None:
                self._history.append(self._current)
            self._current = None
            self._play_next_locked()
            return self._current

    def stop(self) -> None:
        """Stop playback and clear everything."""
        with self._lock:
            self._stop_stream()
            self._current = None
            self._queue.clear()
            self._paused = False

    def clear(self) -> int:
        """Clear the pending queue but keep the current track playing."""
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
            return n

    def shuffle_now(self) -> None:
        """Shuffle the pending queue in place."""
        with self._lock:
            items = list(self._queue)
            random.shuffle(items)
            self._queue = deque(items)

    # ---- live controls --------------------------------------------------

    def pause(self) -> bool:
        with self._lock:
            if not self.is_playing or self._paused:
                return False
            self._paused = True
            self._streamer.update(None, True, self._gain)
            return True

    def resume(self) -> bool:
        with self._lock:
            if not self.is_playing or not self._paused:
                return False
            self._paused = False
            self._streamer.update(None, False, self._gain)
            return True

    def toggle_pause(self) -> bool:
        """Toggle pause/resume. Returns True if now paused, False otherwise."""
        with self._lock:
            if self._paused:
                self.resume()
                return False
            self.pause()
            return self._paused

    def seek(self, offset_ms: int) -> bool:
        with self._lock:
            if not self.is_playing:
                return False
            return bool(self._streamer.update(max(0, offset_ms), self._paused,
                                             self._gain))

    def set_volume(self, percent: float) -> int:
        """Set volume (percent). Returns the clamped percent actually set."""
        with self._lock:
            percent = max(0, min(MAX_VOLUME_PERCENT, percent))
            self._gain = percent_to_gain(percent)
            if self.is_playing:
                self._streamer.update(None, self._paused, self._gain)
            return gain_to_percent(self._gain)

    # ---- mode setters ---------------------------------------------------

    def set_loop(self, mode: LoopMode) -> None:
        with self._lock:
            self._loop = LoopMode(mode)

    def set_shuffle(self, on: bool) -> None:
        with self._lock:
            self._shuffle = bool(on)

    def set_autoplay(self, on: bool) -> None:
        with self._lock:
            self._autoplay = bool(on)

    # ---- event from the bot --------------------------------------------

    def on_stream_finished(self, success: bool = True) -> None:
        """Called when the SDK reports the current stream ended or errored."""
        with self._lock:
            finished = self._current
            if not success and finished is not None:
                self._announce(f"Playback error on: {finished.display()}")
            self._paused = False
            # Loop handling only applies to a track that finished cleanly.
            if success and finished is not None:
                if self._loop == LoopMode.TRACK:
                    self._queue.appendleft(finished)
                elif self._loop == LoopMode.QUEUE:
                    self._queue.append(finished)
            if finished is not None:
                self._history.append(finished)
            self._current = None
            self._play_next_locked()

    # ---- internals ------------------------------------------------------

    def _stop_stream(self) -> None:
        try:
            self._streamer.stop()
        except Exception as exc:  # noqa: BLE001
            log.debug("stop stream failed: %s", exc)

    def _pop_next(self) -> Optional[Track]:
        if not self._queue:
            return None
        if self._shuffle and len(self._queue) > 1:
            idx = random.randrange(len(self._queue))
            self._queue.rotate(-idx)
            track = self._queue.popleft()
            self._queue.rotate(idx)
            return track
        return self._queue.popleft()

    def _maybe_autoplay(self) -> None:
        # Fire-and-forget: the bot resolves related tracks off-thread and calls
        # enqueue() when ready, so we never block the SDK event loop here.
        if not (self._autoplay and self._autoplay_fn and self._history):
            return
        if self._autoplay_pending:
            return
        self._autoplay_pending = True
        try:
            self._autoplay_fn(self._history[-1])
        except Exception as exc:  # noqa: BLE001
            log.warning("autoplay trigger failed: %s", exc)
            self._autoplay_pending = False

    def recent_urls(self, n: int = 20) -> set:
        with self._lock:
            return {t.stream_url for t in self._history[-n:]}

    def clear_autoplay_pending(self) -> None:
        with self._lock:
            self._autoplay_pending = False

    def _play_next_locked(self) -> None:
        if not self._queue:
            self._maybe_autoplay()
        while self._queue:
            track = self._pop_next()
            if track is None:
                break
            ok = False
            try:
                ok = self._streamer.stream(track.stream_url, self._gain)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to start %s: %s", track.display(), exc)
            if ok:
                self._current = track
                self._paused = False
                if self._announce_now_playing:
                    self._announce(f"Now playing: {track.display()}")
                return
            self._announce(f"Skipping (couldn't play): {track.display()}")
        self._current = None
