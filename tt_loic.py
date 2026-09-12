#!/usr/bin/env python3
"""LOIC-style TCP/UDP flood modes, restricted to servers on this machine.

LOIC (Low Orbit Ion Cannon) is a Windows GUI app, which is unusable with a
screen reader; this reproduces its two relevant flood modes -- a TCP
junk-data flood and a UDP junk-datagram flood -- as a plain CLI, then
measures what the flood actually does to a TeamTalk server running locally:
how connection latency, login, and message round-trips behave before,
during, and after the flood, and whether the server stays in service.

Local-only by construction: the target must resolve to an address on this
machine (loopback or one of its own interfaces), the run length is capped,
and ``--confirm`` is required on the flag path.  Point this only at a server
you run on this machine.

Running with no arguments opens the same interactive prompts as the rest of
the suite — server host, TCP port, UDP port, and account, all defaulting from
``teamtalk.env`` — then applies the local-only gate and asks one go/no-go
question that defaults to No before the flood starts.  The account may be
left blank: a blank username and password log the probe in anonymously,
which servers without user accounts accept.
"""

from __future__ import annotations

import argparse
import dataclasses
import ipaddress
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

from tt_teamtalk import (
    ConnectionConfig,
    TeamTalkConfigurationError,
    TeamTalkError,
    TeamTalkSession,
    comma_int,
    message_fields,
    print_tool_error,
    prompt_connection_config,
    prompt_yes_no,
    sdk_event,
    sdk_int,
)


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 10333
DEFAULT_THREADS = 8
DEFAULT_DURATION = 10.0
DEFAULT_TIMEOUT = 5.0
MAX_DURATION_SECONDS = 60
MAX_THREADS = 64
TCP_CHUNK_BYTES = 1024
UDP_DATAGRAM_BYTES = 512
JUNK_BUFFER_BYTES = 1 << 16
PROBE_ECHO_TIMEOUT = 2.0
BASELINE_PROBES = 3
AFTER_PROBES = 2
PROBE_CADENCE = 1.0  # seconds between probes taken during the flood


# --------------------------------------------------------------------------- #
# Local-target gate
# --------------------------------------------------------------------------- #

def _local_addresses() -> set[str]:
    """Addresses that belong to this machine (loopback plus its interfaces)."""

    addresses = {"127.0.0.1", "::1", "localhost"}
    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show"],
            capture_output=True, text=True, timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return addresses
    for line in result.stdout.splitlines():
        parts = line.split()
        # "<ifindex>: <ifname> inet 127.0.0.1/8 ..." -> the address is parts[3]
        if len(parts) >= 4 and parts[2] in ("inet", "inet6"):
            addresses.add(parts[3].split("/")[0])
    return addresses


