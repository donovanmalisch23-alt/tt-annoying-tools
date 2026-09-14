"""The allowlist file: parsing, validation and atomic writes.

The CLI tools read a one-host-per-line allowlist (``whitelist.txt`` by default,
``--whitelist`` to point elsewhere) and refuse to touch anything that is not on
it. This module is the bridge's side of that contract: it edits *any* file the
operator configured, keeps the format the tools expect, and never leaves a
half-written file behind.
"""

from __future__ import annotations

import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

#: Hostname or IP literal, optionally with [] around an IPv6 address.
_HOST_RE = re.compile(r"^\[?[A-Za-z0-9_.:-]+\]?$")


class WhitelistError(ValueError):
    """Raised for a file the tools could not use."""


@dataclass(frozen=True)
class WhitelistFile:
    path: Path
    raw: str
    entries: list[str]
    exists: bool
    mtime: float

    @property
    def count(self) -> int:
        return len(self.entries)


def normalize(host: str) -> str:
    """Match the tools' comparison: lowercase, no trailing dots, no [ ] wrapping."""
    value = host.strip().lower().rstrip(".")
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return value


def parse_entries(text: str) -> list[str]:
    """Every non-comment, non-blank line, normalized, in file order, deduped."""
    entries: list[str] = []
    for line in text.splitlines():
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        value = normalize(stripped)
        if value and value not in entries:
            entries.append(value)
    return entries


def validate_lines(text: str) -> list[str]:
    """Return the offending lines (empty when the text is acceptable)."""
    problems: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.split("#", 1)[0].strip()
        if not stripped:
            continue
        if not _HOST_RE.match(stripped):
            problems.append(f"line {number}: {stripped!r}")
    return problems


def read(path: Path) -> WhitelistFile:
    try:
        raw = path.read_text(encoding="utf-8")
        exists = True
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        return WhitelistFile(path=path, raw="", entries=[], exists=False, mtime=0.0)
    except OSError as exc:  # unreadable: report it, do not crash the server
        raise WhitelistError(f"cannot read {path}: {exc}") from exc
    return WhitelistFile(
        path=path, raw=raw, entries=parse_entries(raw), exists=exists, mtime=mtime
    )


def write(path: Path, raw: str) -> WhitelistFile:
    """Validate then atomically replace the file (tmp file + os.replace)."""
    problems = validate_lines(raw)
    if problems:
        raise WhitelistError("not a usable allowlist entry — " + "; ".join(problems))

    text = raw if raw.endswith("\n") or raw == "" else raw + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=str(path.parent), prefix=".whitelist-", delete=False
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(handle.name, 0o644)
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise
    return read(path)


def is_allowed(host: str, entries: list[str]) -> bool:
    wanted = normalize(host)
    return any(normalize(entry) == wanted for entry in entries)


def require_allowed(host: str, entries: list[str], path: Path) -> None:
    if not entries:
        raise WhitelistError(
            f"the allowlist ({path}) is empty; add the server you are authorised to test."
        )
    if not is_allowed(host, entries):
        raise WhitelistError(
            f"'{host}' is not in {path}; add it there (admin panel) before running this tool."
        )
