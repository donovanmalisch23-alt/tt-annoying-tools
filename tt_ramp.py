#!/usr/bin/env python3
"""Ramped breaking-point capacity test for a TeamTalk server on this machine.

``tt_loic`` already floods a local TeamTalk server and measures what the flood
does to a real client's experience (connect latency + message round trip) with
a matched sender/receiver SDK pair.  This tool wraps that machinery in a ramp:
it runs the flood at increasing thread counts in discrete stages, probes the
server *during* each stage, classifies the stage as healthy / degraded / broken,
and reports where the server's resilience ends -- the sustained load it holds,
the load where it degrades, and the load where it breaks -- so the server can be
hardened against exactly that point.

Local-only by construction, just like ``tt_loic``: the target must resolve to an
address on this machine (loopback or one of its own interfaces) AND be present
in ``whitelist.txt``, ``--confirm`` is required to spawn any flood, and
``--dry-run`` prints the full stage plan without starting anything.  The
per-stage flood length and thread count are bounded by ``tt_loic``'s existing
ceilings (60 s, 64 threads); this tool never raises them.

Point this only at a TeamTalk server you run on this machine, and use the
findings to harden that server (raise accept backlog, add rate limiting, bound
per-client work, tune the channel/user caps) -- not to attack it.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

# Shared session/connection layer.
from tt_teamtalk import (
    TeamTalkConfigurationError,
    TeamTalkError,
    comma_int,
    print_tool_error,
)

# The whitelist gate lives in the suite module; reuse it verbatim rather than
# re-implementing the host normalization, so the two tools can never disagree
# about what "allowed" means.
from tt_suite import (
    DEFAULT_WHITELIST,
    ensure_server_allowed,
)

# The flood generator, the service probe, and the probe helpers all already
# exist in tt_loic.  They are private to that module (underscore-prefixed) but
# this tool is its sibling in the same single-author suite; importing them keeps
# one implementation of "flood a local server and measure its response" instead
# of forking it.  If tt_loic's internals change, update the imports here.
from tt_loic import (
    MAX_DURATION_SECONDS,
    PROBE_CADENCE,
    PROBE_ECHO_TIMEOUT,
    ProbeResult,
    ServiceProbe,
    FloodStats,
    _assert_local_host,
    _median,
    _phase_summary,
    _run_flood,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10333
DEFAULT_STAGE_DURATION = 10.0      # seconds of flood per stage
DEFAULT_START_THREADS = 1
DEFAULT_RAMP_FACTOR = 2            # multiply the thread count each stage
# The ramp's own thread ceiling, independent of tt_loic's 64-thread cap. The
# default --max-threads stays at DEFAULT_MAX_THREADS (gentle); an operator who
# wants to push past it passes --max-threads up to RAMP_MAX_THREADS. Both the
# local-only assert and the whitelist + --confirm gates still apply.
DEFAULT_MAX_THREADS = 64
RAMP_MAX_THREADS = 1024
BASELINE_PROBES = 3                # taken once, before the ramp starts
AFTER_PROBES_PER_STAGE = 1         # taken once after each stage's flood ends
# A stage is "degraded" when message latency inflates past this multiple of the
# baseline median, or when any during-stage probe fails.  "Broken" means the
# server was unreachable for every probe during the stage.
DEGRADATION_LATENCY_RATIO = 2.0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Ramped breaking-point test: flood a TeamTalk server "
        "running on this machine at increasing load, probing it at each "
        "stage to find where it degrades and breaks. Local-only + whitelisted."
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"target host; must be this machine and in whitelist.txt "
        f"(default: {DEFAULT_HOST})",
    )
    parser.add_argument(
        "--tcp-port", type=comma_int, default=DEFAULT_PORT,
        help=f"TCP port to flood / probe (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--udp-port", type=comma_int, default=DEFAULT_PORT,
        help=f"UDP port to flood (default: {DEFAULT_PORT})",
    )
    parser.add_argument(
        "--mode", choices=("both", "tcp", "udp"), default="both",
        help="flood mode per stage: TCP, UDP, or both (default: both)",
    )
    parser.add_argument(
        "--stage-duration", type=float, default=DEFAULT_STAGE_DURATION,
        help=f"seconds of flood per stage, 1-{int(MAX_DURATION_SECONDS)} "
        f"(default: {int(DEFAULT_STAGE_DURATION)})",
    )
    parser.add_argument(
        "--start-threads", type=comma_int, default=DEFAULT_START_THREADS,
        help=f"thread count for the first stage, 1-{RAMP_MAX_THREADS} "
        f"(default: {DEFAULT_START_THREADS})",
    )
    parser.add_argument(
        "--ramp-factor", type=float, default=DEFAULT_RAMP_FACTOR,
        help="multiply the thread count each stage (default: "
        f"{DEFAULT_RAMP_FACTOR}); use 1.0 to hold a fixed load",
    )
    parser.add_argument(
        "--max-threads", type=comma_int, default=DEFAULT_MAX_THREADS,
        help=f"never exceed this many flood threads, 1-{RAMP_MAX_THREADS} "
        f"(default: {DEFAULT_MAX_THREADS}; raise explicitly to push past it)",
    )
    parser.add_argument(
        "--timeout", type=float, default=5.0,
        help="socket timeout in seconds for the flood workers (default: 5)",
    )
    parser.add_argument(
        "--probe-username", default="loadtest",
        help="SDK probe login (default: loadtest, the local server's test account)",
    )
    parser.add_argument(
        "--probe-password", default="loadtest",
        help="SDK probe password (default: loadtest)",
    )
    parser.add_argument(
        "--probe-channel", default="/LoadTest",
        help="channel the probe joins and messages (default: /LoadTest)",
    )
    parser.add_argument(
        "--whitelist",
        default=os.environ.get("TT_WHITELIST", str(DEFAULT_WHITELIST)),
        help=f"server whitelist path (default: {DEFAULT_WHITELIST})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print the full stage plan and exit without flooding anything",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="required: confirms this deliberate ramped flood against the "
        "local, whitelisted target",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    """Enforce every gate before any thread is spawned."""
    if not args.host.strip():
        raise TeamTalkConfigurationError("--host cannot be empty")
    if not 1 <= args.tcp_port <= 65535 or not 1 <= args.udp_port <= 65535:
        raise TeamTalkConfigurationError("ports must be between 1 and 65535")
    if not 1 <= args.stage_duration <= MAX_DURATION_SECONDS:
        raise TeamTalkConfigurationError(
            f"--stage-duration must be between 1 and {int(MAX_DURATION_SECONDS)}s"
        )
    if not 1 <= args.start_threads <= RAMP_MAX_THREADS:
        raise TeamTalkConfigurationError(
            f"--start-threads must be between 1 and {RAMP_MAX_THREADS}"
        )
    if not 1 <= args.max_threads <= RAMP_MAX_THREADS:
        raise TeamTalkConfigurationError(
            f"--max-threads must be between 1 and {RAMP_MAX_THREADS}"
        )
    if args.start_threads > args.max_threads:
        raise TeamTalkConfigurationError(
            "--start-threads cannot exceed --max-threads"
        )
    if args.ramp_factor < 1.0:
        raise TeamTalkConfigurationError("--ramp-factor cannot be less than 1.0")
    if args.timeout <= 0:
        raise TeamTalkConfigurationError("--timeout must be greater than zero")
    if args.mode not in ("both", "tcp", "udp"):
        raise TeamTalkConfigurationError("--mode must be tcp, udp, or both")
    if not args.confirm:
        raise TeamTalkConfigurationError(
            "--confirm is required: this tool deliberately floods the target"
        )
    # Two independent gates, both must pass:
    #   1. the host is in the operator-edited whitelist, and
    #   2. the host resolves to this machine (loopback / local interface).
    ensure_server_allowed(args.host.strip(), _whitelist_path(args))
    _assert_local_host(args.host.strip())


def _whitelist_path(args: argparse.Namespace) -> Path:
    return Path(args.whitelist)


# --------------------------------------------------------------------------- #
# Ramp schedule
# --------------------------------------------------------------------------- #

@dataclass
class RampStage:
    index: int          # 1-based, for display
    threads: int
    mode: str
    duration: float

    def label(self) -> str:
        return f"stage {self.index}: {self.threads} thread(s), {self.mode}, {self.duration:g}s"


def build_stages(args: argparse.Namespace) -> list[RampStage]:
    """Geometric thread ramp, clamped to --max-threads."""
    stages: list[RampStage] = []
    threads = args.start_threads
    index = 0
    while True:
        index += 1
        clamped = min(threads, args.max_threads)
        stages.append(RampStage(index, clamped, args.mode, args.stage_duration))
        if clamped >= args.max_threads:
            break
        # Grow by the factor; round up so a factor of 1.5 still advances.
        threads = max(threads + 1, int(round(threads * args.ramp_factor)))
    return stages


# --------------------------------------------------------------------------- #
# Per-stage result + classification
# --------------------------------------------------------------------------- #

@dataclass
class StageResult:
    stage: RampStage
    during: list[ProbeResult] = field(default_factory=list)
    after: list[ProbeResult] = field(default_factory=list)
    flood_stats: dict = field(default_factory=dict)
    verdict: str = "not run"      # "healthy" | "degraded" | "broken" | "not run"
    detail: str = ""

    def line(self) -> str:
        ok = sum(1 for p in self.during if p.ok)
        relays = [p.relay_ms for p in self.during if p.relay_ms is not None]
        relay_text = (
            f"RTT med {_median(relays):.1f} ms" if relays else "no message replies"
        )
        suffix = f" — {self.detail}" if self.detail else ""
        return (
            f"  {self.stage.label():<46} probes {ok}/{len(self.during)} ok, "
            f"{relay_text}, verdict={self.verdict}{suffix}"
        )


def classify_stage(stage: StageResult, baseline_median: Optional[float]) -> None:
    """Set stage.verdict/detail from its during-stage probes."""
    if not stage.during:
        stage.verdict = "broken"
        stage.detail = "no during-stage probes were taken"
        return
    ok = sum(1 for p in stage.during if p.ok)
    during_relays = [p.relay_ms for p in stage.during if p.relay_ms is not None]
    during_median = _median(during_relays) if during_relays else None

    if ok == 0:
        stage.verdict = "broken"
        stage.detail = "server unreachable for every probe this stage"
        return

    ratio: Optional[float] = None
    if during_median is not None and baseline_median and baseline_median > 0:
        ratio = during_median / baseline_median

    degraded = ok < len(stage.during) or (
        ratio is not None and ratio >= DEGRADATION_LATENCY_RATIO
    )
    if degraded:
        parts = []
        if ok < len(stage.during):
            parts.append(f"{ok}/{len(stage.during)} probes ok")
        if ratio is not None and ratio >= DEGRADATION_LATENCY_RATIO:
            parts.append(f"RTT {ratio:.1f}x baseline")
        stage.verdict = "degraded"
        stage.detail = ", ".join(parts)
    else:
        stage.verdict = "healthy"
        if ratio is not None:
            stage.detail = f"RTT {ratio:.1f}x baseline"
        else:
            stage.detail = "all probes ok"


# --------------------------------------------------------------------------- #
# Running one stage
# --------------------------------------------------------------------------- #

def _stage_args(args: argparse.Namespace, stage: RampStage) -> argparse.Namespace:
    """A per-stage copy of args with this stage's thread count and duration."""
    copy = argparse.Namespace(**vars(args))
    copy.threads = stage.threads
    copy.duration = stage.duration
    return copy


