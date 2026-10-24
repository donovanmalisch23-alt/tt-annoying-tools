"""Audio engine: decode a media URL with ffmpeg and transmit it as voice.

Why this design
---------------
TeamTalk's ``InsertAudioBlock`` lets an application push raw PCM into the voice
stream, so we can play anything ffmpeg can decode **without a virtual audio
cable** (unlike mpv-based bots that need VB-Cable on Windows).

For smooth, stutter-free playback two threads run per track:

* a **reader** pulls decoded PCM from ffmpeg as fast as it arrives into a
  bounded in-process buffer (``buffer_ms``). This buffer absorbs network and
  CPU jitter, so the sender rarely runs dry.
* a **sender** takes one frame at a time from that buffer, applies volume, and
  inserts it, pacing itself to stay only ``lead_ms`` ahead of real time.

That small send-lead keeps ``stop``/``skip`` responsive (the SDK only has
~``lead_ms`` buffered when we cut), while the large read-ahead keeps audio from
stuttering. Pausing stops the sender (the reader simply fills the buffer and
waits), which freezes the position; stop/skip/seek clear the buffer and kill
ffmpeg for near-instant silence.

Security: ffmpeg is launched with an explicit protocol whitelist and an I/O
timeout so a malicious or dead URL can neither reach unexpected protocols nor
hang the engine.
"""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import threading
import time
from array import array
from collections import deque
from typing import Callable, Optional

from ..config import AudioConfig
from ..log import get_logger
from ..models import Track

log = get_logger(__name__)

# Protocols ffmpeg may use for an input. Deliberately excludes concat/subfile/
# ffconcat and similar tricks that could read arbitrary local files.
_PROTOCOLS = "file,crypto,data,http,https,tcp,tls,httpproxy,hls,fragment,rtp,udp"
_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")

try:  # optional acceleration for volume scaling
    import numpy as _np
except Exception:  # noqa: BLE001
    _np = None


