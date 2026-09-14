"""Runs the CLI tools as child processes and streams their output.

One run at a time. Each tool is a real ``python3 tt_*.py`` process, so it
behaves exactly like the command line — including the SDK's own crash-prone
native library, which is a good reason to keep it out of the bridge's process.

Output is buffered per run (newest ``log_lines`` entries kept) and broadcast to
every connected page, so several tabs can watch the same run.
"""

from __future__ import annotations

import os
import queue
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

from .config import BridgeConfig
from .tools import LiveTool, redact, script_name, target_host

#: Run states.
RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
STOPPED = "stopped"

SUBSCRIBER_QUEUE_LINES = 500


class RunBusyError(RuntimeError):
    """Raised when a run is already in progress."""


class RunNotFoundError(KeyError):
    """Raised for an unknown run id."""


@dataclass
class RunRecord:
    id: str
    tool_id: str
    label: str
    argv: list[str]
    display_argv: list[str]
    host: str
    script: str
    state: str = RUNNING
    pid: int | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    note: str = ""
    stop_reason: str | None = None
    lines: list[dict[str, Any]] = field(default_factory=list)
    next_index: int = 1

    def append(self, text: str, *, keep: int) -> dict[str, Any]:
        entry = {"n": self.next_index, "time": time.time(), "text": text}
        self.next_index += 1
        self.lines.append(entry)
        if len(self.lines) > keep:
            del self.lines[: len(self.lines) - keep]
        return entry

    @property
    def running(self) -> bool:
        return self.state == RUNNING

    def summary(self, *, include_lines: bool = False, since: int = 0) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "tool": self.tool_id,
            "label": self.label,
            "script": self.script,
            "state": self.state,
            "pid": self.pid,
            "host": self.host,
            "argv": self.display_argv,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "exit_code": self.exit_code,
            "error": self.error,
            "note": self.note,
            "stop_reason": self.stop_reason,
            "line_count": self.next_index - 1,
        }
        if include_lines:
            payload["lines"] = [line for line in self.lines if line["n"] > since]
        return payload


