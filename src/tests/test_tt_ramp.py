"""Tests for tt_ramp (ramped breaking-point capacity test).

Covers the safety gates (whitelist + local-only + --confirm + bounds), the
stage schedule, per-stage classification (healthy / degraded / broken), the
summary line, and the dry run.  The flood itself is exercised against the live
local server, not in unit tests; here the flood generator and the service probe
are faked.
"""

from __future__ import annotations

import argparse
import threading

import pytest

import tt_ramp
from tt_loic import FloodStats, ProbeResult
from tt_teamtalk import TeamTalkConfigurationError


def namespace(**overrides):
    args = dict(
        host="127.0.0.1",
        tcp_port=10333,
        udp_port=10333,
        mode="both",
        stage_duration=10.0,
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
        tt_ramp.validate_args(namespace())  # 127.0.0.1 is whitelisted + local

    def test_confirm_required(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            tt_ramp.validate_args(namespace(confirm=False))

    def test_non_whitelisted_host_refused(self):
        # TEST-NET-3: not in whitelist.txt and not local either.
        with pytest.raises(TeamTalkConfigurationError, match="not in the whitelist"):
            tt_ramp.validate_args(namespace(host="203.0.113.10"))

    def test_whitelisted_but_remote_refused(self):
        # paralleledition.xyz is in the whitelist but resolves off-machine, so
        # the local-only gate must refuse it even though the whitelist passed.
        with pytest.raises(TeamTalkConfigurationError, match="not this machine"):
            tt_ramp.validate_args(namespace(host="paralleledition.xyz"))

    def test_max_threads_bounded(self):
        # The ramp ceiling is 1024; anything above must be refused.
        with pytest.raises(TeamTalkConfigurationError, match="--max-threads"):
            tt_ramp.validate_args(namespace(max_threads=2000))

    def test_stage_duration_bounded(self):
        with pytest.raises(TeamTalkConfigurationError, match="--stage-duration"):
            tt_ramp.validate_args(namespace(stage_duration=61.0))

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