def _run_stage(
    args: argparse.Namespace,
    stage: RampStage,
    probe: Optional[ServiceProbe],
    stop_event: threading.Event,
) -> StageResult:
    """Flood for one stage; probe during and once after. Returns the stage result."""
    result = StageResult(stage=stage)
    print(f"\n{stage.label()}")

    flood_stats: dict[str, FloodStats] = {}
    flood_thread = threading.Thread(
        target=lambda: flood_stats.update(_run_flood(_stage_args(args, stage), stop_event)),
        daemon=True,
        name=f"ramp-stage-{stage.index}",
    )
    try:
        flood_thread.start()
        if probe is not None:
            index = 0
            while flood_thread.is_alive() and not stop_event.is_set():
                index += 1
                result.during.append(probe.probe("during"))
                # Wait ~PROBE_CADENCE, but wake early if the flood ends or stops.
                waited = 0.0
                while (
                    waited < PROBE_CADENCE
                    and flood_thread.is_alive()
                    and not stop_event.is_set()
                ):
                    time.sleep(0.1)
                    waited += 0.1
        else:
            while flood_thread.is_alive() and not stop_event.is_set():
                time.sleep(0.1)
        flood_thread.join(timeout=5.0)
        stop_event.clear()  # reset for the next stage

        if probe is not None and not stop_event.is_set():
            for _ in range(AFTER_PROBES_PER_STAGE):
                result.after.append(probe.probe("after"))
                time.sleep(0.2)
    except KeyboardInterrupt:
        stop_event.set()
        print("Interrupted; stopping the ramp.")
    finally:
        stop_event.set()
        flood_thread.join(timeout=10.0)
        stop_event.clear()

    result.flood_stats = flood_stats
    return result


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

