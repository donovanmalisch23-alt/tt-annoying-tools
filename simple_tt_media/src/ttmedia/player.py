"""Playback queue and policy.

The player owns the queue, history, and the repeat/shuffle policy, and drives
the :class:`AudioEngine`. It never touches the SDK or the network directly:
  * playback goes through the engine (injected);
  * lazy resolution of metadata-only tracks goes through ``resolve_fn``
    (injected), called just before a track plays so a long queue never pins
    thousands of soon-to-expire stream URLs;
  * "now playing" announcements go through ``announce`` (injected).

The engine calls :meth:`on_track_finished` from its feeder thread when a track
ends; the player advances according to the repeat mode.
"""

from __future__ import annotations

import random
import threading
from typing import Callable, List, Optional

from .log import get_logger
from .models import RepeatMode, Track

log = get_logger(__name__)

# resolve_fn(track) -> True if the track now has a playback_url, else False.
ResolveFn = Callable[[Track], bool]


class Player:
    def __init__(self, engine, announce: Callable[[str], None],
                 resolve_fn: ResolveFn, max_queue: int = 1000) -> None:
        self._engine = engine
        self._announce = announce
        self._resolve = resolve_fn
        self._max_queue = max_queue

        self._lock = threading.RLock()
        self._queue: List[Track] = []
        self._history: List[Track] = []
        self._current: Optional[Track] = None
        self._repeat = RepeatMode.OFF
        self._shuffle = False

    # ---- state ----------------------------------------------------------

    @property
    def current(self) -> Optional[Track]:
        return self._current

    @property
    def repeat(self) -> RepeatMode:
        return self._repeat

    @property
    def shuffle(self) -> bool:
        return self._shuffle

    @property
    def is_playing(self) -> bool:
        return self._engine.is_playing

    @property
    def is_paused(self) -> bool:
        return self._engine.is_paused

    @property
    def volume(self) -> int:
        return self._engine.volume_percent

    def elapsed(self) -> float:
        return self._engine.elapsed()

    def queue_snapshot(self) -> List[Track]:
        with self._lock:
            return list(self._queue)

    def history_snapshot(self) -> List[Track]:
        with self._lock:
            return list(self._history)

    def modes_text(self) -> str:
        return (f"volume {self.volume}% | repeat {self._repeat.value} | "
                f"shuffle {'on' if self._shuffle else 'off'}"
                + (" | paused" if self.is_paused else ""))

    # ---- enqueue / play -------------------------------------------------

    def enqueue(self, tracks: List[Track]) -> int:
        """Add tracks, starting playback if idle.

        Returns the 1-based queue position of the first added track, or 0 if it
        began playing immediately.
        """
        with self._lock:
            if len(self._queue) + len(tracks) > self._max_queue:
                room = self._max_queue - len(self._queue)
                raise ValueError(f"Queue is full (room for {max(0, room)} more).")
            start_now = self._current is None and not self._queue
            self._queue.extend(tracks)
            if start_now:
                self._play_next_locked()
                return 0
            return len(self._queue) - len(tracks) + 1

    def play_now(self, tracks: List[Track]) -> None:
        """Replace playback with these tracks immediately."""
        with self._lock:
            self._queue[:0] = tracks
            self._engine.stop()
            if self._current is not None:
                self._history.append(self._current)
                self._current = None
            self._play_next_locked()

    def enqueue_front(self, tracks: List[Track]) -> int:
        """Add tracks to the front of the queue (play next), starting if idle.

        Returns 0 if playback started immediately, else 1 (queued next).
        """
        with self._lock:
            if len(self._queue) + len(tracks) > self._max_queue:
                room = self._max_queue - len(self._queue)
                raise ValueError(f"Queue is full (room for {max(0, room)} more).")
            start_now = self._current is None and not self._queue
            self._queue[:0] = tracks
            if start_now:
                self._play_next_locked()
                return 0
            return 1

    # ---- transport ------------------------------------------------------

    def skip(self, count: int = 1) -> Optional[Track]:
        with self._lock:
            self._engine.stop()
            if self._current is not None:
                self._history.append(self._current)
                self._current = None
            # Drop (count - 1) further tracks from the queue before the next.
            for _ in range(max(0, count - 1)):
                nxt = self._pop_next_locked()
                if nxt is None:
                    break
                self._history.append(nxt)
            self._play_next_locked()
            return self._current

    def remove(self, index_1based: int) -> Optional[Track]:
        with self._lock:
            i = index_1based - 1
            if i < 0 or i >= len(self._queue):
                return None
            return self._queue.pop(i)

    def restart(self) -> bool:
        return self._engine.seek(0.0)

    def previous(self) -> Optional[Track]:
        with self._lock:
            if not self._history:
                return None
            if self._current is not None:
                self._queue.insert(0, self._current)
            prev = self._history.pop()
            self._queue.insert(0, prev)
            self._engine.stop()
            self._current = None
            self._play_next_locked()
            return self._current

    def stop(self) -> None:
        with self._lock:
            self._engine.stop()
            if self._current is not None:
                self._history.append(self._current)
            self._current = None
            self._queue.clear()

    def clear(self) -> int:
        with self._lock:
            n = len(self._queue)
            self._queue.clear()
            return n

    def pause(self) -> bool:
        return self._engine.pause()

    def resume(self) -> bool:
        return self._engine.resume()

    def toggle_pause(self) -> bool:
        if self._engine.is_paused:
            self._engine.resume()
            return False
        return self._engine.pause()

    def seek_to(self, seconds: float) -> bool:
        return self._engine.seek(seconds)

    def seek_relative(self, delta: float) -> bool:
        return self._engine.seek(max(0.0, self._engine.elapsed() + delta))

    def set_volume(self, percent: float) -> int:
        return self._engine.set_volume(percent)

    def set_repeat(self, mode: RepeatMode) -> None:
        with self._lock:
            self._repeat = RepeatMode(mode)

    def set_shuffle(self, on: bool) -> None:
        with self._lock:
            self._shuffle = bool(on)

    def shuffle_queue(self) -> None:
        with self._lock:
            random.shuffle(self._queue)

    def select(self, index_1based: int) -> Optional[Track]:
        """Jump to a track in the current queue by 1-based position."""
        with self._lock:
            if not self._queue:
                return None
            i = index_1based - 1
            if i < 0 or i >= len(self._queue):
                raise IndexError("out of range")
            del self._queue[:i]  # drop everything before the chosen track
            self._engine.stop()
            if self._current is not None:
                self._history.append(self._current)
                self._current = None
            self._play_next_locked()
            return self._current

    # ---- engine callback ------------------------------------------------

    def on_track_finished(self, track: Track, reason: str) -> None:
        with self._lock:
            if self._current is not track:
                # stop/skip/another play already handled this track; a late
                # callback must not touch history or start anything.
                return
            self._current = None
            if reason == "error":
                # Most often an expired/blocked stream URL: re-resolve once.
                if track.query and track.retries < 1:
                    retry = _replayable(track, force=True)
                    retry.retries = track.retries + 1
                    self._queue.insert(0, retry)
                    log.info("Retrying %s after a stream error", track.label())
                else:
                    self._announce(f"Couldn't play: {track.label()}. Skipping.")
                    self._history.append(track)
                self._play_next_locked()
                return
            if self._repeat == RepeatMode.TRACK:
                self._queue.insert(0, _replayable(track))
            elif self._repeat == RepeatMode.QUEUE:
                self._queue.append(_replayable(track))
            self._history.append(track)
            del self._history[:-200]  # bound memory on long sessions
            self._play_next_locked()

    # ---- internals ------------------------------------------------------

    def _pop_next_locked(self) -> Optional[Track]:
        if not self._queue:
            return None
        if self._shuffle and len(self._queue) > 1:
            return self._queue.pop(random.randrange(len(self._queue)))
        return self._queue.pop(0)

    def _play_next_locked(self) -> None:
        while self._queue:
            track = self._pop_next_locked()
            if track is None:
                break
            if track.needs_resolve or not track.playback_url:
                try:
                    ok = self._resolve(track)
                except Exception as exc:  # noqa: BLE001
                    log.warning("resolve failed for %s: %s", track.label(), exc)
                    ok = False
                if not ok:
                    self._announce(f"Skipping (couldn't load): {track.label()}")
                    continue
            try:
                self._engine.play(track)
            except Exception as exc:  # noqa: BLE001
                log.warning("engine.play failed for %s: %s", track.label(), exc)
                self._announce(f"Skipping (playback error): {track.label()}")
                continue
            self._current = track
            self._announce(f"Now playing: {track.label()}")
            self._prefetch_next_locked()
            return
        self._current = None

    def _prefetch_next_locked(self) -> None:
        """Resolve the upcoming track in the background for a quick hand-off."""
        if self._shuffle or not self._queue:
            return
        nxt = self._queue[0]
        if not (nxt.needs_resolve or not nxt.playback_url):
            return

        def work() -> None:
            try:
                self._resolve(nxt)
            except Exception:  # noqa: BLE001
                pass  # it will be retried (and reported) when it's due to play

        threading.Thread(target=work, daemon=True, name="prefetch").start()


_RESOLVABLE = ("youtube", "soundcloud", "spotify", "applemusic")


def _replayable(track: Track, force: bool = False) -> Track:
    """A copy safe to re-queue; drop a service URL that may have expired."""
    import copy
    t = copy.copy(track)
    t.http_headers = dict(track.http_headers)
    if t.query and (force or t.source in _RESOLVABLE):
        t.needs_resolve = True
        t.playback_url = ""
        t.http_headers = {}
    return t
