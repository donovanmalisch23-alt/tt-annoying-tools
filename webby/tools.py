"""The live tool registry: panel parameters -> the repository's CLI flags.

One place, next to the scripts themselves, decides how a request from the page
becomes an argument vector. The flag names below were taken from each script's
own ``build_parser()`` output, and the tools stay the authority on behaviour:
the bridge only translates, gates, and streams.

Credentials are never placed on the command line: the connection password and
username travel in the child's environment (``TT_PASSWORD`` / ``TT_USERNAME``),
which is what the CLI recommends and keeps argv safe to display.
"""

from __future__ import annotations

import ipaddress
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .config import BridgeConfig
from .whitelist import WhitelistFile, normalize as normalize_host


class ToolRequestError(ValueError):
    """Raised for a request the bridge will not translate."""


@dataclass(frozen=True)
class LiveTool:
    id: str
    label: str
    script: str
    requires_confirm: bool
    requires_whitelist: bool
    local_only: bool
    accepts_connection: bool
    accepts_sdk: bool
    build: Callable[[dict[str, Any]], list[str]]
    ignored: tuple[str, ...] = field(default=())
    note: str = ""


# --- helpers --------------------------------------------------------------- #


def _text(params: dict[str, Any], key: str, default: str = "") -> str:
    value = params.get(key, default)
    if value is None:
        return default
    return str(value)


def _number(params: dict[str, Any], key: str, default: float) -> float:
    raw = params.get(key)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ToolRequestError(f"{key} must be a number, got {raw!r}") from None


def _integer(params: dict[str, Any], key: str, default: int) -> int:
    return int(_number(params, key, float(default)))


def _truthy(params: dict[str, Any], key: str, default: bool = False) -> bool:
    raw = params.get(key)
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "enabled"}


def _names(params: dict[str, Any], key: str) -> list[str]:
    raw = params.get(key)
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        values = [str(item) for item in raw]
    else:
        values = str(raw).replace("\n", ",").split(",")
    cleaned = [item.strip() for item in values]
    return [item for item in cleaned if item]


def _seconds_from_ms(params: dict[str, Any], key: str, default_ms: float) -> float:
    return _number(params, key, default_ms) / 1000.0


# --- argument builders ----------------------------------------------------- #


def _message_sender(params: dict[str, Any]) -> list[str]:
    args: list[str] = ["--target", _text(params, "target", "channel") or "channel"]
    message = _text(params, "message")
    if message:
        args += ["--message", message]
    for name in _names(params, "users"):
        args += ["--user", name]
    args += ["--count", str(_integer(params, "count", 3))]
    args += ["--interval", f"{_seconds_from_ms(params, 'interval_ms', 50):g}"]
    args += ["--wait", f"{_number(params, 'wait', 0.0):g}"]
    return args


def _cycles_tool(params: dict[str, Any], *, wait_default: float) -> list[str]:
    return [
        "--cycles",
        str(_integer(params, "cycles", 5)),
        "--interval",
        f"{_seconds_from_ms(params, 'interval_ms', 200):g}",
        "--wait",
        f"{_number(params, 'wait', wait_default):g}",
    ]


def _login_cycles(params: dict[str, Any]) -> list[str]:
    return _cycles_tool(params, wait_default=0.0)


def _leave_join(params: dict[str, Any]) -> list[str]:
    return _cycles_tool(params, wait_default=50.0)


def _idle_bots(params: dict[str, Any]) -> list[str]:
    args = ["--count", str(_integer(params, "count", 4))]
    if _truthy(params, "dry_run"):
        args.append("--dry-run")
    return args


def _response_bot(params: dict[str, Any]) -> list[str]:
    args = [
        "--trigger",
        _text(params, "trigger", "!hello") or "!hello",
        "--response",
        _text(params, "response", "Hi {username}, thanks for your message!"),
    ]
    for name in _names(params, "allow_users"):
        args += ["--allow-user", name]
    if _truthy(params, "allow_all"):
        args.append("--allow-all")
    args += ["--cooldown", f"{_number(params, 'cooldown', 30.0):g}"]
    args += ["--max-responses", str(_integer(params, "max_responses", 100))]
    return args


