"""Tests for tt_loic (LOIC-style local-only flood tester).

Covers the safety gate (the tool must refuse anything that is not this
machine), argument validation, and the report verdict.  The flood itself is
exercised against the live local server, not in unit tests.
"""

from __future__ import annotations

import argparse

import pytest

import tt_loic
from tt_teamtalk import TeamTalkConfigurationError, TeamTalkError


def namespace(**overrides):
    args = dict(
        host="127.0.0.1",
        tcp_port=10333,
        udp_port=10333,
        mode="both",
        threads=8,
        duration=10.0,
        timeout=5.0,
        probe_username="loadtest",
        probe_password="loadtest",
        probe_channel="/LoadTest",
        no_probe=False,
        confirm=True,
    )
    args.update(overrides)
    return argparse.Namespace(**args)


class TestValidateArgs:
    def test_defaults_ok(self):
        tt_loic.validate_args(namespace())  # must not raise

    def test_confirm_required(self):
        with pytest.raises(TeamTalkConfigurationError, match="--confirm"):
            tt_loic.validate_args(namespace(confirm=False))

    def test_duration_capped(self):
        with pytest.raises(TeamTalkConfigurationError, match="--duration"):
            tt_loic.validate_args(namespace(duration=61.0))

    def test_threads_bounds(self):
        with pytest.raises(TeamTalkConfigurationError, match="--threads"):
            tt_loic.validate_args(namespace(threads=65))

    def test_port_bounds(self):
        with pytest.raises(TeamTalkConfigurationError, match="ports"):
            tt_loic.validate_args(namespace(tcp_port=0))

    def test_bad_mode(self):
        with pytest.raises(TeamTalkConfigurationError, match="--mode"):
            tt_loic.validate_args(namespace(mode="http"))


class TestLocalGate:
    def test_loopback_allowed(self):
        tt_loic.validate_args(namespace(host="127.0.0.1"))  # loopback, no lookup

    def test_local_interface_allowed(self, monkeypatch):
        # an address assigned to this machine is allowed even off-loopback
        monkeypatch.setattr(tt_loic, "_local_addresses", lambda: {"192.168.0.191"})
        tt_loic.validate_args(namespace(host="192.168.0.191"))

    def test_remote_address_refused(self):
        # TEST-NET-3: a numeric address that is never this machine
        with pytest.raises(
            TeamTalkConfigurationError, match="only floods servers running locally"
        ):
            tt_loic.validate_args(namespace(host="203.0.113.10"))

    def test_empty_host_refused(self):
        with pytest.raises(TeamTalkConfigurationError, match="--host"):
            tt_loic.validate_args(namespace(host=" "))


class TestVerdict:
    def _probe(self, phase, ok=True, relay_ms=10.0):
        return tt_loic.ProbeResult(
            phase=phase, connect_ms=1.0,
            relay_ms=relay_ms if ok else None, ok=ok,
        )

    def test_stays_in_service(self):
        baseline = [self._probe("before") for _ in range(3)]
        during = [self._probe("during") for _ in range(8)]
        after = [self._probe("after") for _ in range(2)]
        text = tt_loic._verdict(baseline, during, after)
        assert "stayed fully in service" in text

    def test_outage_noted_with_recovery(self):
        baseline = [self._probe("before") for _ in range(3)]
        during = [self._probe("during", ok=False) for _ in range(5)]
        after = [self._probe("after") for _ in range(2)]
        text = tt_loic._verdict(baseline, during, after)
        assert "NOT reachable" in text
        assert "recovered after the flood stopped" in text

    def test_partial_degradation(self):
        during = [self._probe("during"), self._probe("during", ok=False)]
        text = tt_loic._verdict([self._probe("before")], during, [])
        assert "degraded" in text

    def test_latency_ratio_uses_medians(self):
        # one warm-up outlier must not distort the before/during comparison
        baseline = [
            self._probe("before", relay_ms=40.0),
            self._probe("before", relay_ms=0.2),
            self._probe("before", relay_ms=0.2),
        ]
        during = [
            self._probe("during", relay_ms=0.5),
            self._probe("during", relay_ms=0.6),
            self._probe("during", relay_ms=0.7),
        ]
        text = tt_loic._verdict(baseline, during, [])
        assert "0.2 ms before" in text
        assert "(3.0x)" in text


