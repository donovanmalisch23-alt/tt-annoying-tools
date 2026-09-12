"""Tests for tt_ramp (ramped breaking-point capacity test).

Covers the safety gates (whitelist + --confirm + bounds), the
stage schedule (including per-stage durations, simultaneous mode, and the
total-time cap), per-stage classification (healthy / degraded / broken), the
summary line, and the dry run.  The flood itself is exercised against the live
local server, not in unit tests; here the flood generator and the service probe
are faked.
"""

from __future__ import annotations

import argparse
import threading
import time

import pytest

import tt_ramp
from tt_loic import FloodStats, ProbeResult
from tt_teamtalk import ConnectionConfig, TeamTalkConfigurationError


def namespace(**overrides):
    args = dict(
        host="127.0.0.1",
        tcp_port=10333,
        udp_port=10333,
        mode="both",
        stage_duration=10.0,
        stage_durations=None,
        simultaneous=False,
        max_total_time=None,
        start_threads=1,
        ramp_factor=2.0,
        max_threads=64,
        timeout=5.0,
        probe_username="loadtest",
        probe_password="loadtest",
        probe_channel="/LoadTest",
        whitelist="whitelist.txt",
        dry_run=False,
        confirm=True,
    )
    args.update(overrides)
    return argparse.Namespace(**args)


