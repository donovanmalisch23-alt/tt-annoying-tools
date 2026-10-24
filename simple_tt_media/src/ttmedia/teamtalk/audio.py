"""Audio engine: decode a media URL with ffmpeg and transmit it as voice.

Why this design
---------------
TeamTalk's ``InsertAudioBlock`` lets an application push raw PCM into the
voice stream, so we can play anything ffmpeg can decode *without* a virtual
audio cable (unlike mpv-based bots that need VB-Cable on Windows).

ffmpeg decodes the source to signed-16-bit PCM on stdout. A feeder thread
reads fixed-size frames, applies volume, and inserts them, pacing itself to
stay only ``lead_ms`` ahead of real time. That small lead is deliberate: the
SDK keeps playing whatever is already buffered after we stop, so a short lead
keeps ``stop``/``skip`` responsive (~lead_ms latency, measured ~320 ms).

Controls (all thread-safe): play, pause/resume, stop, seek, set_volume.
Pause works by not reading ffmpeg's stdout; the OS pipe fills and ffmpeg
blocks, which freezes the decode position until we resume.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
from array import array
from typing import Callable, Optional

from ..config import AudioConfig
from ..log import get_logger
from ..models import Track

log = get_logger(__name__)

try:  # optional acceleration for volume scaling
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def _scale_pcm(pcm: bytes, gain: float) -> bytes:
    """Scale signed-16-bit little-endian PCM by ``gain`` (0.0-...), clamped."""
    if gain == 1.0:
        return pcm
    if _np is not None:
        a = _np.frombuffer(pcm, dtype="<i2").astype(_np.float32) * gain
        _np.clip(a, -32768, 32767, out=a)
        return a.astype("<i2").tobytes()
    samples = array("h")
    samples.frombytes(pcm)
    for i, v in enumerate(samples):
        s = int(v * gain)
        samples[i] = -32768 if s < -32768 else 32767 if s > 32767 else s
    return samples.tobytes()


class AudioEngine:
    def __init__(self, client, cfg: AudioConfig,
                 on_finished: Callable[[Track, str], None]) -> None:
        self._client = client
        self._tt = client.tt
        self._cfg = cfg
        self._on_finished = on_finished

        self._sr = cfg.sample_rate
        self._ch = cfg.channels
        self._frame_samples = self._sr * cfg.frame_ms // 1000
        self._frame_bytes = self._frame_samples * self._ch * 2
        self._lead = cfg.lead_ms / 1000.0

        self._lock = threading.RLock()
        self._gen = 0
        self._proc: Optional[subprocess.Popen] = None
        self._feeder: Optional[threading.Thread] = None
        self._resume = threading.Event()
        self._resume.set()

        self._track: Optional[Track] = None
        self._paused = False
        self._stopped = True
        self._volume = max(0, cfg.default_volume) / 100.0
        self._seek_base = 0.0          # seconds the current ffmpeg was seeked to
        self._frames_sent = 0
        self._stream_id = 0
        self._buffers: list = []       # keep ctypes buffers alive while queued
        self._ffmpeg_override = ""
        self.last_error = ""
        self._input_active = False     # have we inserted audio since the last end?

    # ---- public state ---------------------------------------------------

    @property
    def is_playing(self) -> bool:
        return not self._stopped

    @property
    def is_paused(self) -> bool:
        return self._paused

    @property
    def track(self) -> Optional[Track]:
        return self._track

    @property
    def volume_percent(self) -> int:
        return round(self._volume * 100)

    def elapsed(self) -> float:
        return self._seek_base + self._frames_sent * self._cfg.frame_ms / 1000.0

    def ffmpeg_path(self) -> str:
        return self._ffmpeg

    # ---- controls -------------------------------------------------------

    def play(self, track: Track) -> None:
        with self._lock:
            self._stop_locked()
            self._begin_locked(track, 0.0, new_stream=True)

    def _begin_locked(self, track: Track, start_sec: float,
                      new_stream: bool) -> None:
        # Caller must hold the lock and have already stopped+joined any feeder.
        self._track = track
        self._paused = False
        self._stopped = False
        self._seek_base = start_sec
        self._frames_sent = 0
        self._resume.set()
        self._gen += 1
        if new_stream:
            self._stream_id = (self._stream_id % 2_000_000) + 1
        gen = self._gen
        self._proc = self._spawn_ffmpeg(track, start_sec)
        self._feeder = threading.Thread(
            target=self._feed, args=(gen,), daemon=True,
            name=f"audio-feed-{gen}")
        self._feeder.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._track = None

    def pause(self) -> bool:
        with self._lock:
            if self._stopped or self._paused:
                return False
            self._paused = True
            self._resume.clear()
            return True

    def resume(self) -> bool:
        with self._lock:
            if self._stopped or not self._paused:
                return False
            self._paused = False
            self._resume.set()
            return True

    def set_volume(self, percent: float) -> int:
        percent = max(0, min(self._cfg.max_volume, percent))
        self._volume = percent / 100.0
        return round(percent)

    def seek(self, seconds: float) -> bool:
        with self._lock:
            if self._stopped or self._track is None or self._track.is_live:
                return False
            track = self._track
            seconds = max(0.0, seconds)
            if track.duration and seconds >= track.duration:
                seconds = max(0.0, track.duration - 1.0)
            # Fully stop+join the current feeder before restarting ffmpeg, so
            # only one feeder ever touches the shared buffers at a time.
            self._stop_locked()
            self._begin_locked(track, seconds, new_stream=False)
            return True

    def restart_for_channel_change(self) -> bool:
        """Re-init the voice stream after the bot is moved to a new channel.

        The SDK configures its audio-input re-chunker for the channel's codec
        when the stream starts; a move changes the codec frame size, so we end
        the current input and begin a fresh stream (new stream id) at the same
        position. Returns True if playback was restarted.
        """
        with self._lock:
            if self._stopped or self._track is None:
                return False
            track = self._track
            pos = 0.0 if track.is_live else self.elapsed()
            self._stop_locked()
            self._begin_locked(track, pos, new_stream=True)
            return True

    def shutdown(self) -> None:
        self.stop()

    # ---- internals ------------------------------------------------------

    def _stop_locked(self) -> None:
        self._gen += 1
        self._stopped = True
        self._paused = False
        self._resume.set()  # let a paused feeder wake up and exit
        self._kill_proc()
        # Only join if we're not being called from the feeder thread itself.
        feeder = self._feeder
        if feeder is not None and feeder is not threading.current_thread():
            feeder.join(timeout=2.0)
        self._feeder = None
        self._end_input()
        self._buffers.clear()

    def _end_input(self) -> None:
        # Only send the NULL "end of input" when a voice stream is actually
        # active; calling it otherwise trips a harmless native assertion.
        if self._input_active:
            self._input_active = False
            self._client.end_audio_input()

    def _kill_proc(self) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass
        # Drain the pipe so a blocked ffmpeg can exit, in the background.
        threading.Thread(target=_drain, args=(proc,), daemon=True).start()

    @property
    def _ffmpeg(self) -> str:
        return self._ffmpeg_override or shutil.which("ffmpeg") or "ffmpeg"

    def set_ffmpeg_path(self, path: str) -> None:
        self._ffmpeg_override = path or ""

    def _spawn_ffmpeg(self, track: Track, start_sec: float) -> subprocess.Popen:
        url = track.playback_url
        cmd = [self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        if start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        if track.http_headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in track.http_headers.items())
            cmd += ["-headers", hdr]
        if url.lower().startswith(("http://", "https://")):
            cmd += ["-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"]
        cmd += ["-i", url, "-vn", "-f", "s16le",
                "-ac", str(self._ch), "-ar", str(self._sr), "pipe:1"]
        log.debug("ffmpeg: %s", " ".join(cmd[:6]) + " ... " + url[:80])
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)  # no console popups
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                bufsize=0, creationflags=creationflags)
        proc._tail = b""  # type: ignore[attr-defined]
        # Always drain stderr: an unread pipe fills up (64 KB) and blocks ffmpeg.
        threading.Thread(target=_stderr_tail, args=(proc,), daemon=True).start()
        return proc

    def _make_block(self, pcm: bytes):
        tt = self._tt
        n_samples = len(pcm) // (self._ch * 2)
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        self._buffers.append(buf)
        if len(self._buffers) > 64:
            self._buffers.pop(0)
        ab = tt.AudioBlock()
        ab.nStreamID = self._stream_id
        ab.nSampleRate = self._sr
        ab.nChannels = self._ch
        ab.lpRawAudio = ctypes.cast(buf, ctypes.c_void_p)
        ab.nSamples = n_samples
        ab.uStreamTypes = tt.StreamType.STREAMTYPE_VOICE
        return ab

    def _feed(self, gen: int) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        start = time.monotonic()
        pause_accum = 0.0
        reason = "finished"
        frames_this_run = 0
        try:
            while True:
                if self._gen != gen or self._stopped:
                    return
                if not self._resume.is_set():
                    pstart = time.monotonic()
                    self._resume.wait()
                    pause_accum += time.monotonic() - pstart
                    if self._gen != gen or self._stopped:
                        return
                pcm = _read_exact(proc.stdout, self._frame_bytes)
                if self._gen != gen or self._stopped:
                    return
                if len(pcm) < self._frame_bytes:
                    if pcm:
                        pcm += b"\x00" * (self._frame_bytes - len(pcm))
                        self._client.insert_audio_block(
                            self._make_block(_scale_pcm(pcm, self._volume)))
                        self._input_active = True
                        frames_this_run += 1
                    reason = self._eof_reason(proc, frames_this_run)
                    break
                block = self._make_block(_scale_pcm(pcm, self._volume))
                if not self._client.insert_audio_block(block):
                    # Queue full: wait one frame and retry once, never drop.
                    time.sleep(self._cfg.frame_ms / 1000.0)
                    self._client.insert_audio_block(block)
                self._input_active = True
                self._frames_sent += 1
                frames_this_run += 1
                target = start + pause_accum + \
                    self._frames_sent * self._cfg.frame_ms / 1000.0
                sleep_for = (target - self._lead) - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except Exception:  # noqa: BLE001
            log.exception("audio feeder crashed")
            reason = "error"
        finally:
            if self._gen == gen and not self._stopped:
                self._finish(gen, reason)

    def _eof_reason(self, proc: subprocess.Popen, frames: int) -> str:
        """Decide whether EOF was a natural end or an ffmpeg failure."""
        try:
            rc = proc.wait(timeout=3)
        except Exception:  # noqa: BLE001
            rc = None
        time.sleep(0.05)  # let the stderr reader catch the final lines
        err = getattr(proc, "_tail", b"")
        self.last_error = err.decode("utf-8", "replace").strip()
        if rc not in (0, None) and frames < 25:  # failed before ~0.5 s of audio
            log.warning("ffmpeg failed (rc=%s): %s", rc, self.last_error[:300])
            return "error"
        if rc not in (0, None):
            log.info("ffmpeg ended with rc=%s after %d frames: %s",
                     rc, frames, self.last_error[:200])
        return "finished"

    def _finish(self, gen: int, reason: str) -> None:
        with self._lock:
            if self._gen != gen or self._stopped:
                return
            self._stopped = True
            track = self._track
            self._kill_proc()
            self._end_input()
        if track is None:
            return

        # Deliver on a fresh thread so this feeder exits immediately. Otherwise a
        # caller that holds the player lock and calls stop() would wait on our
        # join while we wait on its lock.
        def deliver() -> None:
            try:
                self._on_finished(track, reason)
            except Exception:  # noqa: BLE001
                log.exception("on_finished handler failed")

        threading.Thread(target=deliver, daemon=True, name="track-finished").start()


def _read_exact(stream, n: int) -> bytes:
    """Read exactly n bytes unless EOF; returns short/empty bytes at EOF."""
    chunks = []
    got = 0
    while got < n:
        b = stream.read(n - got)
        if not b:
            break
        chunks.append(b)
        got += len(b)
    return b"".join(chunks)


def _stderr_tail(proc: subprocess.Popen, keep: int = 4000) -> None:
    """Continuously read ffmpeg's stderr, keeping only the last ``keep`` bytes."""
    try:
        while proc.stderr:
            chunk = proc.stderr.read(1024)
            if not chunk:
                break
            proc._tail = (proc._tail + chunk)[-keep:]  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _drain(proc: subprocess.Popen) -> None:
    try:
        if proc.stdout:
            while proc.stdout.read(65536):
                pass
    except Exception:  # noqa: BLE001
        pass
    try:
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
