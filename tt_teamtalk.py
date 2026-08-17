#!/usr/bin/env python3
"""Small, synchronous helpers for the official TeamTalk 5 Python SDK.

The official SDK ships ``TeamTalk5.py`` as a ctypes interface to
``libTeamTalk5.so``.  This module loads that interface lazily so that command
line help and argument validation still work on machines without the SDK.
"""

from __future__ import annotations

import argparse
import ctypes
import getpass
import importlib
import importlib.util
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional


TEAMTALK_SDK_DOWNLOAD = "https://bearware.dk/?page_id=419"
TEAMTALK_SDK_DOCS = "https://www.bearware.dk/teamtalksdk/v5.22a/docs/C-API/"

# Serializes the first SDK import across threads.  _import_sdk_from_file
# monkeypatches the process-global ctypes.cdll.LoadLibrary while importing
# the SDK module; without a lock, concurrent bots (tt_suite --concurrent) can
# race and leave ctypes.cdll.LoadLibrary pointing at a stale wrapper, breaking
# ctypes for the whole process.  An RLock lets _load_configured_sdk hold the
# same lock around its env-var mutation while re-entering load_teamtalk_sdk.
_SDK_LOAD_LOCK = threading.RLock()


class TeamTalkError(RuntimeError):
    """Base error raised by the Linux TeamTalk tools."""


class TeamTalkConfigurationError(TeamTalkError):
    """Raised when a required connection or target setting is missing."""


class TeamTalkSDKError(TeamTalkError):
    """Raised when the official SDK cannot be loaded or reports an error."""


def sdk_text(value: Any) -> str:
    """Convert SDK byte arrays and ctypes values into readable text."""

    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).split(b"\0", 1)[0].decode("utf-8", "replace")

    raw_value = getattr(value, "value", None)
    if raw_value is not None and raw_value is not value:
        return sdk_text(raw_value)

    try:
        return bytes(value).split(b"\0", 1)[0].decode("utf-8", "replace")
    except (TypeError, ValueError):
        return str(value)


def sdk_int(value: Any, default: int = 0) -> int:
    """Convert a ctypes integer or a normal integer to ``int``."""

    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def sdk_string(sdk: Any, value: str) -> Any:
    """Encode a string using the official binding's platform-aware helper."""

    converter = getattr(sdk, "ttstr", None)
    return converter(value) if callable(converter) else value