def _suite(params: dict[str, Any]) -> list[str]:
    args: list[str] = []
    if _truthy(params, "all_channels"):
        args.append("--all-channels")
    if _truthy(params, "all_users"):
        args.append("--all-users")
    for name in _names(params, "users"):
        args += ["--user", name]
    channel_message = _text(params, "channel_message")
    if channel_message:
        args += ["--channel-message", channel_message]
    private_message = _text(params, "private_message")
    if private_message:
        args += ["--private-message", private_message]
    args += ["--message-count", str(_integer(params, "message_count", 1))]
    args += ["--join-leave-cycles", str(_integer(params, "join_leave_cycles", 0))]
    args += ["--login-cycles", str(_integer(params, "login_cycles", 0))]
    args += ["--interval", f"{_number(params, 'interval', 0.2):g}"]
    args += ["--sweep-interval", f"{_number(params, 'sweep_interval', 0.5):g}"]
    if _truthy(params, "concurrent"):
        args.append("--concurrent")
    if _truthy(params, "bot_per_channel"):
        args.append("--bot-per-channel")
    if _truthy(params, "bot_per_user"):
        args.append("--bot-per-user")
    args += ["--churn-bots", str(_integer(params, "churn_bots", 0))]
    args += ["--churn-cycles", str(_integer(params, "churn_cycles", 5))]
    if _truthy(params, "dry_run"):
        args.append("--dry-run")
    return args


def _loic(params: dict[str, Any]) -> list[str]:
    args = [
        "--mode",
        _text(params, "mode", "both") or "both",
        "--threads",
        str(_integer(params, "threads", 8)),
        "--duration",
        f"{_number(params, 'duration', 10.0):g}",
        "--probe-channel",
        _text(params, "probe_channel", "/Lobby") or "/Lobby",
    ]
    if not _truthy(params, "probe", True):
        args.append("--no-probe")
    probe_user = _text(params, "probe_username")
    if probe_user:
        args += ["--probe-username", probe_user]
    probe_password = _text(params, "probe_password")
    if probe_password:
        args += ["--probe-password", probe_password]
    return args


def _ramp(params: dict[str, Any]) -> list[str]:
    args = [
        "--start-threads",
        str(_integer(params, "start_threads", 1)),
        "--ramp-factor",
        f"{_number(params, 'ramp_factor', 2):g}",
        "--max-threads",
        str(_integer(params, "max_threads", 64)),
        "--stage-duration",
        f"{_number(params, 'stage_duration', 6.0):g}",
        "--mode",
        _text(params, "mode", "both") or "both",
        "--probe-channel",
        _text(params, "probe_channel", "/Lobby") or "/Lobby",
    ]
    if _truthy(params, "dry_run"):
        args.append("--dry-run")
    return args


LIVE_TOOLS: tuple[LiveTool, ...] = (
    LiveTool(
        id="message-sender",
        label="Message sender",
        script="tt_message_spammer.py",
        requires_confirm=False,
        requires_whitelist=False,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_message_sender,
    ),
    LiveTool(
        id="login-cycles",
        label="Login / logout cycles",
        script="tt_spammer.py",
        requires_confirm=False,
        requires_whitelist=False,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_login_cycles,
    ),
    LiveTool(
        id="leave-join",
        label="Channel leave / join",
        script="tt_leave_join_spammer.py",
        requires_confirm=False,
        requires_whitelist=False,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_leave_join,
    ),
    LiveTool(
        id="response-bot",
        label="Response bot",
        script="ttbot_the_offender.py",
        requires_confirm=False,
        requires_whitelist=False,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_response_bot,
    ),
    LiveTool(
        id="idle-bots",
        label="Idle bots",
        script="tt_concurrent_bots.py",
        requires_confirm=True,
        requires_whitelist=True,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_idle_bots,
        ignored=("start_delay_ms", "connect_attempts"),
        note="The CLI spaces its own launches; the panel's launch-delay fields do not apply.",
    ),
    LiveTool(
        id="suite",
        label="Combined suite",
        script="tt_suite.py",
        requires_confirm=True,
        requires_whitelist=True,
        local_only=False,
        accepts_connection=True,
        accepts_sdk=True,
        build=_suite,
    ),
    LiveTool(
        id="loic",
        label="Local flood test",
        script="tt_loic.py",
        requires_confirm=True,
        requires_whitelist=False,
        local_only=True,
        accepts_connection=False,
        accepts_sdk=False,
        build=_loic,
        note="Local-only by construction: the CLI refuses any target that is not this machine.",
    ),
    LiveTool(
        id="ramp",
        label="Ramp / breaking point",
        script="tt_ramp.py",
        requires_confirm=True,
        requires_whitelist=True,
        local_only=False,
        accepts_connection=False,
        accepts_sdk=False,
        build=_ramp,
        note="Gated by the allowlist file; the bridge passes its exact path to the tool.",
    ),
)