def _scale_pcm(pcm: bytes, gain: float) -> bytes:
    """Scale signed-16-bit little-endian PCM by ``gain`` (>= 0), clamped."""
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
        self._frame_samples = max(1, self._sr * cfg.frame_ms // 1000)
        self._frame_bytes = self._frame_samples * self._ch * 2
        self._lead = max(0.05, cfg.lead_ms / 1000.0)
        self._buffer_cap = max(self._frame_bytes * 4,
                               self._sr * self._ch * 2 * cfg.buffer_ms // 1000)

        self._lock = threading.RLock()
        self._gen = 0
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._sender: Optional[threading.Thread] = None

        # Shared PCM buffer between reader and sender, guarded by a condition.
        self._buf = bytearray()
        self._eof = False
        self._cond = threading.Condition()
        self._resume = threading.Event()
        self._resume.set()

        self._track: Optional[Track] = None
        self._paused = False
        self._stopped = True
        self._volume = max(0, cfg.default_volume) / 100.0
        self._seek_base = 0.0
        self._frames_sent = 0
        self._stream_id = 0
        self._blocks: deque = deque(maxlen=256)   # keep ctypes buffers alive
        self._ffmpeg_override = ""
        self.last_error = ""
        self._input_active = False

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

    def set_ffmpeg_path(self, path: str) -> None:
        self._ffmpeg_override = path or ""

    @property
    def _ffmpeg(self) -> str:
        return self._ffmpeg_override or shutil.which("ffmpeg") or "ffmpeg"

    # ---- controls -------------------------------------------------------

    def play(self, track: Track) -> None:
        with self._lock:
            self._stop_locked()
            self._begin_locked(track, 0.0, new_stream=True)

    def seek(self, seconds: float) -> bool:
        with self._lock:
            if self._stopped or self._track is None or self._track.is_live:
                return False
            track = self._track
            seconds = max(0.0, seconds)
            if track.duration and seconds >= track.duration:
                seconds = max(0.0, track.duration - 1.0)
            self._stop_locked()
            self._begin_locked(track, seconds, new_stream=False)
            return True

    def stop(self) -> None:
        with self._lock:
            self._stop_locked()
            self._track = None

    def shutdown(self) -> None:
        self.stop()

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

    # ---- lifecycle internals -------------------------------------------

    def _begin_locked(self, track: Track, start_sec: float, new_stream: bool) -> None:
        self._track = track
        self._paused = False
        self._stopped = False
        self._seek_base = start_sec
        self._frames_sent = 0
        self._resume.set()
        self._eof = False
        self._buf = bytearray()
        self._gen += 1
        if new_stream:
            self._stream_id = (self._stream_id % 2_000_000) + 1
        gen = self._gen
        self._proc = self._spawn_ffmpeg(track, start_sec)
        self._reader = threading.Thread(target=self._read_loop, args=(gen,),
                                        daemon=True, name=f"audio-read-{gen}")
        self._sender = threading.Thread(target=self._send_loop, args=(gen,),
                                        daemon=True, name=f"audio-send-{gen}")
        self._reader.start()
        self._sender.start()

    def _stop_locked(self) -> None:
        self._gen += 1
        self._stopped = True
        self._paused = False
        self._resume.set()
        with self._cond:
            self._buf = bytearray()
            self._eof = True
            self._cond.notify_all()
        self._kill_proc()
        me = threading.current_thread()
        for t in (self._reader, self._sender):
            if t is not None and t is not me:
                t.join(timeout=2.0)
        self._reader = self._sender = None
        self._end_input()
        self._blocks.clear()

    def _end_input(self) -> None:
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
        threading.Thread(target=_reap, args=(proc,), daemon=True).start()

    def _spawn_ffmpeg(self, track: Track, start_sec: float) -> subprocess.Popen:
        url = track.playback_url
        is_http = url.lower().startswith(("http://", "https://"))
        cmd = [self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
               "-protocol_whitelist", _PROTOCOLS]
        if is_http:
            cmd += ["-user_agent", _USER_AGENT,
                    "-rw_timeout", str(self._cfg.network_timeout * 1_000_000),
                    "-reconnect", "1", "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5"]
        if start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        if track.http_headers:
            hdr = "".join(f"{k}: {v}\r\n" for k, v in track.http_headers.items())
            cmd += ["-headers", hdr]
        cmd += ["-i", url, "-vn", "-sn", "-dn",
                "-f", "s16le", "-acodec", "pcm_s16le",
                "-ac", str(self._ch), "-ar", str(self._sr), "pipe:1"]
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                bufsize=0, creationflags=creationflags)
        proc._tail = b""  # type: ignore[attr-defined]
        threading.Thread(target=_stderr_tail, args=(proc,), daemon=True).start()
        return proc

    # ---- reader: ffmpeg stdout -> buffer --------------------------------

    def _read_loop(self, gen: int) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                if self._gen != gen or self._stopped:
                    return
                chunk = proc.stdout.read(16384)
                if not chunk:
                    break
                with self._cond:
                    while (len(self._buf) >= self._buffer_cap
                           and self._gen == gen and not self._stopped):
                        self._cond.wait(0.2)
                    if self._gen != gen or self._stopped:
                        return
                    self._buf += chunk
                    self._cond.notify_all()
        except Exception:  # noqa: BLE001
            log.debug("reader stopped", exc_info=True)
        finally:
            with self._cond:
                if self._gen == gen:
                    self._eof = True
                    self._cond.notify_all()

    # ---- sender: buffer -> InsertAudioBlock, paced ----------------------

    def _send_loop(self, gen: int) -> None:
        start = time.monotonic()
        pause_accum = 0.0
        reason = "finished"
        try:
            while True:
                if self._gen != gen or self._stopped:
                    return
                if not self._resume.is_set():
                    p0 = time.monotonic()
                    self._resume.wait()
                    pause_accum += time.monotonic() - p0
                    if self._gen != gen or self._stopped:
                        return
                frame = self._take_frame(gen)
                if frame is None:            # clean end of stream
                    reason = self._eof_reason()
                    break
                if frame == b"":             # transient underrun; keep waiting
                    continue
                block = self._make_block(_scale_pcm(frame, self._volume))
                if not self._client.insert_audio_block(block):
                    time.sleep(self._cfg.frame_ms / 1000.0)
                    self._client.insert_audio_block(block)
                self._input_active = True
                self._frames_sent += 1
                target = start + pause_accum + \
                    self._frames_sent * self._cfg.frame_ms / 1000.0
                sleep_for = (target - self._lead) - time.monotonic()
                if sleep_for > 0:
                    time.sleep(sleep_for)
        except Exception:  # noqa: BLE001
            log.exception("sender crashed")
            reason = "error"
        finally:
            if self._gen == gen and not self._stopped:
                self._finish(gen, reason)

    def _take_frame(self, gen: int) -> Optional[bytes]:
        """Return a frame, b"" on a transient underrun, or None at end."""
        with self._cond:
            while len(self._buf) < self._frame_bytes and not self._eof:
                if self._gen != gen or self._stopped:
                    return None
                self._cond.wait(0.2)
                if self._gen != gen or self._stopped:
                    return None
            if len(self._buf) >= self._frame_bytes:
                frame = bytes(self._buf[:self._frame_bytes])
                del self._buf[:self._frame_bytes]
                self._cond.notify_all()
                return frame
            if self._eof:
                if self._buf:                # final short frame -> pad
                    frame = bytes(self._buf) + b"\x00" * (self._frame_bytes - len(self._buf))
                    self._buf = bytearray()
                    self._cond.notify_all()
                    return frame
                return None
            return b""

    def _make_block(self, pcm: bytes):
        tt = self._tt
        buf = ctypes.create_string_buffer(pcm, len(pcm))
        self._blocks.append(buf)
        ab = tt.AudioBlock()
        ab.nStreamID = self._stream_id
        ab.nSampleRate = self._sr
        ab.nChannels = self._ch
        ab.lpRawAudio = ctypes.cast(buf, ctypes.c_void_p)
        ab.nSamples = len(pcm) // (self._ch * 2)
        ab.uStreamTypes = tt.StreamType.STREAMTYPE_VOICE
        return ab

    def _eof_reason(self) -> str:
        proc = self._proc
        rc = None
        if proc is not None:
            try:
                rc = proc.wait(timeout=3)
            except Exception:  # noqa: BLE001
                rc = None
        time.sleep(0.05)
        self.last_error = getattr(proc, "_tail", b"").decode("utf-8", "replace").strip()
        if rc not in (0, None) and self._frames_sent < 25:
            log.warning("ffmpeg failed (rc=%s): %s", rc, self.last_error[:300])
            return "error"
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

        def deliver() -> None:
            try:
                self._on_finished(track, reason)
            except Exception:  # noqa: BLE001
                log.exception("on_finished handler failed")

        threading.Thread(target=deliver, daemon=True, name="track-finished").start()


def _stderr_tail(proc: subprocess.Popen, keep: int = 4000) -> None:
    try:
        while proc.stderr:
            chunk = proc.stderr.read(1024)
            if not chunk:
                break
            proc._tail = (proc._tail + chunk)[-keep:]  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass


def _reap(proc: subprocess.Popen) -> None:
    try:
        if proc.stdout:
            try:
                proc.stdout.close()
            except Exception:  # noqa: BLE001
                pass
        proc.wait(timeout=5)
    except Exception:  # noqa: BLE001
        pass