def sdk_event(sdk: Any, name: str) -> Optional[int]:
    """Return an event constant while tolerating older SDK builds."""

    value = getattr(getattr(sdk, "ClientEvent", object()), name, None)
    return None if value is None else sdk_int(value)


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def config_dir() -> Path:
    """Directory for user-facing config files (whitelist.txt, teamtalk.env).

    When frozen by PyInstaller, a script's own ``__file__`` points into a
    temporary extraction directory (``_MEIPASS``) that is wiped after the run,
    so user config is resolved relative to the executable instead — the
    folder the binary is run from.  When not frozen, the script's directory
    is used as before.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _load_project_env() -> None:
    """Load local non-shell configuration without overriding exported values."""

    configured_file = os.environ.get("TT_ENV_FILE")
    env_path = (
        Path(configured_file).expanduser()
        if configured_file
        else config_dir() / "teamtalk.env"
    )
    if not env_path.is_file():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key.isidentifier():
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)


_load_project_env()


def _sdk_roots() -> list[Path]:
    roots: list[Path] = []
    for variable in ("TEAMTALK_SDK_PATH", "TEAMTALK_SDK_ROOT"):
        value = os.environ.get(variable)
        if value:
            roots.append(Path(value).expanduser())
    return roots


def _find_sdk_python() -> Optional[Path]:
    explicit = os.environ.get("TEAMTALK_SDK_PYTHON")
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_dir():
            path = path / "TeamTalk5.py"
        if path.is_file():
            return path.resolve()

    candidates: list[Path] = []
    for root in _sdk_roots():
        candidates.extend(
            (
                root / "Library" / "TeamTalkPy" / "TeamTalk5.py",
                root / "TeamTalkPy" / "TeamTalk5.py",
                root / "TeamTalk5.py",
            )
        )

    here = Path(__file__).resolve().parent
    # Vendored SDK shipped with this project (sdk/TeamTalk5.py).
    candidates.insert(0, here / "sdk" / "TeamTalk5.py")
    candidates.extend((here / "TeamTalk5.py", here / "TeamTalkPy" / "TeamTalk5.py"))

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _find_sdk_library(sdk_python: Optional[Path]) -> Optional[Path]:
    explicit = os.environ.get("TEAMTALK_SDK_LIBRARY") or os.environ.get(
        "TEAMTALK_LIBRARY"
    )
    if explicit:
        path = Path(explicit).expanduser()
        if path.is_file():
            return path.resolve()

    # The native library has platform-specific names: libTeamTalk5.so on
    # Linux, libTeamTalk5.dylib on macOS, and TeamTalk5.dll on Windows (the
    # Windows wrapper loads it as ``cdll.TeamTalk5``, without the "lib" prefix).
    stems = ("libTeamTalk5", "libTeamTalk5Pro", "TeamTalk5", "TeamTalk5Pro")
    exts = (".so", ".dll", ".dylib")

    dirs: list[Path] = []
    for root in _sdk_roots():
        dirs.extend((root / "Library" / "TeamTalk_DLL", root))
    here = Path(__file__).resolve().parent
    dirs.append(here / "sdk")
    if sdk_python is not None:
        dirs.extend((sdk_python.parent, sdk_python.parent.parent / "TeamTalk_DLL"))

    candidates: list[Path] = []
    for directory in dirs:
        for stem in stems:
            for ext in exts:
                candidates.append(directory / f"{stem}{ext}")

    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _import_sdk_from_file(path: Path, library: Optional[Path]) -> Any:
    """Import the SDK module, optionally resolving its shared library by path."""

    module_name = "TeamTalk5"
    spec = importlib.util.spec_from_file_location(module_name, str(path))
    if spec is None or spec.loader is None:
        raise TeamTalkSDKError(f"could not create an import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    previous_module = sys.modules.get(module_name)
    sys.modules[module_name] = module

    # On Windows the upstream wrapper loads the native library as
    # ``cdll.TeamTalk5`` (attribute access, which bypasses LoadLibrary) and
    # relies on ``os.add_dll_directory`` to locate ``TeamTalk5.dll``.  In a
    # frozen build that directory is derived from this module's path with a
    # literal "..\\TeamTalk_DLL" suffix; register the resolved, absolute DLL
    # directory here too so the load never depends on that ".." resolving.
    if sys.platform == "win32" and library is not None:
        dll_dir = str(Path(library).resolve().parent)
        try:
            if hasattr(os, "add_dll_directory") and os.path.isdir(dll_dir):
                os.add_dll_directory(dll_dir)
        except OSError:
            pass

    original_load_library = ctypes.cdll.LoadLibrary

    def load_library(name: Any) -> Any:
        name_text = os.fspath(name) if isinstance(name, os.PathLike) else str(name)
        base = os.path.basename(name_text)
        if library is not None and (
            base.startswith("libTeamTalk5") or base.startswith("TeamTalk5")
        ):
            return original_load_library(str(library))
        return original_load_library(name)

    ctypes.cdll.LoadLibrary = load_library  # type: ignore[method-assign]
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # pragma: no cover - exact exception is platform-specific
        if previous_module is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous_module
        detail = f" ({library})" if library else ""
        raise TeamTalkSDKError(
            f"could not import TeamTalk5.py{detail}: {exc}"
        ) from exc
    finally:
        ctypes.cdll.LoadLibrary = original_load_library  # type: ignore[method-assign]

    return module


def load_teamtalk_sdk() -> Any:
    """Load the official ``TeamTalk5`` Python interface on first use.

    Set ``TEAMTALK_SDK_PATH`` to the extracted SDK directory, or set
    ``TEAMTALK_SDK_PYTHON`` and ``TEAMTALK_SDK_LIBRARY`` explicitly.
    """

    with _SDK_LOAD_LOCK:
        existing = sys.modules.get("TeamTalk5")
        if existing is not None:
            return existing

        sdk_python = _find_sdk_python()
        sdk_library = _find_sdk_library(sdk_python)

        try:
            if sdk_python is not None:
                return _import_sdk_from_file(sdk_python, sdk_library)
            return importlib.import_module("TeamTalk5")
        except TeamTalkSDKError:
            raise
        except Exception as exc:  # pragma: no cover - exact exception is platform-specific
            raise TeamTalkSDKError(
                "TeamTalk5.py/libTeamTalk5.so was not found. Download the official "
                f"TeamTalk SDK from {TEAMTALK_SDK_DOWNLOAD}, extract it, then set "
                "TEAMTALK_SDK_PATH to its root directory."
            ) from exc


# --------------------------------------------------------------------------- #
# First-run TeamTalk 5 SDK license acceptance
# --------------------------------------------------------------------------- #
#
# The TeamTalk 5 SDK License.txt states that use of the SDK files is not
# permitted until the user has read and agreed to the license terms.  This
# gate enforces that on the first run: it prints the bundled license text (or a
# reference to it), prompts Y/N, and persists the decision in a marker file so
# subsequent runs skip the prompt.  ``TT_ACCEPT_SDK_LICENSE=1`` and the
# ``--accept-sdk-license`` flag pre-approve (for scripted / CI runs), and
# ``--decline-sdk-license`` / ``TT_ACCEPT_SDK_LICENSE=0`` explicitly decline.
_SDK_LICENSE_MARKER = ".tt-sdk-license-accepted"


def _vendored_license_path() -> Optional[Path]:
    """Path to the bundled TeamTalk 5 SDK ``License.txt``, if present."""

    here = Path(__file__).resolve().parent
    candidate = here / "sdk" / "License.txt"
    return candidate if candidate.is_file() else None


def _sdk_license_accepted_marker() -> Path:
    """Marker file recording that the user accepted the SDK license."""

    return config_dir() / _SDK_LICENSE_MARKER


def _sdk_license_decision() -> Optional[bool]:
    """Return an explicit pre-made decision from env vars, else ``None``."""

    value = os.environ.get("TT_ACCEPT_SDK_LICENSE")
    if value is None:
        return None
    return value.strip().lower() in {"1", "true", "yes", "on", "y"}


def _print_sdk_license_terms() -> None:
    """Print the TeamTalk 5 SDK license terms to the console."""

    license_path = _vendored_license_path()
    if license_path is not None:
        try:
            text = license_path.read_text(encoding="utf-8", errors="replace")
            bar = "=" * 78
            print(bar)
            print("TEAMTALK 5 SDK LICENSE AGREEMENT")
            print(bar)
            print(text)
            print(bar)
            return
        except OSError:
            pass
    # Fallback summary if the bundled file is unavailable.
    print(
        "Use of the TeamTalk 5 SDK is not permitted until you have read and "
        "agreed to the TeamTalk 5 SDK License Agreement from BearWare.dk. See "
        f"{TEAMTALK_SDK_DOWNLOAD} for the full terms."
    )


def ensure_sdk_license_accepted(*, pre_choice: Optional[bool] = None) -> None:
    """Prompt for (or honour a stored) acceptance of the SDK license.

    On the first run this prints the license terms and asks the user to agree
    (Y/N).  A ``Y`` answer persists the decision so later runs skip the prompt.
    An ``N`` answer (or an explicit decline) raises ``TeamTalkConfigurationError``
    and prevents the SDK from being used.  ``pre_choice`` lets a caller pass the
    parsed ``--accept-sdk-license`` / ``--decline-sdk-license`` decision so the
    flag works even when there is no interactive terminal (e.g. CI).
    """

    # Explicit flag / env decision always wins and is persisted either way.
    if pre_choice is None:
        pre_choice = _sdk_license_decision()

    if pre_choice is False:
        marker = _sdk_license_accepted_marker()
        if marker.is_file():
            try:
                marker.unlink()
            except OSError:
                pass
        raise TeamTalkConfigurationError(
            "TeamTalk 5 SDK license terms declined. The SDK will not be used. "
            "Re-run and accept the terms (Y) to continue, or remove the SDK."
        )

    marker = _sdk_license_accepted_marker()
    if marker.is_file():
        return  # already accepted on a previous run

    if pre_choice is True:
        try:
            marker.write_text(
                "accepted via TT_ACCEPT_SDK_LICENSE / --accept-sdk-license\n",
                encoding="utf-8",
            )
        except OSError:
            pass
        return

    # Interactive first-run prompt.
    _print_sdk_license_terms()
    print(
        "\nBy using the TeamTalk 5 SDK you agree to be bound by the terms and "
        "conditions stated above. Do you accept the binding terms of the "
        "TTSDK license? (Y/N)"
    )
    while True:
        try:
            answer = input("> ").strip().lower()
        except EOFError:
            answer = ""
        if answer in {"y", "yes"}:
            try:
                marker.write_text("accepted\n", encoding="utf-8")
            except OSError:
                pass
            print("[license] TeamTalk 5 SDK license terms accepted.")
            return
        if answer in {"n", "no"}:
            raise TeamTalkConfigurationError(
                "TeamTalk 5 SDK license terms declined. The SDK will not be used."
            )
        print("Please answer Y or N.")


@dataclass(frozen=True)
class ConnectionConfig:
    host: str
    tcp_port: int = 10333
    udp_port: int = 10333
    username: str = "guest"
    password: str = ""
    nickname: str = "tt-api-client"
    client_name: str = "TT Annoying Tools Linux API"
    encrypted: bool = False
    channel_id: Optional[int] = None
    channel_path: Optional[str] = None
    channel_password: str = ""
    command_timeout: float = 15.0
    kick_resistance: bool = True
    reconnect_delay: float = 3.5
    sdk_path: Optional[str] = None
    sdk_python: Optional[str] = None
    sdk_library: Optional[str] = None
    license_name: Optional[str] = None
    license_key: str = ""
    accept_sdk_license: Optional[bool] = None


def prompt_text(label: str, default: Optional[str] = None, *, secret: bool = False) -> str:
    """Read one interactive value, retaining a supplied default on Enter."""

    if default is None:
        prompt = f"{label}: "
    elif secret:
        prompt = f"{label} [configured]: "
    else:
        prompt = f"{label} [{default}]: "
    value = getpass.getpass(prompt) if secret else input(prompt)
    value = value.strip()
    return default if value == "" and default is not None else value


def comma_int(value: str) -> int:
    """argparse type that accepts ``_`` and ``,`` thousands separators.

    Lets users write ``--count 10,999`` or ``--count 1_000`` instead of being
    rejected by ``type=int``.  ``-`` is preserved for negative inputs and
    surrounding whitespace is tolerated; anything that is not an integer after
    stripping separators raises ``argparse.ArgumentTypeError`` so argparse
    prints its usual ``invalid int value`` message.
    """

    cleaned = value.strip().replace("_", "").replace(",", "")
    try:
        return int(cleaned)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid int value: {value!r}") from exc


def prompt_int(
    label: str,
    default: int,
    *,
    minimum: Optional[int] = None,
    maximum: Optional[int] = None,
) -> int:
    """Read an integer prompt and repeat until its range is valid."""

    while True:
        value = prompt_text(label, str(default))
        try:
            parsed = int(value)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if minimum is not None and parsed < minimum:
            print(f"Please enter a number of at least {minimum}.")
            continue
        if maximum is not None and parsed > maximum:
            print(f"Please enter a number no greater than {maximum}.")
            continue
        return parsed


def prompt_float(
    label: str,
    default: float,
    *,
    minimum: Optional[float] = None,
    maximum: Optional[float] = None,
) -> float:
    """Read a decimal prompt and repeat until its range is valid."""

    while True:
        value = prompt_text(label, f"{default:g}")
        try:
            parsed = float(value)
        except ValueError:
            print("Please enter a number.")
            continue
        if minimum is not None and parsed < minimum:
            print(f"Please enter a number of at least {minimum:g}.")
            continue
        if maximum is not None and parsed > maximum:
            print(f"Please enter a number no greater than {maximum:g}.")
            continue
        return parsed


def prompt_choice(label: str, choices: Iterable[str], default: str) -> str:
    """Read a case-insensitive choice while showing the available values."""

    normalized = {choice.lower(): choice for choice in choices}
    choices_text = "/".join(normalized)
    while True:
        value = prompt_text(f"{label} ({choices_text})", default).lower()
        if value in normalized:
            return normalized[value]
        print(f"Please choose one of: {choices_text}.")


def prompt_yes_no(label: str, default: bool = True) -> bool:
    """Read a yes/no prompt with a friendly default."""

    default_text = "Y/n" if default else "y/N"
    while True:
        value = input(f"{label} [{default_text}]: ").strip().lower()
        if not value:
            return default
        if value in {"y", "yes"}:
            return True
        if value in {"n", "no"}:
            return False
        print("Please answer yes or no.")


def prompt_connection_config(*, channel_required: bool) -> ConnectionConfig:
    """Collect the API-only connection details needed by an interactive tool."""

    host = prompt_text("TeamTalk server host", os.environ.get("TT_HOST", "127.0.0.1"))
    tcp_port = prompt_int("TeamTalk TCP port", _env_int("TT_TCP_PORT", 10333), minimum=1, maximum=65535)
    udp_port = prompt_int("TeamTalk UDP port", _env_int("TT_UDP_PORT", 10333), minimum=1, maximum=65535)
    username = prompt_text("TeamTalk username", os.environ.get("TT_USERNAME", "guest"))
    configured_password = os.environ.get("TT_PASSWORD", "")
    password = prompt_text("TeamTalk password", configured_password or None, secret=True)
    nickname = prompt_text("TeamTalk nickname", os.environ.get("TT_NICKNAME", "tt-api-client"))
    encrypted = prompt_yes_no(
        "Is the TeamTalk server encrypted?",
        _env_bool("TT_ENCRYPTED"),
    )

    channel_id: Optional[int] = None
    channel_path: Optional[str] = None
    if channel_required:
        configured_channel_id = os.environ.get("TT_CHANNEL_ID")
        if configured_channel_id is not None:
            channel_id = prompt_int(
                "TeamTalk channel ID",
                _env_int("TT_CHANNEL_ID", 0),
                minimum=0,
            )
        else:
            channel_path = prompt_text(
                "TeamTalk channel path",
                os.environ.get("TT_CHANNEL_PATH", "/"),
            )
    channel_password = ""
    if channel_required:
        channel_password = prompt_text(
            "TeamTalk channel password",
            os.environ.get("TT_CHANNEL_PASSWORD", "") or None,
            secret=True,
        )

    return ConnectionConfig(
        host=host,
        tcp_port=tcp_port,
        udp_port=udp_port,
        username=username,
        password=password,
        nickname=nickname,
        client_name=os.environ.get("TT_CLIENT_NAME", "TT Annoying Tools Linux API"),
        encrypted=encrypted,
        channel_id=channel_id,
        channel_path=channel_path,
        channel_password=channel_password,
        command_timeout=float(os.environ.get("TT_COMMAND_TIMEOUT", "15")),
        sdk_path=os.environ.get("TEAMTALK_SDK_PATH"),
        sdk_python=os.environ.get("TEAMTALK_SDK_PYTHON"),
        sdk_library=os.environ.get("TEAMTALK_SDK_LIBRARY") or os.environ.get("TEAMTALK_LIBRARY"),
        license_name=os.environ.get("TT_LICENSE_NAME"),
        license_key=os.environ.get("TT_LICENSE_KEY") or "",
    )


def add_connection_arguments(parser: argparse.ArgumentParser) -> None:
    """Add shared connection options to a tool's argument parser."""

    parser.add_argument("--host", default=os.environ.get("TT_HOST"), help="TeamTalk server host")
    parser.add_argument(
        "--tcp-port",
        type=int,
        default=_env_int("TT_TCP_PORT", 10333),
        help="TeamTalk TCP port (default: 10333)",
    )
    parser.add_argument(
        "--udp-port",
        type=int,
        default=_env_int("TT_UDP_PORT", 10333),
        help="TeamTalk UDP port (default: 10333)",
    )
    parser.add_argument(
        "--username",
        default=os.environ.get("TT_USERNAME", "guest"),
        help="TeamTalk username (default: TT_USERNAME or guest)",
    )
    parser.add_argument(
        "--password",
        default=os.environ.get("TT_PASSWORD", ""),
        help="TeamTalk password; prefer TT_PASSWORD to keep it out of shell history",
    )
    parser.add_argument(
        "--nickname",
        default=os.environ.get("TT_NICKNAME", "tt-api-client"),
        help="nickname shown in TeamTalk",
    )
    parser.add_argument(
        "--client-name",
        default=os.environ.get("TT_CLIENT_NAME", "TT Annoying Tools Linux API"),
        help="client name reported to the server",
    )
    parser.add_argument(
        "--encrypted",
        action="store_true",
        default=_env_bool("TT_ENCRYPTED"),
        help="request an encrypted TeamTalk connection",
    )
    channel_group = parser.add_mutually_exclusive_group()
    channel_group.add_argument(
        "--channel-id",
        "--channel",
        dest="channel_id",
        type=int,
        default=None if os.environ.get("TT_CHANNEL_ID") is None else _env_int("TT_CHANNEL_ID", 0),
        help="channel ID to join",
    )
    channel_group.add_argument(
        "--channel-path",
        default=os.environ.get("TT_CHANNEL_PATH"),
        help="channel path to join, for example /Lobby/Games",
    )
    parser.add_argument(
        "--channel-password",
        default=os.environ.get("TT_CHANNEL_PASSWORD", ""),
        help="password for the selected channel",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("TT_COMMAND_TIMEOUT", "15")),
        help="seconds to wait for an SDK event (default: 15)",
    )
    parser.add_argument(
        "--kick-resistance",
        dest="kick_resistance",
        action="store_true",
        default=_env_bool("TT_KICK_RESISTANCE", True),
        help="after a kick/disconnect, wait and reconnect to resume "
        "(default: on; set TT_KICK_RESISTANCE=0 to disable)",
    )
    parser.add_argument(
        "--no-kick-resistance",
        dest="kick_resistance",
        action="store_false",
        help="do not reconnect after a kick; stop the bot instead",
    )
    parser.add_argument(
        "--reconnect-delay",
        type=float,
        default=float(os.environ.get("TT_RECONNECT_DELAY", "3.5")),
        help="seconds to wait before checking online status and reconnecting "
        "after a kick (default: 3.5)",
    )
    parser.add_argument(
        "--sdk-path",
        default=os.environ.get("TEAMTALK_SDK_PATH"),
        help="extracted TeamTalk SDK root (or set TEAMTALK_SDK_PATH)",
    )
    parser.add_argument(
        "--sdk-python",
        default=os.environ.get("TEAMTALK_SDK_PYTHON"),
        help="path to the SDK's TeamTalk5.py",
    )
    parser.add_argument(
        "--sdk-library",
        default=os.environ.get("TEAMTALK_SDK_LIBRARY") or os.environ.get("TEAMTALK_LIBRARY"),
        help="path to libTeamTalk5.so",
    )
    parser.add_argument(
        "--license-name",
        default=os.environ.get("TT_LICENSE_NAME"),
        help="TeamTalk SDK license registration name (or set TT_LICENSE_NAME). "
        "Supply with --license-key to disable the 30-day SDK trial.",
    )
    parser.add_argument(
        "--license-key",
        default=os.environ.get("TT_LICENSE_KEY"),
        help="TeamTalk SDK license key (or set TT_LICENSE_KEY). "
        "Use a password manager or env var; avoid passing keys on the command line.",
    )
    parser.add_argument(
        "--accept-sdk-license",
        dest="accept_sdk_license",
        action="store_const",
        const=True,
        default=None,
        help="accept the TeamTalk 5 SDK license terms without prompting "
        "(or set TT_ACCEPT_SDK_LICENSE=1). Skips the first-run Y/N prompt.",
    )
    parser.add_argument(
        "--decline-sdk-license",
        dest="accept_sdk_license",
        action="store_const",
        const=False,
        help="decline the TeamTalk 5 SDK license terms (or set "
        "TT_ACCEPT_SDK_LICENSE=0). Prevents the SDK from being used.",
    )