def _assert_local_host(host: str) -> None:
    """Refuse any target that is not this machine."""

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as exc:
        raise TeamTalkConfigurationError(f"cannot resolve host {host!r}: {exc}")
    if not infos:
        raise TeamTalkConfigurationError(f"host {host!r} did not resolve")
    resolved = {str(info[4][0]) for info in infos}
    local = _local_addresses()
    for addr in resolved:
        try:
            parsed = ipaddress.ip_address(addr.split("%")[0])
        except ValueError:
            continue
        if parsed.is_loopback or addr in local:
            return
    raise TeamTalkConfigurationError(
        f"{host!r} resolves to {sorted(resolved)}, which is not this machine; "
        "this tool only floods servers running locally."
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="LOIC-style TCP/UDP flood test against a TeamTalk server "
        "running on this machine, with before/during/after service probes."
    )
    parser.add_argument(
        "--host", default=DEFAULT_HOST,
        help=f"flood target host; must be this machine (default: {DEFAULT_HOST})",
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
        help="flood mode: LOIC's TCP flood, UDP flood, or both (default: both)",
    )
    parser.add_argument(
        "--threads", type=comma_int, default=DEFAULT_THREADS,
        help=f"flood threads per mode, 1-{MAX_THREADS} "
        f"(default: {DEFAULT_THREADS})",
    )
    parser.add_argument(
        "--duration", type=float, default=DEFAULT_DURATION,
        help=f"flood length in seconds, capped at {MAX_DURATION_SECONDS} "
        f"(default: {DEFAULT_DURATION:g})",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT,
        help=f"socket timeout in seconds, like LOIC's timeout field "
        f"(default: {DEFAULT_TIMEOUT:g})",
    )
    parser.add_argument(
        "--probe-username", default=os.environ.get("TT_USERNAME", ""),
        help="SDK probe login; blank logs the probe in anonymously "
        "(default: TT_USERNAME or blank)",
    )
    parser.add_argument(
        "--probe-password", default=os.environ.get("TT_PASSWORD", ""),
        help="SDK probe password; blank for anonymous login "
        "(default: TT_PASSWORD or blank)",
    )
    parser.add_argument(
        "--probe-channel", default="/LoadTest",
        help="channel the probe joins and messages (default: /LoadTest)",
    )
    parser.add_argument(
        "--no-probe", action="store_true",
        help="skip the SDK service probes; flood only",
    )
    parser.add_argument(
        "--confirm", action="store_true",
        help="required: confirms this deliberate flood against the local target",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if not args.host.strip():
        raise TeamTalkConfigurationError("--host cannot be empty")
    if not 1 <= args.tcp_port <= 65535 or not 1 <= args.udp_port <= 65535:
        raise TeamTalkConfigurationError("ports must be between 1 and 65535")
    if not 1 <= args.threads <= MAX_THREADS:
        raise TeamTalkConfigurationError(
            f"--threads must be between 1 and {MAX_THREADS}"
        )
    if not 1 <= args.duration <= MAX_DURATION_SECONDS:
        raise TeamTalkConfigurationError(
            f"--duration must be between 1 and {MAX_DURATION_SECONDS}s"
        )
    if args.timeout <= 0:
        raise TeamTalkConfigurationError("--timeout must be greater than zero")
    if args.mode not in ("both", "tcp", "udp"):
        raise TeamTalkConfigurationError("--mode must be tcp, udp, or both")
    if not args.confirm:
        raise TeamTalkConfigurationError(
            "--confirm is required: this tool deliberately floods the target"
        )
    _assert_local_host(args.host.strip())


# --------------------------------------------------------------------------- #
# Flood
# --------------------------------------------------------------------------- #

@dataclass
class FloodStats:
    connections: int = 0
    bytes_sent: int = 0
    datagrams: int = 0
    errors: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add(self, **deltas: int) -> None:
        with self.lock:
            for name, delta in deltas.items():
                setattr(self, name, getattr(self, name) + delta)


def _tcp_flood(
    host: str, port: int, stats: FloodStats,
    stop_event: threading.Event, timeout: float,
) -> None:
    """LOIC TCP mode: open connections and stream junk until they break."""

    addr = (host, port)
    junk = os.urandom(JUNK_BUFFER_BYTES)
    while not stop_event.is_set():
        try:
            sock = socket.create_connection(addr, timeout=timeout)
            sock.settimeout(timeout)
        except OSError:
            stats.add(errors=1)
            if stop_event.is_set():
                return
            continue
        stats.add(connections=1)
        try:
            offset = 0
            while not stop_event.is_set():
                sock.sendall(junk[offset:offset + TCP_CHUNK_BYTES])
                stats.add(bytes_sent=TCP_CHUNK_BYTES)
                offset = (offset + TCP_CHUNK_BYTES) % (JUNK_BUFFER_BYTES - TCP_CHUNK_BYTES)
        except OSError:
            stats.add(errors=1)
        finally:
            try:
                sock.close()
            except OSError:
                pass


def _udp_flood(
    host: str, port: int, stats: FloodStats,
    stop_event: threading.Event, timeout: float,
) -> None:
    """LOIC UDP mode: fire junk datagrams as fast as the socket allows."""

    addr = (host, port)
    junk = os.urandom(JUNK_BUFFER_BYTES)
    datagram = junk[:UDP_DATAGRAM_BYTES]
    sock: Optional[socket.socket] = None
    try:
        while not stop_event.is_set():
            if sock is None:
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.settimeout(timeout)
            try:
                sock.sendto(datagram, addr)
                stats.add(datagrams=1)
            except OSError:
                stats.add(errors=1)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def _run_flood(
    args: argparse.Namespace, stop_event: threading.Event
) -> dict[str, FloodStats]:
    """Run the selected flood modes for --duration seconds. Returns per-mode stats."""

    host = args.host.strip()
    threads: list[threading.Thread] = []
    stats: dict[str, FloodStats] = {}

    def spawn(kind: str, worker) -> None:
        stats[kind] = FloodStats()
        for _ in range(args.threads):
            thread = threading.Thread(
                target=worker,
                name=f"loic-{kind}",
                daemon=True,
            )
            thread.start()
            threads.append(thread)

    if args.mode in ("both", "tcp"):
        spawn("tcp", lambda: _tcp_flood(
            host, args.tcp_port, stats["tcp"], stop_event, args.timeout,
        ))
    if args.mode in ("both", "udp"):
        spawn("udp", lambda: _udp_flood(
            host, args.udp_port, stats["udp"], stop_event, args.timeout,
        ))

    deadline = time.monotonic() + args.duration
    while time.monotonic() < deadline and not stop_event.is_set():
        time.sleep(0.1)
    stop_event.set()
    for thread in threads:
        thread.join(timeout=5.0)
    return stats


# --------------------------------------------------------------------------- #
# Service probes (what the flood does to a real client's experience)
# --------------------------------------------------------------------------- #

@dataclass
class ProbeResult:
    phase: str
    connect_ms: Optional[float] = None
    relay_ms: Optional[float] = None
    ok: bool = False
    note: str = ""

    def line(self, index: int) -> str:
        connect = (
            f"{self.connect_ms:.1f} ms" if self.connect_ms is not None else "failed"
        )
        relay = f"{self.relay_ms:.1f} ms" if self.relay_ms is not None else "no reply"
        suffix = f" — {self.note}" if self.note else ""
        status = "ok" if self.ok else "FAILED"
        return (
            f"[probe {index} {self.phase}] connect {connect}, message {relay} "
            f"({status}){suffix}"
        )


def _is_missing_channel(exc: Exception) -> bool:
    """True when ``exc`` means the configured probe channel does not exist."""

    return "channel path was not found" in str(exc).lower()


class ServiceProbe:
    """Two real SDK clients that keep measuring the server's responsiveness.

    A TeamTalk server relays a channel message to the other users in the
    channel but never back to its sender, so the probe runs a matched pair:
    a sender and a receiver, both logged in to the same channel.  For every
    probe it times a fresh TCP connect plus a message round trip (the
    receiver must see the marker).  If the flood knocks either client out,
    the next probe tries to log in again -- being unable to log in during
    the flood is itself a finding, not an error to hide.
    """

    def __init__(self, args: argparse.Namespace) -> None:
        self.host = args.host.strip()
        self.tcp_port = args.tcp_port
        self.text = f"loic-probe-{int(time.monotonic() * 1000) % 100000}"
        self.probe_channel = args.probe_channel
        self.config = ConnectionConfig(
            host=self.host,
            tcp_port=args.tcp_port,
            udp_port=args.udp_port,
            username=args.probe_username,
            password=args.probe_password,
            nickname="loic-probe",
            channel_path=args.probe_channel,
            channel_password="",
            accept_sdk_license=True,
        )
        # the receiver needs its own login: the server will not relay a
        # channel message back to the account that sent it
        self.listener_config = dataclasses.replace(
            self.config, nickname="loic-listener"
        )
        self.session: Optional[TeamTalkSession] = None   # sends the markers
        self.listener: Optional[TeamTalkSession] = None  # receives them
        self._sender_id = -1
        self._text_event: Optional[int] = None
        self.logged_in = False
        self.fell_back_to_root = False

    def _session_config(self, role: str) -> ConnectionConfig:
        return self.config if role == "sender" else self.listener_config

    def _open_session(
        self, session: Optional[TeamTalkSession], role: str
    ) -> TeamTalkSession:
        """Open (or re-open) one probe session and log in.

        When the configured probe channel does not exist on this server, the
        probe re-targets the root channel -- every TeamTalk server has one --
        and says so.  Without this, a configuration mismatch would wedge the
        probe: ``open()`` fails at the channel join *after* connecting, so
        the session stays connected and logged in, every later ``open()``
        early-returns past the join, and each probe dies on
        "the client is not in a channel".
        """

        if session is None:
            session = TeamTalkSession(self._session_config(role))
        try:
            session.open()
            return session
        except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
            if self.fell_back_to_root or not _is_missing_channel(exc):
                raise
        # The client connected and logged in before the join failed, so the
        # root channel's ID can be read from the half-open session.  Valid
        # channel IDs start at one, so clamp a zero to the root.
        root_id = max(1, sdk_int(session.client.getRootChannelID(), 1))
        self.fell_back_to_root = True
        print(
            f"[probe] channel {self.probe_channel!r} not found on this "
            "server; probing the root channel instead."
        )
        # Rebuild both probe configs around the root channel so both clients
        # -- and every later reconnect -- land there (open() joins the
        # configured channel).
        self.config = dataclasses.replace(
            self.config, channel_id=root_id, channel_path=None, channel_password="",
        )
        self.listener_config = dataclasses.replace(
            self.config, nickname="loic-listener"
        )
        try:
            session.close()
        except (TeamTalkError, TeamTalkConfigurationError, OSError):
            pass
        session = TeamTalkSession(self._session_config(role))
        session.open()
        return session

    def _ensure_session(
        self, session: Optional[TeamTalkSession], role: str
    ) -> Optional[TeamTalkSession]:
        """Open or re-open one probe session; None when it cannot log in."""

        try:
            return self._open_session(session, role)
        except (TeamTalkError, TeamTalkConfigurationError, OSError):
            if session is not None:
                try:
                    session.close()
                except (TeamTalkError, TeamTalkConfigurationError, OSError):
                    pass
            return None

    def _refresh_ids(self) -> None:
        """Read the sender's user ID and the receiver's text-message event."""

        self._sender_id = -1
        self._text_event = None
        if self.session is not None:
            self._sender_id = sdk_int(self.session.client.getMyUserID(), -1)
        if self.listener is not None:
            self._text_event = sdk_event(
                self.listener.sdk, "CLIENTEVENT_CMD_USER_TEXTMSG"
            )

    def start(self) -> None:
        self.session = self._ensure_session(self.session, "sender")
        self.listener = self._ensure_session(self.listener, "listener")
        self._refresh_ids()
        self.logged_in = bool(self.session and self.listener)
        if not self.logged_in:
            print(
                "[probe] SDK login unavailable; measuring connect latency only."
            )

    def _try_login(self) -> None:
        """Attempt fresh logins for the next probe after a drop."""

        self.session = self._ensure_session(self.session, "sender")
        self.listener = self._ensure_session(self.listener, "listener")
        self._refresh_ids()
        self.logged_in = bool(self.session and self.listener)

    def probe(self, phase: str) -> ProbeResult:
        result = ProbeResult(phase=phase)

        # 1. raw TCP connect: measures the accept backlog, not the SDK stack.
        start = time.monotonic()
        try:
            sock = socket.create_connection(
                (self.host, self.tcp_port), timeout=PROBE_ECHO_TIMEOUT
            )
            sock.close()
            result.connect_ms = (time.monotonic() - start) * 1000.0
        except OSError as exc:
            result.note = f"connect refused ({exc.__class__.__name__})"
            return result

        # 2. message round trip: sender -> server -> receiver.
        if not self.logged_in:
            self._try_login()
        if not self.logged_in:
            result.note = "login failed during flood"
            return result

        start = time.monotonic()
        marker = f"{self.text}-{time.monotonic_ns() % 1000000}"
        try:
            channel_id = self.session.current_channel_id()
            self.session.send_channel_message(marker, channel_id)
        except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
            self.logged_in = False
            result.note = f"send failed ({exc.__class__.__name__})"
            return result

        deadline = start + PROBE_ECHO_TIMEOUT
        try:
            while time.monotonic() < deadline:
                message = self.listener.poll(
                    max(1, int((deadline - time.monotonic()) * 1000))
                )
                if self.listener.is_connection_failure(message):
                    self.logged_in = False
                    result.note = "server dropped the receiver connection"
                    return result
                if sdk_int(getattr(message, "nClientEvent", 0)) != self._text_event:
                    continue
                fields = message_fields(getattr(message, "textmessage", None))
                if (
                    fields["from_user_id"] == self._sender_id
                    and fields["text"] == marker
                    and fields["channel_id"] == channel_id
                ):
                    result.relay_ms = (time.monotonic() - start) * 1000.0
                    result.ok = True
                    return result
        except (TeamTalkError, TeamTalkConfigurationError, OSError) as exc:
            self.logged_in = False
            result.note = f"poll failed ({exc.__class__.__name__})"
            return result
        result.note = "message round trip timed out"
        return result

    def close(self) -> None:
        sessions = (self.session, self.listener)
        self.session = None
        self.listener = None
        for session in sessions:
            if session is None:
                continue
            try:
                session.close()
            except (TeamTalkError, OSError):
                pass


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #

def _mb(count: int) -> str:
    if count >= (1 << 20):
        return f"{count / (1 << 20):.1f} MB"
    if count >= (1 << 10):
        return f"{count / (1 << 10):.1f} KB"
    return f"{count} B"


def _phase_summary(probes: Sequence[ProbeResult]) -> str:
    if not probes:
        return "no samples"
    connects = [p.connect_ms for p in probes if p.connect_ms is not None]
    relays = [p.relay_ms for p in probes if p.relay_ms is not None]
    ok = sum(1 for p in probes if p.ok)
    connect_text = (
        f"connect avg {sum(connects) / len(connects):.1f} ms"
        if connects else "connect failed"
    )
    if relays:
        relay_text = (
            f"message avg {sum(relays) / len(relays):.1f} ms, worst {max(relays):.1f} ms"
        )
    else:
        relay_text = "no message round trips"
    return f"{connect_text}; {relay_text}; {ok}/{len(probes)} probes fully succeeded"


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _verdict(
    baseline: Sequence[ProbeResult],
    during: Sequence[ProbeResult],
    after: Sequence[ProbeResult],
) -> str:
    if not during:
        return "no during-flood samples were taken."
    ok = sum(1 for p in during if p.ok)
    base_relays = [p.relay_ms for p in baseline if p.relay_ms is not None]
    during_relays = [p.relay_ms for p in during if p.relay_ms is not None]

    if ok == len(during):
        verdict = (
            "the server stayed fully in service during the flood "
            f"({ok}/{len(during)} probes succeeded)"
        )
    elif ok == 0:
        verdict = (
            f"the server was NOT reachable for any of the {len(during)} probes "
            "during the flood"
        )
        if after:
            recovered = all(p.ok for p in after)
            verdict += " and " + (
                "recovered after the flood stopped." if recovered
                else "did NOT fully recover after the flood stopped."
            )
        return verdict
    else:
        verdict = (
            f"the server degraded but stayed partly in service during the "
            f"flood ({ok}/{len(during)} probes fully succeeded)"
        )
    if base_relays and during_relays:
        # Medians, not means: the first probe of a run pays one-time warm-up
        # cost (lazy SDK/audio init on the receiver), which would otherwise
        # dominate a 3-sample baseline average.
        base_mid = _median(base_relays)
        during_mid = _median(during_relays)
        ratio = during_mid / base_mid if base_mid > 0 else float("inf")
        verdict += (
            f"; message latency went from {base_mid:.1f} ms before to "
            f"{during_mid:.1f} ms during the flood ({ratio:.1f}x)"
        )
    return verdict + "."


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #

def run(args: argparse.Namespace) -> int:
    host = args.host.strip()
    validate_args(args)

    probe: Optional[ServiceProbe] = None
    baseline: list[ProbeResult] = []
    during: list[ProbeResult] = []
    after: list[ProbeResult] = []

    if args.no_probe:
        print("[probe] skipped (--no-probe).")
    else:
        probe = ServiceProbe(args)
        probe.start()
        for index in range(BASELINE_PROBES):
            result = probe.probe("before")
            baseline.append(result)
            print(result.line(index + 1))
            time.sleep(0.2)
        if baseline and all(p.connect_ms is None for p in baseline):
            probe.close()
            raise TeamTalkError(
                f"nothing is listening on {host}:{args.tcp_port}; "
                "start the local TeamTalk server first."
            )
        print(
            "Baseline: " + _phase_summary(baseline) + "."
            if baseline else "Baseline: no samples."
        )

    modes_text = {
        "both": f"TCP {args.tcp_port} + UDP {args.udp_port}",
        "tcp": f"TCP {args.tcp_port}",
        "udp": f"UDP {args.udp_port}",
    }[args.mode]
    print(
        f"Flooding {host} ({modes_text}) with {args.threads} thread(s) per mode "
        f"for {args.duration:g}s. Ctrl+C stops early."
    )
    stop_event = threading.Event()
    flood_stats: dict[str, FloodStats] = {}
    flood_thread = threading.Thread(
        target=lambda: flood_stats.update(_run_flood(args, stop_event)),
        daemon=True,
        name="loic-run",
    )
    try:
        flood_thread.start()
        if probe is not None:
            index = 0
            while flood_thread.is_alive():
                index += 1
                result = probe.probe("during")
                during.append(result)
                print(result.line(index))
                # wait ~1 s, but wake early if the flood ends
                waited = 0.0
                while waited < PROBE_CADENCE and flood_thread.is_alive():
                    time.sleep(0.1)
                    waited += 0.1
        else:
            while flood_thread.is_alive():
                time.sleep(0.1)
        flood_thread.join(timeout=5.0)
        stop_event.set()

        if probe is not None:
            for index in range(AFTER_PROBES):
                result = probe.probe("after")
                after.append(result)
                print(result.line(index + 1))
                time.sleep(0.2)
    except KeyboardInterrupt:
        stop_event.set()
        print("Interrupted; stopping the flood.")
    finally:
        stop_event.set()
        flood_thread.join(timeout=10.0)
        if probe is not None:
            probe.close()

    print("Flood stopped. Totals:")
    for kind in ("tcp", "udp"):
        stats = flood_stats.get(kind)
        if stats is None:
            continue
        with stats.lock:
            if kind == "tcp":
                print(
                    f"  TCP: {stats.connections} connection(s), "
                    f"{_mb(stats.bytes_sent)} of junk, {stats.errors} error(s)."
                )
            else:
                print(
                    f"  UDP: {stats.datagrams:,} datagram(s), "
                    f"{_mb(stats.datagrams * UDP_DATAGRAM_BYTES)} of junk, "
                    f"{stats.errors} error(s)."
                )

    if baseline or during or after:
        print("Service impact:")
        if baseline:
            print(f"  before: {_phase_summary(baseline)}")
        if during:
            print(f"  during: {_phase_summary(during)}")
        if after:
            print(f"  after:  {_phase_summary(after)}")
        print("Verdict: " + _verdict(baseline, during, after))
    return 0


def interactive_run() -> int:
    """No-arguments path: the suite's shared prompts, then one go/no-go.

    Same interface as the other tools: ``prompt_connection_config`` asks for
    the server host, TCP port, UDP port, and account (Enter accepts the
    ``teamtalk.env`` defaults), the local-only gate runs before anything
    else, and one question that defaults to No starts the flood.  The
    account answers become the probe login, exactly as ``--probe-username``
    / ``--probe-password`` do on the flag path.
    """
    config = prompt_connection_config(channel_required=False)
    # Gate first: a target that is not this machine is refused before the
    # go/no-go question so the operator is never asked to confirm a run that
    # cannot proceed.  run() re-checks it either way.
    _assert_local_host(config.host)
    if not prompt_yes_no(
        f"Flood {config.host} (TCP + UDP, {DEFAULT_THREADS} thread(s) per "
        f"mode, {DEFAULT_DURATION:g}s)?",
        False,
    ):
        print("Flood cancelled.")
        return 0
    # The prompt above is this run's confirmation; validate_args inside run()
    # re-checks the local-only assert, the confirm requirement, and every bound.
    args = argparse.Namespace(
        host=config.host,
        tcp_port=config.tcp_port,
        udp_port=config.udp_port,
        mode="both",
        threads=DEFAULT_THREADS,
        duration=DEFAULT_DURATION,
        timeout=DEFAULT_TIMEOUT,
        probe_username=config.username,
        probe_password=config.password,
        probe_channel=os.environ.get("TT_CHANNEL_PATH", "").strip() or "/LoadTest",
        no_probe=False,
        confirm=True,
    )
    return run(args)


def main(argv: Optional[Sequence[str]] = None) -> int:
    actual_argv = list(sys.argv[1:] if argv is None else argv)
    try:
        if not actual_argv:
            return interactive_run()
        return run(build_parser().parse_args(actual_argv))
    except (TeamTalkConfigurationError, TeamTalkError, OSError) as exc:
        return print_tool_error(exc)
    except (EOFError, KeyboardInterrupt):
        print("Interrupted.")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())