class TestValidateArgs:
    def test_defaults_ok(self):
        tt_ramp.validate_args(namespace())  # 127.0.0.1 is whitelisted

    def test_probe_credentials_default_blank(self, monkeypatch):
        # No hardcoded account: blank means the probe logs in anonymously.
        monkeypatch.delenv("TT_USERNAME", raising=False)
        monkeypatch.delenv("TT_PASSWORD", raising=False)
        args = tt_ramp.build_parser().parse_args([])
        assert args.probe_username == ""
        assert args.probe_password == ""

    def test_blank_probe_credentials_allowed(self):
        # Anonymous login is a valid probe account on servers without users.
        tt_ramp.validate_args(namespace(probe_username="", probe_password=""))

    def test_confirm_required(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            tt_ramp.validate_args(namespace(confirm=False))

    def test_non_whitelisted_host_refused(self):
        # TEST-NET-3: not in whitelist.txt.
        with pytest.raises(TeamTalkConfigurationError, match="not in the whitelist"):
            tt_ramp.validate_args(namespace(host="203.0.113.10"))

    def test_whitelisted_remote_host_allowed(self):
        # The whitelist is the sole gate: a whitelisted host that resolves
        # off-machine is a legal target, not refused for being remote.
        tt_ramp.validate_args(namespace(host="paralleledition.xyz"))

    def test_max_threads_bounded(self):
        # The ramp ceiling is 1024; anything above must be refused.
        with pytest.raises(TeamTalkConfigurationError, match="--max-threads"):
            tt_ramp.validate_args(namespace(max_threads=2000))

    def test_stage_duration_bounded(self):
        with pytest.raises(TeamTalkConfigurationError, match="--stage-duration"):
            tt_ramp.validate_args(namespace(stage_duration=61.0))

    def test_stage_durations_entry_out_of_range_refused(self):
        with pytest.raises(
            TeamTalkConfigurationError, match="--stage-durations entry 2"
        ):
            tt_ramp.validate_args(namespace(stage_durations=[5.0, 0.5]))

    def test_stage_durations_within_range_ok(self):
        tt_ramp.validate_args(namespace(stage_durations=[5.0, 10.0, 60.0]))

    def test_max_total_time_must_be_positive(self):
        with pytest.raises(TeamTalkConfigurationError, match="--max-total-time"):
            tt_ramp.validate_args(namespace(max_total_time=0.0))

    def test_max_total_time_ok(self):
        tt_ramp.validate_args(namespace(max_total_time=90.0))

    def test_start_exceeding_max_refused(self):
        with pytest.raises(TeamTalkConfigurationError, match="start-threads"):
            tt_ramp.validate_args(namespace(start_threads=8, max_threads=4))

    def test_ramp_factor_below_one_refused(self):
        with pytest.raises(TeamTalkConfigurationError, match="--ramp-factor"):
            tt_ramp.validate_args(namespace(ramp_factor=0.5))

    def test_port_bounds(self):
        with pytest.raises(TeamTalkConfigurationError, match="ports"):
            tt_ramp.validate_args(namespace(tcp_port=0))

    def test_bad_mode(self):
        with pytest.raises(TeamTalkConfigurationError, match="--mode"):
            tt_ramp.validate_args(namespace(mode="http"))


class TestBuildStages:
    def test_default_geometric_ramp(self):
        stages = tt_ramp.build_stages(namespace())
        assert [s.threads for s in stages] == [1, 2, 4, 8, 16, 32, 64]
        assert all(s.mode == "both" and s.duration == 10.0 for s in stages)
        assert [s.index for s in stages] == list(range(1, 8))

    def test_clamped_to_max_threads(self):
        stages = tt_ramp.build_stages(namespace(max_threads=10))
        assert [s.threads for s in stages] == [1, 2, 4, 8, 10]
        assert stages[-1].threads == 10  # never exceeds the ceiling

    def test_single_stage_when_start_equals_max(self):
        stages = tt_ramp.build_stages(namespace(start_threads=64, max_threads=64))
        assert [s.threads for s in stages] == [64]

    def test_factor_one_increments_by_one(self):
        # ramp_factor 1.0 must still advance (by +1) so the ramp is not stuck.
        stages = tt_ramp.build_stages(
            namespace(ramp_factor=1.0, start_threads=1, max_threads=3)
        )
        assert [s.threads for s in stages] == [1, 2, 3]

    def test_stage_durations_assign_per_stage(self):
        # Stage 1 gets the first entry, stage 2 the second, and the last
        # entry repeats for every further stage.
        stages = tt_ramp.build_stages(
            namespace(stage_durations=[5.0, 10.0], max_threads=8)
        )
        assert [s.duration for s in stages] == [5.0, 10.0, 10.0, 10.0]
        assert [s.threads for s in stages] == [1, 2, 4, 8]

    def test_stage_durations_cover_every_stage(self):
        stages = tt_ramp.build_stages(
            namespace(stage_durations=[1.0, 2.0, 3.0], max_threads=4)
        )
        assert [s.duration for s in stages] == [1.0, 2.0, 3.0]


class TestParseStageDurations:
    def test_comma_separated(self):
        assert tt_ramp.parse_stage_durations("5,10,20") == [5.0, 10.0, 20.0]

    def test_single_entry(self):
        assert tt_ramp.parse_stage_durations("7") == [7.0]

    def test_spaces_and_empties_tolerated(self):
        assert tt_ramp.parse_stage_durations(" 5 , ,10 ") == [5.0, 10.0]

    def test_empty_string_refused(self):
        with pytest.raises(argparse.ArgumentTypeError):
            tt_ramp.parse_stage_durations("  ,  ")

    def test_non_numeric_refused(self):
        with pytest.raises(argparse.ArgumentTypeError):
            tt_ramp.parse_stage_durations("5,soon")


class TestClassifyStage:
    def _stage(self, during):
        return tt_ramp.StageResult(stage=tt_ramp.RampStage(1, 4, "both", 10.0),
                                   during=list(during))

    def test_healthy(self):
        stage = self._stage([
            ProbeResult("during", 1.0, 0.5, True),
            ProbeResult("during", 1.0, 0.6, True),
        ])
        tt_ramp.classify_stage(stage, baseline_median=0.5)
        assert stage.verdict == "healthy"

    def test_degraded_by_latency_ratio(self):
        stage = self._stage([
            ProbeResult("during", 1.0, 2.0, True),
            ProbeResult("during", 1.0, 2.1, True),
        ])
        tt_ramp.classify_stage(stage, baseline_median=0.5)
        assert stage.verdict == "degraded"
        assert "2.0x" not in stage.detail  # 4.0x, not the threshold literal
        assert "x baseline" in stage.detail

    def test_degraded_by_partial_failure(self):
        stage = self._stage([
            ProbeResult("during", 1.0, 0.5, True),
            ProbeResult("during", None, None, False, "refused"),
        ])
        tt_ramp.classify_stage(stage, baseline_median=0.5)
        assert stage.verdict == "degraded"
        assert "1/2 probes ok" in stage.detail

    def test_broken_when_no_probes_ok(self):
        stage = self._stage([
            ProbeResult("during", None, None, False, "connect refused"),
        ])
        tt_ramp.classify_stage(stage, baseline_median=0.5)
        assert stage.verdict == "broken"

    def test_broken_when_no_during_probes(self):
        stage = self._stage([])
        tt_ramp.classify_stage(stage, baseline_median=0.5)
        assert stage.verdict == "broken"

    def test_no_baseline_still_classifies(self):
        stage = self._stage([ProbeResult("during", 1.0, 0.5, True)])
        tt_ramp.classify_stage(stage, baseline_median=None)
        assert stage.verdict == "healthy"


class TestSummary:
    def _result(self, index, threads, verdict):
        return tt_ramp.StageResult(
            stage=tt_ramp.RampStage(index, threads, "both", 10.0),
            verdict=verdict,
        )

    def test_breaks_at_first_broken(self):
        stages = [
            self._result(1, 1, "healthy"),
            self._result(2, 2, "degraded"),
            self._result(3, 4, "broken"),
        ]
        summary = tt_ramp._summary(stages)
        assert "BREAKS at 4 thread(s)" in summary
        assert "Degrades first at 2 thread(s)" in summary
        assert "Holds cleanly up to 1 thread(s)" in summary

    def test_held_to_ceiling_when_nothing_breaks(self):
        stages = [
            self._result(1, 1, "healthy"),
            self._result(2, 2, "healthy"),
        ]
        summary = tt_ramp._summary(stages)
        assert "HELD at every tested load up to 2 thread(s)" in summary
        assert "no breaking point reached" in summary

    def test_degraded_but_not_broken_does_not_claim_held(self):
        # A degraded stage means the "HELD at every tested load" claim must NOT
        # appear; the summary reports degradation + clean hold instead.
        stages = [
            self._result(1, 4, "healthy"),
            self._result(2, 8, "degraded"),
        ]
        summary = tt_ramp._summary(stages)
        assert "HELD at every tested load" not in summary
        assert "No breaking point reached" in summary
        assert "Degrades first at 8 thread(s)" in summary
        assert "Holds cleanly up to 4 thread(s)" in summary

    def test_no_stages(self):
        assert tt_ramp._summary([]) == "no stages were run."


class TestDryRun:
    def test_prints_plan_and_exits_clean(self, capsys):
        rc = tt_ramp.run(namespace(host="127.0.0.1", dry_run=True, confirm=True))
        out = capsys.readouterr().out
        assert rc == 0
        assert "nothing will be flooded" in out
        assert "stage 7: 64 thread(s)" in out
        assert "stages      : 7" in out

    def test_dry_run_does_not_flood(self, monkeypatch):
        called = {"n": 0}

        def boom(args, stop_event):
            called["n"] += 1
            raise AssertionError("dry run must not start a flood")

        monkeypatch.setattr(tt_ramp, "_run_flood", boom)
        monkeypatch.setattr(tt_ramp, "ServiceProbe", lambda args: pytest.fail("no probe"))
        rc = tt_ramp.run(namespace(host="127.0.0.1", dry_run=True, confirm=True))
        assert rc == 0
        assert called["n"] == 0


class TestRunStage:
    def test_stage_probes_during_and_after_and_classifies(self, monkeypatch):
        # Fake flood: block briefly so the during-probe loop can sample it.
        def fake_flood(args, stop_event):
            stop_event.wait(0.3)
            return {"tcp": FloodStats()}

        class FakeProbe:
            def probe(self, phase):
                return ProbeResult(phase, 1.0, 0.5, True)

            def close(self):
                pass

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        stop_event = threading.Event()
        stage = tt_ramp.RampStage(1, 1, "both", 10.0)
        result = tt_ramp._run_stage(namespace(), stage, FakeProbe(), stop_event)
        assert len(result.during) >= 1
        assert len(result.after) == tt_ramp.AFTER_PROBES_PER_STAGE
        tt_ramp.classify_stage(result, baseline_median=0.5)
        assert result.verdict == "healthy"
        assert "tcp" in result.flood_stats

    def test_stage_respects_stop_event(self, monkeypatch):
        def fake_flood(args, stop_event):
            stop_event.wait(10.0)
            return {}

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        stop_event = threading.Event()
        stop_event.set()  # pre-stopped
        stage = tt_ramp.RampStage(1, 1, "both", 10.0)
        result = tt_ramp._run_stage(namespace(), stage, None, stop_event)
        # No probe + stop set => no during samples => classified broken.
        tt_ramp.classify_stage(result, baseline_median=0.5)
        assert result.verdict == "broken"


class TestSimultaneousRun:
    """--simultaneous: every stage floods at the same time."""

    class FakeProbe:
        def probe(self, phase):
            return ProbeResult(phase, 1.0, 0.5, True)

        def close(self):
            pass

    def test_all_stages_flood_concurrently(self, monkeypatch):
        # Track how many fake floods are in flight at once: simultaneous
        # mode must overlap all three stages, where the sequential ramp
        # would run them one after another.
        in_flight = {"n": 0}
        peak = {"n": 0}
        lock = threading.Lock()

        def fake_flood(args, stop_event):
            with lock:
                in_flight["n"] += 1
                peak["n"] = max(peak["n"], in_flight["n"])
            try:
                stop_event.wait(0.5)
                return {"tcp": FloodStats()}
            finally:
                with lock:
                    in_flight["n"] -= 1

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        stages = [
            tt_ramp.RampStage(1, 1, "both", 10.0),
            tt_ramp.RampStage(2, 2, "both", 10.0),
            tt_ramp.RampStage(3, 4, "both", 10.0),
        ]
        stop_event = threading.Event()
        stage_events = [threading.Event() for _ in stages]
        results = tt_ramp._run_simultaneous(
            namespace(simultaneous=True), stages, self.FakeProbe(),
            stop_event, stage_events,
        )
        assert peak["n"] == 3  # all stages flooded at the same time
        assert len(results) == 3
        for result in results:
            assert "tcp" in result.flood_stats
            assert len(result.during) >= 1
            assert len(result.after) == tt_ramp.AFTER_PROBES_PER_STAGE

    def test_run_without_probe_still_collects_stats(self, monkeypatch):
        # --no-probe path: stages of different lengths must all record
        # their own flood stats, and the runner must not block forever
        # waiting for probes that do not exist.
        def fake_flood(args, stop_event):
            # stage threads were 1, 2, 4 -> sleep grows with the count
            stop_event.wait(0.2 * args.threads)
            return {"tcp": FloodStats()}

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        stages = [
            tt_ramp.RampStage(1, 1, "both", 1.0),
            tt_ramp.RampStage(2, 2, "both", 1.0),
            tt_ramp.RampStage(3, 4, "both", 1.0),
        ]
        stop_event = threading.Event()
        stage_events = [threading.Event() for _ in stages]
        results = tt_ramp._run_simultaneous(
            namespace(simultaneous=True), stages, None, stop_event, stage_events,
        )
        assert all(result.flood_stats.get("tcp") is not None for result in results)

    def test_shared_stop_event_ends_everything(self, monkeypatch):
        def fake_flood(args, stop_event):
            stop_event.wait(10.0)  # would run forever without the stop
            return {}

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        stages = [tt_ramp.RampStage(1, 1, "both", 10.0)]
        stop_event = threading.Event()
        stop_event.set()  # pre-stopped, e.g. by the total-time cap
        stage_events = [threading.Event()]
        results = tt_ramp._run_simultaneous(
            namespace(simultaneous=True), stages, None, stop_event, stage_events,
        )
        assert results[0].flood_stats == {}  # the flood returned, not leaked


class TestMaxTotalTimeCap:
    """--max-total-time: one wall-clock cap ends the whole ramp."""

    def test_run_stops_at_cap(self, monkeypatch):
        # A flood that would outlast the cap must be cut short: the cap
        # timer sets the stop events, the stages all return, and run()
        # finishes with whatever results exist.
        def fake_flood(args, stop_event):
            stop_event.wait(30.0)
            return {"tcp": FloodStats()}

        class FakeProbe:
            def __init__(self, args):
                pass

            def start(self):
                pass

            def probe(self, phase):
                return ProbeResult(phase, 1.0, 0.5, True)

            def close(self):
                pass

        monkeypatch.setattr(tt_ramp, "_run_flood", fake_flood)
        monkeypatch.setattr(tt_ramp, "ServiceProbe", FakeProbe)
        started = time.monotonic()
        rc = tt_ramp.run(namespace(
            host="127.0.0.1", confirm=True,
            simultaneous=True,
            max_total_time=1.0,
            start_threads=1, max_threads=1, stage_duration=10.0,
        ))
        elapsed = time.monotonic() - started
        assert rc == 0
        assert elapsed < 10.0  # stopped by the cap, not the fake 30 s flood
        assert not any(
            thread.name.startswith("ramp-") and thread.is_alive()
            for thread in threading.enumerate()
        )


class TestInteractiveRun:
    """No-arguments path: shared prompts, gates before the go/no-go, then run."""

    def _config(self, **overrides):
        values = dict(
            host="127.0.0.1",
            tcp_port=10333,
            udp_port=10333,
            username="loadtest",
            password="loadtest",
        )
        values.update(overrides)
        return ConnectionConfig(**values)

    def test_prompt_answers_become_ramp_args(self, monkeypatch):
        captured = {}

        def fake_run(args):
            captured["args"] = args
            return 0

        monkeypatch.setattr(
            tt_ramp, "prompt_connection_config",
            lambda **_: self._config(username="probe", password="secret"),
        )
        monkeypatch.setattr(
            tt_ramp, "prompt_yes_no", lambda label, default: True
        )
        monkeypatch.setattr(tt_ramp, "run", fake_run)
        assert tt_ramp.interactive_run() == 0
        args = captured["args"]
        # The account answers become the probe login; host/ports the target.
        assert args.host == "127.0.0.1"
        assert (args.tcp_port, args.udp_port) == (10333, 10333)
        assert args.probe_username == "probe"
        assert args.probe_password == "secret"
        assert args.probe_channel == "/LoadTest"
        # The go/no-go answer stands in for --confirm; nothing else changes.
        assert args.confirm is True
        assert args.dry_run is False
        assert args.max_threads == tt_ramp.DEFAULT_MAX_THREADS
        assert args.stage_duration == tt_ramp.DEFAULT_STAGE_DURATION

    def test_declined_go_no_go_cancels_without_running(self, monkeypatch, capsys):
        monkeypatch.setattr(
            tt_ramp, "prompt_connection_config", lambda **_: self._config()
        )
        monkeypatch.setattr(
            tt_ramp, "prompt_yes_no", lambda label, default: False
        )
        monkeypatch.setattr(
            tt_ramp, "run", lambda args: pytest.fail("declined run must not start")
        )
        assert tt_ramp.interactive_run() == 0
        assert "Ramp cancelled." in capsys.readouterr().out

    def test_non_whitelisted_host_refused_before_go_no_go(self, monkeypatch):
        monkeypatch.setattr(
            tt_ramp, "prompt_connection_config",
            lambda **_: self._config(host="203.0.113.10"),
        )
        monkeypatch.setattr(
            tt_ramp, "prompt_yes_no",
            lambda label, default: pytest.fail("refused host must not reach confirm"),
        )
        with pytest.raises(TeamTalkConfigurationError, match="not in the whitelist"):
            tt_ramp.interactive_run()

    def test_whitelisted_remote_host_reaches_go_no_go(self, monkeypatch):
        # A whitelisted host that resolves off-machine is a legal target, so
        # it must pass the gate and reach the confirm question.
        monkeypatch.setattr(
            tt_ramp, "prompt_connection_config",
            lambda **_: self._config(host="paralleledition.xyz"),
        )
        seen = {}

        def fake_yes_no(label, default):
            seen["label"] = label
            seen["default"] = default
            return False  # decline; reaching the prompt is what's under test

        monkeypatch.setattr(tt_ramp, "prompt_yes_no", fake_yes_no)
        monkeypatch.setattr(
            tt_ramp, "run", lambda args: pytest.fail("declined run must not start")
        )
        assert tt_ramp.interactive_run() == 0
        assert "paralleledition.xyz" in seen["label"]
        assert seen["default"] is False


class TestMainDispatch:
    def test_no_args_dispatches_to_interactive_run(self, monkeypatch):
        called = []

        def fake_interactive():
            called.append(True)
            return 7

        monkeypatch.setattr(tt_ramp, "interactive_run", fake_interactive)
        assert tt_ramp.main([]) == 7
        assert called == [True]

    def test_flag_path_still_requires_confirm(self, monkeypatch):
        monkeypatch.setattr(
            tt_ramp, "_run_flood",
            lambda args, stop_event: pytest.fail("must not flood"),
        )
        assert tt_ramp.main(["--host", "127.0.0.1", "--dry-run"]) == 2

    def test_flag_path_dry_run_with_confirm_previews(self, monkeypatch, capsys):
        monkeypatch.setattr(
            tt_ramp, "_run_flood",
            lambda args, stop_event: pytest.fail("must not flood"),
        )
        monkeypatch.setattr(
            tt_ramp, "ServiceProbe", lambda args: pytest.fail("no probe")
        )
        assert tt_ramp.main(["--host", "127.0.0.1", "--confirm", "--dry-run"]) == 0
        assert "nothing will be flooded" in capsys.readouterr().out