TOOLS_BY_ID: dict[str, LiveTool] = {tool.id: tool for tool in LIVE_TOOLS}

#: Values that must never be echoed back in the displayed argument vector.
SENSITIVE_FLAGS = ("--password", "--probe-password", "--license-key")


def tool_catalog() -> list[dict[str, Any]]:
    return [
        {
            "id": tool.id,
            "label": tool.label,
            "script": tool.script,
            "requires_confirm": tool.requires_confirm,
            "requires_whitelist": tool.requires_whitelist,
            "local_only": tool.local_only,
            "accepts_connection": tool.accepts_connection,
            "ignored": list(tool.ignored),
            "note": tool.note,
        }
        for tool in LIVE_TOOLS
    ]


def _connection_args(connection: dict[str, Any]) -> list[str]:
    host = str(connection.get("host") or "").strip()
    if not host:
        raise ToolRequestError("a server host is required")
    args = [
        "--host",
        host,
        "--tcp-port",
        str(int(connection.get("tcp_port") or 10333)),
        "--udp-port",
        str(int(connection.get("udp_port") or 10333)),
    ]
    username = str(connection.get("username") or "")
    if username:
        args += ["--username", username]
    nickname = str(connection.get("nickname") or "")
    if nickname:
        args += ["--nickname", nickname]
    client_name = str(connection.get("client_name") or "")
    if client_name:
        args += ["--client-name", client_name]
    channel_path = str(connection.get("channel_path") or "")
    if channel_path:
        args += ["--channel-path", channel_path]
    channel_id = connection.get("channel_id")
    if channel_id not in (None, ""):
        args += ["--channel-id", str(int(channel_id))]
    channel_password = str(connection.get("channel_password") or "")
    if channel_password:
        args += ["--channel-password", channel_password]
    if connection.get("encrypted"):
        args.append("--encrypted")
    timeout = connection.get("timeout")
    if timeout not in (None, ""):
        args += ["--timeout", f"{float(timeout):g}"]
    reconnect = connection.get("reconnect_delay")
    if reconnect not in (None, ""):
        args += ["--reconnect-delay", f"{float(reconnect):g}"]
    if connection.get("kick_resistance") is False:
        args.append("--no-kick-resistance")
    return args


