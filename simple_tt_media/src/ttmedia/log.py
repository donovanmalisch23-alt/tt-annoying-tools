"""Centralised logging setup.

One place configures the root logger so the CLI, the setup wizard, and the
bot all format messages identically and respond to a single level switch.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_CONFIGURED = False


def setup_logging(level: str = "INFO", logfile: Optional[str] = None) -> None:
    """Configure root logging once. Safe to call repeatedly."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        root.setLevel(level.upper())
        return

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if logfile:
        try:
            Path(logfile).parent.mkdir(parents=True, exist_ok=True)
            handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
        except OSError:
            pass  # a bad log path must never stop the bot from running
    for h in handlers:
        h.setFormatter(fmt)
    root.handlers[:] = handlers
    root.setLevel(level.upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