def config_from_args(args: argparse.Namespace) -> ConnectionConfig:
    """Create and validate a ``ConnectionConfig`` from argparse output."""

    host = (args.host or "").strip()
    if not host:
        raise TeamTalkConfigurationError(
            "--host is required (or set TT_HOST); refusing to guess a server"
        )
    if not 1 <= args.tcp_port <= 65535 or not 1 <= args.udp_port <= 65535:
        raise TeamTalkConfigurationError("TCP and UDP ports must be between 1 and 65535")
    if args.timeout <= 0:
        raise TeamTalkConfigurationError("--timeout must be greater than zero")
    if args.reconnect_delay < 0:
        raise TeamTalkConfigurationError("--reconnect-delay cannot be negative")
    if args.channel_id is not None and args.channel_id < 0:
        raise TeamTalkConfigurationError("--channel-id cannot be negative")

    channel_path = args.channel_path
    if channel_path:
        channel_path = channel_path.strip()
        if not channel_path.startswith("/"):
            channel_path = "/" + channel_path

    return ConnectionConfig(
        host=host,
        tcp_port=args.tcp_port,
        udp_port=args.udp_port,
        username=args.username,
        password=args.password,
        nickname=args.nickname,
        client_name=args.client_name,
        encrypted=args.encrypted,
        channel_id=args.channel_id,
        channel_path=channel_path,
        channel_password=args.channel_password,
        command_timeout=args.timeout,
        kick_resistance=args.kick_resistance,
        reconnect_delay=args.reconnect_delay,
        sdk_path=args.sdk_path,
        sdk_python=args.sdk_python,
        sdk_library=args.sdk_library,
        license_name=args.license_name,
        license_key=args.license_key or "",
        accept_sdk_license=args.accept_sdk_license,
    )


