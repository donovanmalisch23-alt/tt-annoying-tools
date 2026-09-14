"""Webby: the local bridge that runs the real TeamTalk tools for the web panel.

The browser panel at the repository root is a pure client: a page cannot open a
raw TCP connection to a TeamTalk server on port 10333, so a small local process
does it instead. This package is that process:

* it serves the built panel from ``dist/`` (same origin, so no CORS games), and
* it exposes a JSON API that starts the repository's own CLI tools as child
  processes, streaming their output back to the page, and
* it owns the allowlist file, which only an authenticated admin can edit.

It is deliberately **standard library only**: no pip install, no virtualenv, no
build step for the bridge itself. The tools it launches are the same
``tt_*.py`` scripts the command line uses, so behaviour cannot drift.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
