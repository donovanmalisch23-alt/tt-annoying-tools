"""Playback queue and state machine.

The player owns the queue and the notion of "what's playing now". It talks to
the TeamTalk server through a tiny :class:`Streamer` protocol (implemented by
the bot) and announces changes through a callback. Keeping those two as
injected dependencies means the whole queue/skip/stop logic is unit-testable
without a live server or the SDK.
"""

from __future__ import annotations

import threading
from collections import deque
from typing import Callable, Deque, List, Optional, Protocol

from .log import get_logger
from .models import Track

log = get_logger(__name__)


class Streamer(Protocol):
    """What the player needs from the transport layer (the bot)."""

    def stream(self, url: str) -> bool: ...   # start streaming, return success
    def stop(self) -> None: ...               # stop any current stream


class MediaPlayer:
    def __init__(
        self,
        streamer: Streamer,
        announce: Callable[[str], None],
        max_queue: int = 200,
        announce_now_playing: bool = True,
    ) -> None:
        self._streamer = streamer
        self._announce = announce
        self._max_queue = max_queue
        self._announce_now_playing = announce_now_playing
        self._queue: Deque[Track] = deque()
        self._current: Optional[Track] = None
        self._lock = threading.RLock()

    # ---- queries --------------------------------------------------------

    @property
    def current(self) -> Optional[Track]:
        return self._current

    @property
    def is_playing(self) -> bool:
        return self._current is not None

    def queue_snapshot(self) -> List[Track]:
        with self._lock:
            return list(self._queue)

    # ---- mutations ------------------------------------------------------

    def enqueue(self, tracks: List[Track]) -> int:
        """Add tracks; start playback if idle. Returns queued position (1-based)
        of the first added track, or 0 if it started playing immediately."""
        with self._lock:
            if len(self._queue) + len(tracks) > self._max_queue:
                raise ValueError(f"Queue is full (max {self._max_queue}).")
            started_immediately = not self.is_playing and not self._queue
            self._queue.extend(tracks)
            if started_immediately:
                self._play_next_locked()
                return 0
            return len(self._queue) - len(tracks) + 1

    def skip(self) -> Optional[Track]:
        """Skip the current track; returns the track that starts next."""
        with self._lock:
            self._streamer.stop()
            self._current = None
            self._play_next_locked()
            return self._current

    def stop(self) -> None:
        """Stop playback and clear everything."""
        with self._lock:
            self._streamer.stop()
            self._current = None
            self._queue.clear()

    def clear(self) -> int:
        """Clear the pending queue but keep the current track playing."""
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
            return n

    def on_stream_finished(self, success: bool = True) -> None:
        """Called by the bot when the SDK reports a stream ended or errored."""
        with self._lock:
            if not success and self._current is not None:
                self._announce(f"Playback error on: {self._current.display()}")
            self._current = None
            self._play_next_locked()

    # ---- internals ------------------------------------------------------

    def _play_next_locked(self) -> None:
        while self._queue:
            track = self._queue.popleft()
            ok = False
            try:
                ok = self._streamer.stream(track.stream_url)
            except Exception as exc:  # noqa: BLE001
                log.warning("Failed to start %s: %s", track.display(), exc)
            if ok:
                self._current = track
                if self._announce_now_playing:
                    self._announce(f"Now playing: {track.display()}")
                return
            self._announce(f"Skipping (couldn't play): {track.display()}")
        self._current = None