class TeamTalkSession:
    """A blocking session wrapper around the SDK's event-queue API."""

    def __init__(self, config: ConnectionConfig, sdk: Any = None):
        self.config = config
        # Enforce the TeamTalk 5 SDK license acceptance before any SDK file is
        # loaded/used.  A pre-supplied sdk (already-loaded module) skips the
        # import path but the license gate still runs first, because using the
        # SDK at all requires acceptance of its terms.
        ensure_sdk_license_accepted(pre_choice=config.accept_sdk_license)
        self.sdk = sdk or self._load_configured_sdk()
        try:
            self.client = self.sdk.TeamTalk()
        except Exception as exc:
            raise TeamTalkSDKError(f"failed to initialize TeamTalk: {exc}") from exc
        self.connected = False
        self.logged_in = False
        self.channel_id: Optional[int] = None
        self.rejoin_channel_id: Optional[int] = None
        self.rejoin_channel_password: str = ""
        self._closed = False

    def _load_configured_sdk(self) -> Any:
        # Hold the load lock across the env-var mutation + load + restore so
        # concurrent bot threads cannot interleave (or observe a half-restored
        # TEAMTALK_SDK_* environment).  load_teamtalk_sdk re-enters the same
        # RLock, so there is no deadlock.
        with _SDK_LOAD_LOCK:
            old_path = os.environ.get("TEAMTALK_SDK_PATH")
            old_python = os.environ.get("TEAMTALK_SDK_PYTHON")
            old_library = os.environ.get("TEAMTALK_SDK_LIBRARY")
            if self.config.sdk_path:
                os.environ["TEAMTALK_SDK_PATH"] = self.config.sdk_path
            if self.config.sdk_python:
                os.environ["TEAMTALK_SDK_PYTHON"] = self.config.sdk_python
            if self.config.sdk_library:
                os.environ["TEAMTALK_SDK_LIBRARY"] = self.config.sdk_library
            try:
                return load_teamtalk_sdk()
            finally:
                if old_path is None:
                    os.environ.pop("TEAMTALK_SDK_PATH", None)
                else:
                    os.environ["TEAMTALK_SDK_PATH"] = old_path
                if old_python is None:
                    os.environ.pop("TEAMTALK_SDK_PYTHON", None)
                else:
                    os.environ["TEAMTALK_SDK_PYTHON"] = old_python
                if old_library is None:
                    os.environ.pop("TEAMTALK_SDK_LIBRARY", None)
                else:
                    os.environ["TEAMTALK_SDK_LIBRARY"] = old_library

    def __enter__(self) -> "TeamTalkSession":
        try:
            self.open()
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
        self.close(raise_errors=exc_type is None)

    def _error_text(self, message: Any) -> str:
        error = getattr(message, "clienterrormsg", None)
        if error is None:
            return ""
        number = sdk_int(getattr(error, "nErrorNo", -1), -1)
        description = sdk_text(getattr(error, "szErrorMsg", ""))
        if not description and number >= 0:
            try:
                description = sdk_text(self.client.getErrorMessage(number))
            except Exception:
                pass
        return description or (f"SDK error {number}" if number >= 0 else "unknown SDK error")

    def _wait_for(
        self,
        expected_events: Iterable[int],
        action: str,
        command_id: Optional[int] = None,
    ) -> Any:
        expected = {sdk_int(event) for event in expected_events}
        deadline = time.monotonic() + self.config.command_timeout
        command_success = sdk_event(self.sdk, "CLIENTEVENT_CMD_SUCCESS")
        command_error = sdk_event(self.sdk, "CLIENTEVENT_CMD_ERROR")
        connection_lost = sdk_event(self.sdk, "CLIENTEVENT_CON_LOST")
        connection_failed = sdk_event(self.sdk, "CLIENTEVENT_CON_FAILED")
        crypt_error = sdk_event(self.sdk, "CLIENTEVENT_CON_CRYPT_ERROR")

        while time.monotonic() < deadline:
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            try:
                message = self.client.getMessage(nWaitMS=remaining_ms)
            except TypeError:
                message = self.client.getMessage(remaining_ms)
            event = sdk_int(getattr(message, "nClientEvent", 0))
            if event in expected:
                if event == command_success and command_id is not None:
                    source = sdk_int(getattr(message, "nSource", -1), -1)
                    if source != command_id:
                        continue
                return message
            failure_events = {
                failure
                for failure in (connection_lost, connection_failed, crypt_error)
                if failure is not None
            }
            if event in failure_events:
                self.connected = False
                self.logged_in = False
                self.channel_id = None
                raise TeamTalkError(f"{action} failed: {self._error_text(message) or 'connection event'}")
            if event == command_error:
                source = sdk_int(getattr(message, "nSource", -1), -1)
                if command_id is None or source == command_id:
                    raise TeamTalkError(f"{action} failed: {self._error_text(message)}")
        raise TeamTalkError(f"timed out waiting for {action} after {self.config.command_timeout:g}s")

    def _check_command(self, command_id: Any, action: str) -> int:
        command = sdk_int(command_id, -1)
        if command < 0:
            # TT_Do* functions return -1 for a local (pre-send) failure such as
            # "not connected" or invalid state.  This is not a ClientError code,
            # so there is no message to look up via getErrorMessage (passing
            # -command would look up an unrelated error).  Server-side
            # rejections arrive later as CLIENTEVENT_CMD_ERROR, handled by
            # _wait_for, whose ClientErrorMsg carries the real error code.
            raise TeamTalkError(
                f"{action} was rejected by TeamTalk locally "
                "(client not connected or in an invalid state)"
            )
        return command

    def _apply_license(self) -> None:
        """Activate a purchased TeamTalk SDK license if one is configured.

        ``setLicense`` is a no-op when no name/key are set, so trial builds
        are unaffected.  It is a process-global SDK call, so it is enough to
        run it before connect; a failure is logged but does not block the
        connection (the SDK then falls back to TRAIL MODE).
        """
        name = self.config.license_name
        if not name:
            return
        try:
            ok = self.sdk.setLicense(sdk_string(self.sdk, name), sdk_string(self.sdk, self.config.license_key))
        except Exception as exc:  # pragma: no cover - depends on SDK build
            print(f"[license] setLicense failed: {exc}")
            return
        if ok:
            print("[license] TeamTalk SDK license accepted; trial mode disabled.")
        else:
            print("[license] TeamTalk SDK rejected the license key; running in TRAIL MODE.")

    def _connect_and_login(self) -> None:
        """Establish the TCP/UDP connection and log in (no channel join)."""

        self._apply_license()
        try:
            connected = self.client.connect(
                sdk_string(self.sdk, self.config.host),
                self.config.tcp_port,
                self.config.udp_port,
                0,
                0,
                self.config.encrypted,
            )
        except Exception as exc:
            raise TeamTalkError(f"could not start connection: {exc}") from exc
        if not connected:
            raise TeamTalkError("TeamTalk rejected the connection request")
        success = sdk_event(self.sdk, "CLIENTEVENT_CON_SUCCESS")
        if success is None:
            raise TeamTalkSDKError("the loaded SDK has no connection-success event")
        self._wait_for({success}, "connection")
        self.connected = True
        self.login()

    def open(self) -> None:
        if self.connected:
            if not self.logged_in:
                self.login()
            return
        self._connect_and_login()
        self._rejoin_working_channel()

    def _rejoin_working_channel(self) -> None:
        """Rejoin the channel this session should be in.

        Prefers an explicitly assigned working channel (set by bots that join
        an arbitrary, discovered channel) and falls back to the configured
        channel.  No-op when neither is set.
        """

        if self.rejoin_channel_id is not None:
            self.join_channel(self.rejoin_channel_id, self.rejoin_channel_password)
        elif self.config.channel_id is not None:
            self.join_channel(self.config.channel_id, self.config.channel_password)
        elif self.config.channel_path:
            self.join_channel_path(self.config.channel_path, self.config.channel_password)

    def is_online(self) -> bool:
        """Non-destructive online probe: connected, logged in, and known to the server.

        Uses ``getMyUserID`` so an idle bot notices a server kick that arrived
        while it was sleeping without consuming queued events.
        """

        if self._closed or not self.connected or not self.logged_in:
            return False
        try:
            return sdk_int(self.client.getMyUserID(), -1) > 0
        except Exception:
            return False

    def is_connection_failure(self, message: Any) -> bool:
        """Return True if ``message`` is a connection-lost/failed/crypt event.

        Resets the connected/logged_in/channel flags when it is, so an idle
        event-loop bot that polls ``getMessage`` directly (instead of going
        through ``_wait_for``) notices a server kick deterministically the
        moment the event is dequeued — rather than relying on ``getMyUserID``
        returning <=0 after a disconnect, which the SDK does not guarantee.
        """

        event = sdk_int(getattr(message, "nClientEvent", 0))
        failure_events = {
            failure
            for failure in (
                sdk_event(self.sdk, "CLIENTEVENT_CON_LOST"),
                sdk_event(self.sdk, "CLIENTEVENT_CON_FAILED"),
                sdk_event(self.sdk, "CLIENTEVENT_CON_CRYPT_ERROR"),
            )
            if failure is not None
        }
        if event and event in failure_events:
            self.connected = False
            self.logged_in = False
            self.channel_id = None
            return True
        return False

    def reconnect(self) -> bool:
        """Rebuild the native client and re-establish connection + login.

        The configured channel is rejoined on a best-effort basis; a failed
        rejoin does not undo a successful reconnect.  Returns True if online.
        """

        old = self.client
        try:
            old.closeTeamTalk()
        except Exception:
            pass
        try:
            old.closeTeamTalk = lambda: True  # type: ignore[method-assign]
        except Exception:
            pass
        self.connected = False
        self.logged_in = False
        self.channel_id = None
        try:
            self.client = self.sdk.TeamTalk()
        except Exception as exc:
            raise TeamTalkSDKError(f"failed to reinitialize TeamTalk: {exc}") from exc
        try:
            self._connect_and_login()
        except Exception as exc:
            print(f"[kick-resistance] reconnect failed: {exc}")
            return False
        try:
            self._rejoin_working_channel()
        except Exception as exc:
            print(f"[kick-resistance] could not rejoin channel: {exc}")
        return self.is_online()

    def check_and_reconnect(self) -> bool:
        """Wait the reconnect delay, then reconnect if still offline.

        Implements kick resistance: after a suspected kick the bot waits
        ``reconnect_delay`` seconds, checks whether it is still online, and
        if not rebuilds the connection so the caller can resume.  When kick
        resistance is disabled this only reports the current online status
        without reconnecting, so the caller can stop cleanly.
        """

        if self.config.reconnect_delay > 0:
            time.sleep(self.config.reconnect_delay)
        if self.is_online():
            return True
        if not self.config.kick_resistance:
            return False
        print("[kick-resistance] reconnecting after disconnect…")
        try:
            return self.reconnect()
        except Exception as exc:
            print(f"[kick-resistance] reconnect failed: {exc}")
            return False

    def login(self) -> None:
        """Log in on an established TeamTalk connection."""

        if self.logged_in:
            return
        if not self.connected:
            raise TeamTalkConfigurationError("connect before logging in")
        login_command = self._check_command(
            self.client.doLogin(
                sdk_string(self.sdk, self.config.nickname),
                sdk_string(self.sdk, self.config.username),
                sdk_string(self.sdk, self.config.password),
                sdk_string(self.sdk, self.config.client_name),
            ),
            "login",
        )
        logged_in = sdk_event(self.sdk, "CLIENTEVENT_CMD_MYSELF_LOGGEDIN")
        if logged_in is None:
            raise TeamTalkSDKError("the loaded SDK has no login-success event")
        self._wait_for({logged_in}, "login", login_command)
        self.logged_in = True

    def logout(self) -> None:
        """Log out and wait until TeamTalk confirms the session ended."""

        if not self.logged_in:
            return
        logout_command = self._check_command(self.client.doLogout(), "logout")
        logged_out = sdk_event(self.sdk, "CLIENTEVENT_CMD_MYSELF_LOGGEDOUT")
        if logged_out is None:
            raise TeamTalkSDKError("the loaded SDK has no logout-success event")
        self._wait_for({logged_out}, "logout", logout_command)
        self.logged_in = False

    def close(self, *, raise_errors: bool = False) -> None:
        """Log out, disconnect, and release the SDK client exactly once.

        Cleanup still disconnects and releases the native client if logout
        fails.  Callers can request that a logout failure be raised after
        cleanup; context managers do this only when the wrapped operation did
        not already raise another exception.
        """

        if self._closed:
            return
        self._closed = True
        logout_error: Optional[Exception] = None
        if getattr(self, "client", None) is None:
            return
        if self.logged_in:
            try:
                self.logout()
            except Exception as exc:
                logout_error = exc
        if self.connected:
            try:
                self.client.disconnect()
            except Exception:
                pass
        try:
            self.client.closeTeamTalk()
        except Exception:
            pass
        try:
            # The official Python wrapper calls closeTeamTalk again from its
            # TeamTalk.__del__.  Shadow that instance method after the native
            # handle has been released so repeated cycles cannot double-free
            # the SDK object during interpreter shutdown.
            self.client.closeTeamTalk = lambda: True
        except Exception:
            pass
        self.connected = False
        self.logged_in = False
        if logout_error is not None and raise_errors:
            raise logout_error

    def __del__(self) -> None:  # pragma: no cover - interpreter shutdown dependent
        try:
            self.close()
        except Exception:
            pass

    def join_channel(self, channel_id: int, password: str = "") -> int:
        if channel_id < 0:
            raise TeamTalkConfigurationError("channel ID cannot be negative")
        command = self._check_command(
            self.client.doJoinChannelByID(channel_id, sdk_string(self.sdk, password)),
            "join channel",
        )
        success = sdk_event(self.sdk, "CLIENTEVENT_CMD_SUCCESS")
        if success is None:
            raise TeamTalkSDKError("the loaded SDK has no command-success event")
        self._wait_for({success}, "join channel", command)
        self.channel_id = channel_id
        return channel_id

    def join_channel_path(self, path: str, password: str = "") -> int:
        deadline = time.monotonic() + self.config.command_timeout
        encoded_path = sdk_string(self.sdk, path)
        while time.monotonic() < deadline:
            channel_id = sdk_int(self.client.getChannelIDFromPath(encoded_path), -1)
            # TeamTalk uses zero to mean "not found" for this lookup; valid
            # channel IDs start at one (the root channel).
            if channel_id > 0:
                return self.join_channel(channel_id, password)
            remaining_ms = max(1, int((deadline - time.monotonic()) * 1000))
            self.poll(min(100, remaining_ms))
        raise TeamTalkError(f"TeamTalk channel path was not found: {path}")

    def leave_channel(self) -> None:
        command = self._check_command(self.client.doLeaveChannel(), "leave channel")
        success = sdk_event(self.sdk, "CLIENTEVENT_CMD_SUCCESS")
        if success is None:
            raise TeamTalkSDKError("the loaded SDK has no command-success event")
        self._wait_for({success}, "leave channel", command)
        self.channel_id = None

    def current_channel_id(self) -> int:
        if self.channel_id is not None:
            return self.channel_id
        channel_id = sdk_int(self.client.getMyChannelID(), -1)
        if channel_id < 0:
            raise TeamTalkConfigurationError(
                "the client is not in a channel; supply --channel-id or --channel-path"
            )
        self.channel_id = channel_id
        return channel_id

    def list_channels(self) -> list[dict[str, Any]]:
        """Return the channels currently visible to the logged-in client."""

        if not self.logged_in:
            raise TeamTalkConfigurationError("connect and log in before listing channels")
        try:
            channels = self.client.getServerChannels()
        except Exception as exc:
            raise TeamTalkError(f"could not retrieve TeamTalk channels: {exc}") from exc

        result: list[dict[str, Any]] = []
        for channel in channels:
            channel_id = sdk_int(getattr(channel, "nChannelID", -1), -1)
            if channel_id < 0:
                continue
            try:
                path = sdk_text(self.client.getChannelPath(channel_id))
            except Exception:
                path = ""
            name = sdk_text(getattr(channel, "szName", ""))
            result.append(
                {
                    "id": channel_id,
                    "parent_id": sdk_int(getattr(channel, "nParentID", -1), -1),
                    "name": name,
                    "path": path or ("/" if not name else name),
                    "password_required": bool(getattr(channel, "bPassword", False)),
                    "hidden": bool(sdk_int(getattr(channel, "uChannelType", 0)) & 0x0040),
                }
            )
        result.sort(key=lambda item: (item["path"].casefold(), item["id"]))
        return result

    def list_users(self, *, include_self: bool = False) -> list[dict[str, Any]]:
        """Return online users that can be selected as private-message targets."""

        if not self.logged_in:
            raise TeamTalkConfigurationError("connect and log in before listing users")
        try:
            users = self.client.getServerUsers()
            own_user_id = sdk_int(self.client.getMyUserID(), -1)
        except Exception as exc:
            raise TeamTalkError(f"could not retrieve TeamTalk users: {exc}") from exc

        result: list[dict[str, Any]] = []
        for user in users:
            user_id = sdk_int(getattr(user, "nUserID", -1), -1)
            if user_id < 0 or (not include_self and user_id == own_user_id):
                continue
            channel_id = sdk_int(getattr(user, "nChannelID", -1), -1)
            try:
                channel_path = sdk_text(self.client.getChannelPath(channel_id))
            except Exception:
                channel_path = ""
            nickname = sdk_text(getattr(user, "szNickname", ""))
            username = sdk_text(getattr(user, "szUsername", ""))
            result.append(
                {
                    "id": user_id,
                    "nickname": nickname,
                    "username": username,
                    "channel_id": channel_id,
                    "channel_path": channel_path,
                    "display_name": nickname or username or f"user {user_id}",
                }
            )
        result.sort(key=lambda item: (item["display_name"].casefold(), item["id"]))
        return result

    def send_text(
        self,
        text: str,
        *,
        channel_id: Optional[int] = None,
        user_id: Optional[int] = None,
    ) -> None:
        if not text:
            raise TeamTalkConfigurationError("message text cannot be empty")
        if (channel_id is None) == (user_id is None):
            raise TeamTalkConfigurationError("choose exactly one TeamTalk message target")

        if channel_id is not None:
            message_type = getattr(self.sdk.TextMsgType, "MSGTYPE_CHANNEL")
            messages = self.sdk.buildTextMessage(
                text, message_type, nChannelID=channel_id
            )
            action = "send channel message"
        else:
            message_type = getattr(self.sdk.TextMsgType, "MSGTYPE_USER")
            messages = self.sdk.buildTextMessage(text, message_type, nToUserID=user_id)
            action = "send private message"

        success = sdk_event(self.sdk, "CLIENTEVENT_CMD_SUCCESS")
        if success is None:
            raise TeamTalkSDKError("the loaded SDK has no command-success event")
        for message in messages:
            command = self._check_command(self.client.doTextMessage(message), action)
            self._wait_for({success}, action, command)

    def send_channel_message(self, text: str, channel_id: Optional[int] = None) -> None:
        self.send_text(text, channel_id=self.current_channel_id() if channel_id is None else channel_id)

    def send_private_message(self, text: str, user_id: int) -> None:
        if user_id < 0:
            raise TeamTalkConfigurationError("user ID cannot be negative")
        self.send_text(text, user_id=user_id)

    def poll(self, wait_ms: int = 1000) -> Any:
        try:
            return self.client.getMessage(nWaitMS=wait_ms)
        except TypeError:
            return self.client.getMessage(wait_ms)


def message_fields(message: Any) -> dict[str, Any]:
    """Return stable fields from a TeamTalk ``TextMessage`` structure."""

    return {
        "type": sdk_int(getattr(message, "nMsgType", 0)),
        "from_user_id": sdk_int(getattr(message, "nFromUserID", 0)),
        "from_username": sdk_text(getattr(message, "szFromUsername", "")),
        "to_user_id": sdk_int(getattr(message, "nToUserID", 0)),
        "channel_id": sdk_int(getattr(message, "nChannelID", 0)),
        "text": sdk_text(getattr(message, "szMessage", "")),
        "more": bool(getattr(message, "bMore", False)),
    }


def print_tool_error(error: Exception) -> int:
    """Print a concise CLI error and return a conventional failure code."""

    print(f"Error: {error}", file=sys.stderr)
    if isinstance(error, TeamTalkSDKError):
        print(f"See {TEAMTALK_SDK_DOWNLOAD} for the official SDK.", file=sys.stderr)
    return 2
