"""Bridge configuration: flags, environment variables and safe defaults."""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_PORT = 8787
DEFAULT_HOST = "0.0.0.0"

#: Where a run's stdout/stderr is buffered before the page reads it.
DEFAULT_LOG_LINES = 5000


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(f"WEBBY_{name}")
    return value if value not in (None, "") else default


def _env_bool(name: str, default: bool = False) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


@dataclass
class BridgeConfig:
    """Everything the bridge needs to know about its environment."""

    repo_root: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    whitelist_path: Path = Path("whitelist.txt")
    admin_path: Path = Path(".webby/admin.json")
    state_dir: Path = Path(".webby")
    python: str = sys.executable
    accept_sdk_license: bool = False
    ensure_admin: bool = False
    require_admin_for_runs: bool = False
    max_run_seconds: float = 0.0
    log_lines: int = DEFAULT_LOG_LINES
    session_ttl: float = 8 * 3600.0
    allow_origins: tuple[str, ...] = ("*",)
    static_dir: Path | None = None
    stop_grace_seconds: float = 6.0
    extra_env: dict[str, str] = field(default_factory=dict)

    # ----- derived paths ---------------------------------------------------- #

    @property
    def dist_dir(self) -> Path:
        return self.static_dir or (self.repo_root / "dist")

    @property
    def sdk_license_marker(self) -> Path:
        return self.repo_root / ".tt-sdk-license-accepted"

    @property
    def sdk_license_accepted(self) -> bool:
        """True when the operator has already accepted the SDK license here.

        The CLI tools skip the first-run prompt when the marker file exists, so
        passing ``--accept-sdk-license`` in that case preserves exactly what
        running the tool by hand would do. A bridge child has no usable stdin,
        so without this the prompt would fail on EOF.
        """
        return self.accept_sdk_license or self.sdk_license_marker.is_file()

    @property
    def sdk_python(self) -> Path:
        return self.repo_root / "sdk" / "TeamTalk5.py"

    @property
    def sdk_library(self) -> Path:
        return self.repo_root / "sdk" / "libTeamTalk5.so"

    def tool_script(self, name: str) -> Path:
        return self.repo_root / name

    # ----- description ------------------------------------------------------ #

    def describe(self) -> dict[str, object]:
        """The public, non-secret part of the configuration."""
        return {
            "repo_root": str(self.repo_root),
            "host": self.host,
            "port": self.port,
            "whitelist_path": str(self.whitelist_path),
            "admin_path": str(self.admin_path),
            "state_dir": str(self.state_dir),
            "python": self.python,
            "accept_sdk_license": self.sdk_license_accepted,
            "require_admin_for_runs": self.require_admin_for_runs,
            "max_run_seconds": self.max_run_seconds,
            "dist_dir": str(self.dist_dir),
            "dist_built": (self.dist_dir / "index.html").is_file(),
            "sdk_python_present": self.sdk_python.is_file(),
            "sdk_library_present": self.sdk_library.is_file(),
            "sdk_marker_present": self.sdk_license_marker.is_file(),
            "allow_origins": list(self.allow_origins),
        }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python3 -m webby",
        description=(
            "Local bridge that lets the web panel drive the real TeamTalk tools "
            "and manage the allowlist file."
        ),
    )
    parser.add_argument(
        "command",
        nargs="?",
        default="serve",
        choices=["serve", "status", "stop", "admin"],
        help="serve (default), status, stop, or admin",
    )
    parser.add_argument(
        "admin_action",
        nargs="?",
        default="show",
        choices=["set", "show"],
        help="admin: set (create/replace the credential) or show (default)",
    )
    parser.add_argument("--repo-root", default=_env("REPO_ROOT"))
    parser.add_argument("--host", default=_env("HOST", DEFAULT_HOST))
    parser.add_argument("--port", type=int, default=_env_int("PORT", DEFAULT_PORT))
    parser.add_argument(
        "--whitelist",
        default=_env("WHITELIST"),
        help="Allowlist file the admin panel edits (any path).",
    )
    parser.add_argument("--admin-file", default=_env("ADMIN_FILE"), help="Admin credential file.")
    parser.add_argument("--state-dir", default=_env("STATE_DIR"), help="Pid/log directory.")
    parser.add_argument("--python", default=_env("PYTHON", sys.executable))
    parser.add_argument(
        "--accept-sdk-license",
        action="store_true",
        default=_env_bool("ACCEPT_SDK_LICENSE", False),
        help=(
            "Pass --accept-sdk-license to the tools. Only use this if you have read and "
            "accept the TeamTalk SDK license; otherwise accept it once with the CLI."
        ),
    )
    parser.add_argument(
        "--require-admin",
        action="store_true",
        default=_env_bool("REQUIRE_ADMIN_FOR_RUNS", False),
        help="Require an admin session before a run can be started.",
    )
    parser.add_argument(
        "--ensure-admin",
        action="store_true",
        default=_env_bool("ENSURE_ADMIN", False),
        help=(
            "On start, create an admin credential if none exists and print the "
            "generated password once. Used by the dev server so the preview has "
            "a usable admin login without a second terminal."
        ),
    )
    parser.add_argument(
        "--max-run-seconds",
        type=float,
        default=_env_float("MAX_RUN_SECONDS", 0.0),
        help="Hard cap for a single run (0 = no cap).",
    )
    parser.add_argument("--log-lines", type=int, default=_env_int("LOG_LINES", DEFAULT_LOG_LINES))
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=None,
        help="Origin allowed to call the API (repeatable; default * for local use).",
    )
    parser.add_argument("--static-dir", default=_env("STATIC_DIR"))
    # `admin` sub-options.
    parser.add_argument("--username", default=_env("ADMIN_USER", "admin"))
    parser.add_argument("--password", default=None, help="admin set: password (omit to be prompted)")
    parser.add_argument("--generate", action="store_true", help="admin set: generate a password")
    parser.add_argument("--force", action="store_true", help="admin set: overwrite an existing file")
    return parser


def config_from_args(argv: list[str] | None = None) -> tuple[BridgeConfig, argparse.Namespace]:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve() if args.repo_root else Path.cwd()
    state_dir = Path(args.state_dir).expanduser() if args.state_dir else repo_root / ".webby"
    whitelist = Path(args.whitelist).expanduser() if args.whitelist else repo_root / "whitelist.txt"
    admin = Path(args.admin_file).expanduser() if args.admin_file else state_dir / "admin.json"
    origins = tuple(args.allow_origin) if args.allow_origin else ("*",)

    config = BridgeConfig(
        repo_root=repo_root,
        host=args.host,
        port=args.port,
        whitelist_path=whitelist if whitelist.is_absolute() else repo_root / whitelist,
        admin_path=admin if admin.is_absolute() else repo_root / admin,
        state_dir=state_dir if state_dir.is_absolute() else repo_root / state_dir,
        python=args.python,
        accept_sdk_license=bool(args.accept_sdk_license),
        ensure_admin=bool(args.ensure_admin),
        require_admin_for_runs=bool(args.require_admin),
        max_run_seconds=float(args.max_run_seconds),
        log_lines=max(200, int(args.log_lines)),
        allow_origins=origins,
        static_dir=Path(args.static_dir).expanduser() if args.static_dir else None,
    )
    return config, args