def build_request(
    config: BridgeConfig,
    tool_id: str,
    params: dict[str, Any],
    connection: dict[str, Any],
    *,
    confirm: bool,
) -> tuple[list[str], dict[str, str], list[str]]:
    """Return ``(argv, child_env, warnings)`` for one run request.

    Raises :class:`ToolRequestError` for anything the bridge refuses to run.
    """
    tool = TOOLS_BY_ID.get(tool_id)
    if tool is None:
        raise ToolRequestError(f"unknown tool {tool_id!r}")

    script = config.tool_script(tool.script)
    if not script.is_file():
        raise ToolRequestError(f"{tool.script} is missing from {config.repo_root}")

    warnings: list[str] = []
    if tool.requires_confirm and not confirm:
        raise ToolRequestError(f"{tool.label} requires an explicit confirmation (confirm: true)")

    args: list[str] = [config.python, "-u", str(script)]
    if tool.accepts_connection:
        args += _connection_args(connection)
    else:
        host = str(connection.get("host") or "").strip()
        if not host:
            raise ToolRequestError("a server host is required")
        args += [
            "--host",
            host,
            "--tcp-port",
            str(int(connection.get("tcp_port") or 10333)),
            "--udp-port",
            str(int(connection.get("udp_port") or 10333)),
        ]

    if tool.requires_whitelist or tool.id == "ramp":
        args += ["--whitelist", str(config.whitelist_path)]
    if tool.accepts_sdk:
        if config.sdk_python.is_file():
            args += ["--sdk-python", str(config.sdk_python)]
        if config.sdk_library.is_file():
            args += ["--sdk-library", str(config.sdk_library)]

    args += tool.build(params)

    if tool.requires_confirm:
        args.append("--confirm")
    # A child process has no usable stdin, so the SDK's first-run license prompt
    # would fail on EOF. Skip it when the license was already accepted here.
    if tool.accepts_sdk and config.sdk_license_accepted:
        args.append("--accept-sdk-license")
    for ignored in tool.ignored:
        if params.get(ignored):
            warnings.append(f"{ignored} is ignored in live mode")

    env = {
        "PYTHONUNBUFFERED": "1",
        "TT_ACCEPT_SDK_LICENSE": "1" if config.sdk_license_accepted else "0",
        "TT_USERNAME": str(connection.get("username") or ""),
        "TT_PASSWORD": str(connection.get("password") or ""),
        "TT_HOST": str(connection.get("host") or ""),
    }
    env.update(config.extra_env)
    return args, env, warnings


def redact(argv: list[str]) -> list[str]:
    """A copy of argv safe to log and display."""
    safe: list[str] = []
    hide_next = False
    for token in argv:
        if hide_next:
            safe.append("***")
            hide_next = False
            continue
        safe.append(token)
        if token in SENSITIVE_FLAGS:
            hide_next = True
    return safe


def target_host(argv: list[str]) -> str:
    try:
        return argv[argv.index("--host") + 1]
    except (ValueError, IndexError):
        return ""


def script_name(argv: list[str]) -> str:
    for token in argv:
        if token.endswith(".py"):
            return Path(token).name
    return ""


# --- gates ----------------------------------------------------------------- #
#
# These mirror the checks the tools make for themselves. The tools remain the
# authority — they re-check whatever the bridge forwards — but refusing here
# means the operator gets a sentence instead of a process that dies on start.


def _local_addresses() -> set[str]:
    """Addresses belonging to this machine (loopback plus its interfaces)."""
    addresses = {"127.0.0.1", "::1", "localhost"}
    try:
        result = subprocess.run(
            ["ip", "-o", "addr", "show"], capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return addresses
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in ("inet", "inet6"):
            addresses.add(parts[3].split("/")[0])
    return addresses


def _is_local_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    resolved = {str(info[4][0]) for info in infos}
    local = _local_addresses()
    for address in resolved:
        try:
            parsed = ipaddress.ip_address(address.split("%")[0])
        except ValueError:
            continue
        if parsed.is_loopback or address in local:
            return True
    return False


def require_allowed_ok(
    config: BridgeConfig,
    tool_id: str,
    argv: list[str],
    whitelist: WhitelistFile,
    warnings: list[str],
) -> None:
    """Apply the allowlist and local-only gates for one request, or raise."""
    tool = TOOLS_BY_ID.get(tool_id)
    if tool is None:
        raise ToolRequestError(f"unknown tool {tool_id!r}")
    host = target_host(argv)

    if tool.local_only and not _is_local_host(host):
        raise ToolRequestError(
            f"{tool.label} only runs against this machine, and '{host}' is not it. "
            "Point it at 127.0.0.1 (or the local server's own address)."
        )

    if tool.requires_whitelist or tool.id == "ramp":
        wanted = normalize_host(host)
        if not whitelist.entries:
            raise ToolRequestError(
                f"the allowlist ({whitelist.path}) is empty; sign in to the admin panel "
                "and add the server you are authorised to test."
            )
        if wanted not in whitelist.entries:
            raise ToolRequestError(
                f"'{host}' is not in {whitelist.path}; add it in the admin panel first. "
                "Gated tools refuse any host that is missing from the allowlist."
            )
        warnings.append(f"allowlisted target: {host}")
    return None