def test_mb_formatting():
    assert tt_loic._mb(512) == "512 B"
    assert tt_loic._mb(2048) == "2.0 KB"
    assert tt_loic._mb(3 * (1 << 20)) == "3.0 MB"


# --------------------------------------------------------------------------- #
# ServiceProbe: a missing probe channel must fall back to the root channel,
# not wedge every probe for a configuration mismatch unrelated to the flood.
# --------------------------------------------------------------------------- #

class _FakeSdk:
    class ClientEvent:
        CLIENTEVENT_CMD_USER_TEXTMSG = 17


class FakeProbeSession:
    """Stands in for TeamTalkSession in the probe-fallback tests."""

    instances = []

    def __init__(self, config):
        self.config = config
        self.client = self  # the probe reads session.client.*
        self.sdk = _FakeSdk()
        self.opened = 0
        self.closed = False
        type(self).instances.append(self)

    def open(self):
        self.opened += 1
        if self.config.channel_path == "/NoSuchChannel":
            # open() joins the configured channel only after connect+login,
            # so a missing channel fails exactly this late in the real SDK.
            raise TeamTalkError(
                f"TeamTalk channel path was not found: {self.config.channel_path}"
            )

    def getRootChannelID(self):
        return 1

    def getMyUserID(self):
        return 42

    def close(self):
        self.closed = True


@pytest.fixture()
def fake_sessions(monkeypatch):
    FakeProbeSession.instances = []
    monkeypatch.setattr(tt_loic, "TeamTalkSession", FakeProbeSession)
    return FakeProbeSession.instances


class TestProbeRootFallback:
    def test_missing_channel_falls_back_to_root(self, fake_sessions, capsys):
        probe = tt_loic.ServiceProbe(namespace(probe_channel="/NoSuchChannel"))
        probe.start()

        assert probe.logged_in
        assert probe.fell_back_to_root
        assert probe._sender_id == 42
        # sender, sender-rebuilt-in-root, receiver
        assert len(fake_sessions) == 3
        first, second, receiver = fake_sessions
        assert first.closed, "the half-open session must be released"
        assert second.config.channel_id == 1
        assert second.config.channel_path is None
        assert receiver.config.channel_id == 1
        assert receiver.config.nickname == "loic-listener"
        out = capsys.readouterr().out
        assert "not found on this server" in out
        assert "root channel instead" in out

    def test_existing_channel_needs_no_fallback(self, fake_sessions, capsys):
        probe = tt_loic.ServiceProbe(namespace())
        probe.start()

        assert probe.logged_in
        assert not probe.fell_back_to_root
        # sender and receiver, no rebuild
        assert len(fake_sessions) == 2
        assert "root channel" not in capsys.readouterr().out

    def test_relogin_after_drop_lands_in_root(self, fake_sessions):
        probe = tt_loic.ServiceProbe(namespace(probe_channel="/NoSuchChannel"))
        probe.start()

        # the flood knocked the sender out and its session died with it
        probe.logged_in = False
        probe.session = None
        probe._try_login()

        assert probe.logged_in
        assert fake_sessions[-1].config.channel_id == 1
        assert fake_sessions[-1].config.channel_path is None

    def test_login_failure_does_not_fake_a_fallback(self, fake_sessions, capsys, monkeypatch):
        # a probe account the server rejects is a real login failure: the
        # probe must report it, not paper over it with a channel fallback
        class Rejected(FakeProbeSession):
            def open(self):
                self.opened += 1
                raise TeamTalkError("TeamTalk rejected the connection request")

        monkeypatch.setattr(tt_loic, "TeamTalkSession", Rejected)
        probe = tt_loic.ServiceProbe(namespace())
        probe.start()

        assert not probe.logged_in
        assert probe.session is None
        assert probe.listener is None
        assert "SDK login unavailable" in capsys.readouterr().out