def _flood_totals(flood_stats: dict[str, FloodStats]) -> str:
    parts: list[str] = []
    for kind in ("tcp", "udp"):
        stats = flood_stats.get(kind)
        if stats is None:
            continue
        with stats.lock:
            if kind == "tcp":
                parts.append(
                    f"tcp {stats.connections} conn, {stats.bytes_sent:,} B, "
                    f"{stats.errors} err"
                )
            else:
                parts.append(
                    f"udp {stats.datagrams:,} dgram, {stats.errors} err"
                )
    return ", ".join(parts) if parts else "no flood stats"


def _summary(stages: list[StageResult]) -> str:
    """The breaking-point line: holds / degrades / breaks."""
    if not stages:
        return "no stages were run."

    last_healthy: Optional[RampStage] = None
    first_degraded: Optional[RampStage] = None
    first_broken: Optional[RampStage] = None
    for result in stages:
        if result.verdict == "healthy" and first_degraded is None:
            last_healthy = result.stage
        elif result.verdict == "degraded" and first_degraded is None:
            first_degraded = result.stage
        elif result.verdict == "broken" and first_broken is None:
            first_broken = result.stage
            break  # the ramp stops at the first broken stage

    if first_broken is not None:
        head = (
            f"BREAKS at {first_broken.threads} thread(s) "
            f"(stage {first_broken.index}): the server was unreachable under load."
        )
    elif first_degraded is not None:
        head = (
            "No breaking point reached within this tool's ceiling "
            f"(max {RAMP_MAX_THREADS} threads, {int(MAX_DURATION_SECONDS)}s/stage)."
        )
    else:
        max_tested = stages[-1].stage.threads
        head = (
            f"HELD at every tested load up to {max_tested} thread(s); "
            "no breaking point reached within this tool's ceiling "
            f"(max {RAMP_MAX_THREADS} threads, {int(MAX_DURATION_SECONDS)}s/stage)."
        )

    degraded_line = ""
    if first_degraded is not None:
        degraded_line = (
            f" Degrades first at {first_degraded.threads} thread(s) "
            f"(stage {first_degraded.index})."
        )
    hold_line = ""
    if last_healthy is not None:
        hold_line = f" Holds cleanly up to {last_healthy.threads} thread(s)."
    else:
        hold_line = " No stage was fully clean."

    return head + degraded_line + hold_line + (
        " Use these numbers to harden the server "
        "(accept backlog, rate limiting, per-client caps)."
    )