class RunManager:
    def __init__(self, config: BridgeConfig):
        self.config = config
        self.current: RunRecord | None = None
        self.last: RunRecord | None = None
        self._lock = threading.RLock()
        self._subscribers: set[queue.Queue] = set()
        self._counter = 0

    # ----- subscribers ----------------------------------------------------- #

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue(maxsize=SUBSCRIBER_QUEUE_LINES)
        with self._lock:
            self._subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        with self._lock:
            self._subscribers.discard(channel)

    def _broadcast(self, payload: dict[str, Any]) -> None:
        with self._lock:
            channels = list(self._subscribers)
        for channel in channels:
            try:
                channel.put_nowait(payload)
            except queue.Full:
                # A stalled reader: drop the oldest event rather than block.
                try:
                    channel.get_nowait()
                    channel.put_nowait(payload)
                except (queue.Empty, queue.Full):
                    pass

    # ----- lifecycle ------------------------------------------------------- #

    def start(self, tool: LiveTool, argv: list[str], env_overrides: dict[str, str], *, note: str = "") -> RunRecord:
        with self._lock:
            if self.current is not None and self.current.running:
                raise RunBusyError(
                    f"{self.current.label} is already running; stop it before starting another tool."
                )
            self._counter += 1
            run = RunRecord(
                id=f"run-{self._counter}",
                tool_id=tool.id,
                label=tool.label,
                argv=list(argv),
                display_argv=redact(argv),
                host=target_host(argv),
                script=script_name(argv),
                note=note or tool.note,
            )
            self.current = run

        env = os.environ.copy()
        env.update(env_overrides)
        try:
            process = subprocess.Popen(  # noqa: S603 - deliberate: runs the repo's own tools
                argv,
                cwd=str(self.config.repo_root),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                env=env,
                start_new_session=True,
            )
        except OSError as exc:
            run.state = FAILED
            run.error = f"could not start {tool.script}: {exc}"
            run.finished_at = time.time()
            with self._lock:
                self.current = None
                self.last = run
            self._broadcast({"event": "state", "data": run.summary()})
            raise

        run.pid = process.pid
        self._broadcast({"event": "state", "data": run.summary()})

        threading.Thread(
            target=self._reader, args=(run, process), name=f"webby-read-{run.id}", daemon=True
        ).start()
        threading.Thread(
            target=self._reaper, args=(run, process), name=f"webby-wait-{run.id}", daemon=True
        ).start()
        if self.config.max_run_seconds > 0:
            timer = threading.Timer(self.config.max_run_seconds, self._enforce_cap, args=(run,))
            timer.daemon = True
            timer.start()
        return run

    def _enforce_cap(self, run: RunRecord) -> None:
        if run.running:
            self._append(
                run, f"run exceeded the {self.config.max_run_seconds:g}s cap; stopping it"
            )
            self.stop(reason="time limit reached")

    def _append(self, run: RunRecord, text: str) -> None:
        entry = run.append(text.rstrip("\n"), keep=self.config.log_lines)
        self._broadcast(
            {"event": "log", "data": {"run": run.id, "n": entry["n"], "time": entry["time"], "text": entry["text"]}}
        )

    def _reader(self, run: RunRecord, process: subprocess.Popen) -> None:
        stream = process.stdout
        if stream is None:
            return
        try:
            for line in stream:
                self._append(run, line)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _reaper(self, run: RunRecord, process: subprocess.Popen) -> None:
        code = process.wait()
        # Let the reader drain whatever is left in the pipe.
        time.sleep(0.05)
        run.exit_code = code
        run.finished_at = time.time()
        if run.stop_reason is not None:
            run.state = STOPPED
        elif code == 0:
            run.state = FINISHED
        else:
            run.state = FAILED
            if run.error is None:
                run.error = f"the tool exited with status {code}"
        with self._lock:
            if self.current is run:
                self.current = None
            self.last = run
        self._broadcast({"event": "state", "data": run.summary()})

    def stop(self, *, reason: str = "stopped by the operator") -> RunRecord | None:
        """SIGINT the tool (it shuts itself down cleanly), then escalate."""
        with self._lock:
            run = self.current
        if run is None or not run.running or run.pid is None:
            return None
        run.stop_reason = reason
        self._append(run, f"--- stop requested ({reason}) ---")
        signal_number = signal.SIGINT
        try:
            os.killpg(os.getpgid(run.pid), signal_number)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(run.pid, signal_number)
            except OSError:
                return run

        def escalate() -> None:
            deadline = time.time() + self.config.stop_grace_seconds
            while time.time() < deadline:
                if not run.running:
                    return
                time.sleep(0.2)
            for number in (signal.SIGTERM, signal.SIGKILL):
                if not run.running:
                    return
                try:
                    os.killpg(os.getpgid(run.pid or 0), number)
                except OSError:
                    return
                time.sleep(1.0)

        threading.Thread(target=escalate, name=f"webby-stop-{run.id}", daemon=True).start()
        return run

    def stop_all(self) -> None:
        self.stop(reason="bridge shutting down")

    # ----- queries --------------------------------------------------------- #

    def get(self, run_id: str) -> RunRecord:
        for candidate in (self.current, self.last):
            if candidate is not None and candidate.id == run_id:
                return candidate
        raise RunNotFoundError(run_id)

    def snapshot(self) -> dict[str, Any] | None:
        run = self.current or self.last
        return None if run is None else run.summary()

    def log(self, run_id: str, since: int = 0) -> tuple[dict[str, Any], list[dict[str, Any]], int]:
        run = self.get(run_id)
        lines = [line for line in run.lines if line["n"] > since]
        next_index = run.next_index
        return run.summary(), lines, next_index

    def initial_events(self) -> Iterator[dict[str, Any]]:
        """The state plus a tail of the current run, for a fresh SSE client."""
        run = self.current or self.last
        if run is None:
            yield {"event": "state", "data": None}
            return
        yield {"event": "state", "data": run.summary()}
        for line in run.lines[-200:]:
            yield {
                "event": "log",
                "data": {"run": run.id, "n": line["n"], "time": line["time"], "text": line["text"]},
            }
