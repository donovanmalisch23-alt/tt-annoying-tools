"""The Webby bridge — live mode's backend.

``webby/selftest.py`` exercises the whole surface: the argv contract against
every tool's own ``build_parser()``, then a real HTTP server — allowlist reads
and writes, admin sign-in and throttling, the run gates, a streamed child
process, and the static panel. This module is the pytest entry point for it, so
``python3 -m pytest src/tests/`` covers the bridge alongside the tools.
"""

from __future__ import annotations

from pathlib import Path

from webby import selftest

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_bridge_argv_is_accepted_by_every_tool_parser() -> None:
    """The bridge and the CLI tools must not drift apart.

    Failure here means the bridge would spawn a tool with a flag that tool does
    not accept — a bug the panel could never show you, because the child would
    simply exit 2.
    """

    before = len(selftest.FAILED)
    selftest.check_argv_contract(REPO_ROOT)
    assert len(selftest.FAILED) == before, "the bridge's argv drifted from a tool's parser"


def test_bridge_self_test_passes() -> None:
    """The full end-to-end run: server, gates, admin edits, a streamed run."""

    assert selftest.main() == 0