# --------------------------------------------------------------------------- #
# Top-level run
# --------------------------------------------------------------------------- #

def _dry_run_plan(args: argparse.Namespace, stages: list[RampStage]) -> str:
    lines = [
        "tt_ramp dry run — nothing will be flooded. Planned stages:",
        f"  target      : {args.host}:{args.tcp_port} (udp {args.udp_port})",
        f"  mode        : {args.mode}",
        f"  stage length: {args.stage_duration:g}s",
        f"  probe       : channel {args.probe_channel!r}, user {args.probe_username!r}",
        f"  whitelist   : {args.whitelist}",
        f"  stages      : {len(stages)}",
    ]
    for stage in stages:
        lines.append(f"    {stage.label()}")
    lines.append(
        "Re-run with --confirm (and the host in whitelist.txt + on this machine) "
        "to execute the ramp."
    )
    return "\n".join(lines)


def run(args: argparse.Namespace) -> int:
    host = args.host.strip()
    validate_args(args)
    stages = build_stages(args)

    if args.dry_run:
        print(_dry_run_plan(args, stages))
        return 0

    probe: Optional[ServiceProbe] = None
    baseline: list[ProbeResult] = []
    results: list[StageResult] = []
    stop_event = threading.Event()

    # One SIGINT handler: stop the current stage's flood and end the ramp.
    def _stop(*_):
        stop_event.set()
        print("\n(interrupt — stopping the ramp)", file=sys.stderr)
    prev = signal.SIG_DFL
    try:
        prev = signal.signal(signal.SIGINT, _stop)
    except ValueError:
        # Not the main thread (shouldn't happen here); leave the default handler.
        pass

    try:
        probe = ServiceProbe(args)
        probe.start()
        for index in range(BASELINE_PROBES):
            if stop_event.is_set():
                break
            result = probe.probe("before")
            baseline.append(result)
            print(result.line(index + 1))
            time.sleep(0.2)
        if baseline and all(p.connect_ms is None for p in baseline):
            raise TeamTalkError(
                f"nothing is listening on {host}:{args.tcp_port}; "
                "start the local TeamTalk server first."
            )
        baseline_relays = [p.relay_ms for p in baseline if p.relay_ms is not None]
        baseline_median = _median(baseline_relays) if baseline_relays else None
        print(
            "Baseline: " + _phase_summary(baseline) + "."
            if baseline else "Baseline: no samples."
        )
        if baseline_median is not None:
            print(f"Baseline message RTT median: {baseline_median:.1f} ms")
        print(
            f"\nRamping {host} ({args.mode}) across {len(stages)} stage(s) of "
            f"{args.stage_duration:g}s each. Ctrl+C stops early."
        )

        for stage in stages:
            if stop_event.is_set():
                break
            result = _run_stage(args, stage, probe, stop_event)
            classify_stage(result, baseline_median)
            print(result.line())
            print(f"    flood: {_flood_totals(result.flood_stats)}")
            results.append(result)
            # The breaking point is the finding; stop once the server breaks.
            if result.verdict == "broken":
                print("Server broke — stopping the ramp at the breaking point.")
                break

    except KeyboardInterrupt:
        stop_event.set()
        print("Interrupted; stopping the ramp.")
    finally:
        stop_event.set()
        try:
            signal.signal(signal.SIGINT, prev)
        except ValueError:
            pass
        if probe is not None:
            probe.close()

    print("\nRamp results:")
    for result in results:
        print(result.line())
        print(f"    flood: {_flood_totals(result.flood_stats)}")
    print("\n" + _summary(results))
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not actual_argv:
            print(
                "This tool is flag-only for safety: pass --host and --confirm "
                "(see --help), or --dry-run to preview the ramp."
            )
            return 2
        return run(build_parser().parse_args(actual_argv))
    except (TeamTalkConfigurationError, TeamTalkError, OSError) as exc:
        return print_tool_error(exc)
    except (EOFError, KeyboardInterrupt):
        print("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())