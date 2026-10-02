#!/usr/bin/env python3
"""Shared ``--profile`` support: saved setting bundles for every tool.

A profile is a small plain-text ``key = value`` file — conventionally named
``<something>.profile`` — that pre-loads any subset of a tool's command-line
settings, so switching servers is ``--profile home`` instead of retyping
``--host``, ports, and credentials.  The same profile can hold keys for
several tools at once: each tool picks up the keys it recognizes and ignores
the rest, so one file can drive the whole suite.

Format:

- ``#`` starts a comment line; blank lines are skipped.
- Each setting is one line, ``key = value``.  Keys are flag names without
  the leading dashes; dashes and underscores are equivalent and case is
  ignored (``tcp-port``, ``tcp_port``, and ``TCP-Port`` all work).
- A value may be wrapped in one pair of double or single quotes, which is
  stripped; quotes are the only way to keep leading/trailing spaces.
- An empty value is allowed and means the empty string: ``password =``
  forces an anonymous login without inheriting ``TT_PASSWORD``.
- Boolean flags accept true/false, yes/no, on/off, and 1/0.  ``true``
  injects the flag (``--encrypted``); ``false`` injects its opposite when
  one exists (``--no-kick-resistance``).

Precedence: an explicit command-line flag always beats the profile, and the
profile beats the tool's built-in defaults (including ``teamtalk.env``).

Resolution: ``--profile`` takes either a path to the file or a bare name.
A bare name is searched for as ``<name>.profile`` (then plain ``<name>``)
in the current directory and then next to the tools themselves, so
``--profile home`` finds ``./home.profile`` from wherever the tool is run.

A profile cannot set ``--profile`` itself (no chaining) or ``--help``.
Typo protection: a key that no tool anywhere recognizes is an error naming
the valid keys; a key that other tools recognize but this one does not is
skipped silently so shared profiles work everywhere.

``./profile_creator.sh`` writes profiles interactively: it walks every
settable key once, explains each, and Enter skips (the skipped keys are
kept in the file as comments to fill in later).
"""

from __future__ import annotations

import argparse
import importlib
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from tt_teamtalk import TeamTalkConfigurationError, add_connection_arguments

PROFILE_SUFFIX = ".profile"

# Every tool whose command-line settings a profile may carry.  Searched in
# this order for typo detection and by the profile creator, so the shared
# connection settings come first and the per-tool settings follow.
TOOL_MODULES = (
    "tt_spammer",
    "tt_leave_join_spammer",
    "tt_message_spammer",
    "ttbot_the_offender",
    "tt_concurrent_bots",
    "tt_suite",
    "tt_loic",
    "tt_ramp",
)

# Destinations a profile may never touch: help would abort the run, and
# chaining profiles would need recursion with merge rules nobody asked for.
_EXCLUDED_DESTS = {"help", "profile"}


def normalize_key(key: str) -> str:
    """Map a flag spelling to its canonical profile key.

    Works for both spellings callers use: a bare profile key (``tcp_port``)
    and a full flag (``--tcp-port``); leading dashes are stripped so both
    land on the same key.
    """
    return key.strip().lstrip("-").lower().replace("-", "_")


def add_profile_argument(parser: argparse.ArgumentParser) -> None:
    """Add the shared ``--profile`` option to a tool's parser."""

    parser.add_argument(
        "--profile",
        metavar="NAME_OR_PATH",
        help="load settings from a .profile file: a path, or a name searched "
        "as <name>.profile in the current directory. Explicit flags win "
        "over the profile; create profiles with ./profile_creator.sh",
    )


# --------------------------------------------------------------------------- #
# Locating and parsing profile files
# --------------------------------------------------------------------------- #

def profile_candidates(spec: str) -> list[Path]:
    """Every path a ``--profile`` value could refer to, best guess first."""
    spec = spec.strip()
    if spec.endswith(PROFILE_SUFFIX):
        names = [spec]
    else:
        # Try the conventional suffix first, then a file named exactly as
        # given (so plain ``--profile teamtalk.env``-style paths work too).
        names = [spec + PROFILE_SUFFIX, spec]
    dirs = [Path.cwd(), Path(__file__).resolve().parent]
    candidates: list[Path] = []
    for name in names:
        candidates.append(Path(name))
        for directory in dirs:
            candidate = directory / name
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def find_profile(spec: str) -> Path:
    """Resolve a ``--profile`` value to an existing file."""
    candidates = profile_candidates(spec)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(candidate) for candidate in candidates)
    raise TeamTalkConfigurationError(
        f"profile not found: {spec!r} (searched {searched})"
    )


def load_profile(path: Path) -> dict[str, str]:
    """Parse a profile file into an ordered ``key -> value`` mapping."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TeamTalkConfigurationError(f"cannot read profile {path}: {exc}")
    settings: dict[str, str] = {}
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if "=" not in line:
            raise TeamTalkConfigurationError(
                f"{path}:{lineno}: expected 'key = value', got: {line!r}"
            )
        key_part, _, value = line.partition("=")
        key = normalize_key(key_part)
        if not key:
            raise TeamTalkConfigurationError(
                f"{path}:{lineno}: empty setting name"
            )
        if key in settings:
            raise TeamTalkConfigurationError(
                f"{path}:{lineno}: setting {key!r} appears more than once"
            )
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        settings[key] = value
    return settings


def _profile_bool(key: str, value: str) -> bool:
    lowered = value.strip().lower()
    if lowered in {"true", "yes", "on", "1"}:
        return True
    if lowered in {"false", "no", "off", "0"}:
        return False
    raise TeamTalkConfigurationError(
        f"profile setting {key!r}: expected true/false, got {value!r}"
    )


# --------------------------------------------------------------------------- #
# Applying profiles through each tool's own argparse parser
# --------------------------------------------------------------------------- #

def _long_options(action: argparse.Action) -> list[str]:
    return [option for option in action.option_strings if option.startswith("--")]


def _profileable_actions(parser: argparse.ArgumentParser) -> list[argparse.Action]:
    """The actions a profile may set, in declaration order."""
    return [
        action
        for action in parser._actions
        if _long_options(action) and action.dest not in _EXCLUDED_DESTS
    ]


def _actions_by_key(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    """Map every long-flag spelling (including aliases) to its action."""
    mapping: dict[str, argparse.Action] = {}
    for action in _profileable_actions(parser):
        for option in _long_options(action):
            mapping.setdefault(normalize_key(option), action)
    return mapping


def _actions_by_dest(
    actions: Iterable[argparse.Action],
) -> dict[str, list[argparse.Action]]:
    grouped: dict[str, list[argparse.Action]] = {}
    for action in actions:
        grouped.setdefault(action.dest, []).append(action)
    return grouped


def _known_profile_keys() -> set[str]:
    """Every key any tool understands, for typo detection."""
    keys: set[str] = set()
    for parser in _tool_parsers():
        keys.update(_actions_by_key(parser))
    return keys


def _tool_parsers() -> list[argparse.ArgumentParser]:
    """Build every tool's parser, importing the tools lazily.

    The tools import this module, so importing them back at module load
    time would be circular; by the time a profile is being applied the
    running tool is already imported, and ``importlib`` returns the loaded
    module from ``sys.modules`` rather than re-executing it.
    """
    parsers = []
    for module_name in TOOL_MODULES:
        module = importlib.import_module(module_name)
        parsers.append(module.build_parser())
    return parsers


def _explicit_dests(argv: list[str], key_map: dict[str, argparse.Action]) -> set[str]:
    """Destinations the user set on the command line (profile must lose)."""
    dests: set[str] = set()
    for index, token in enumerate(argv):
        if token == "--":
            break
        if not token.startswith("--"):
            continue
        name = token.split("=", 1)[0]
        action = key_map.get(normalize_key(name))
        if action is not None:
            dests.add(action.dest)
    return dests


def _injection_token(
    action: argparse.Action,
    key: str,
    value: str,
    dest_actions: dict[str, list[argparse.Action]],
) -> Optional[str]:
    """One argv token that sets ``key`` to ``value`` through argparse.

    Values are injected as ``--flag=value`` so a value that starts with a
    dash (a channel path typo, a negative number) cannot be mistaken for
    another option, and the tool's own ``type``/``choices`` validation runs
    exactly as it would for a typed command line.
    """
    if isinstance(action, argparse._StoreConstAction):
        wanted = _profile_bool(key, value)
        for candidate in dest_actions.get(action.dest, []):
            if (
                isinstance(candidate, argparse._StoreConstAction)
                and bool(candidate.const) == wanted
            ):
                return _long_options(candidate)[0]
        if wanted == bool(action.default):
            # The value the profile asks for is already this tool's default,
            # so there is nothing to inject.  If instead the profile asks
            # for the opposite polarity and no flag expresses it (some
            # booleans only have an "on" flag), say so instead of silently
            # running with a value that was never requested.
            return None
        raise TeamTalkConfigurationError(
            f"profile setting {key!r}: {wanted} has no matching flag on this tool"
        )
    if action.choices is not None and value not in action.choices:
        choices = "/".join(str(choice) for choice in action.choices)
        raise TeamTalkConfigurationError(
            f"profile setting {key!r}: {value!r} is not one of: {choices}"
        )
    if action.type is not None:
        try:
            action.type(value)
        except (ValueError, TypeError, argparse.ArgumentTypeError) as exc:
            raise TeamTalkConfigurationError(
                f"profile setting {key!r}: invalid value {value!r} ({exc})"
            )
    return f"{_long_options(action)[0]}={value}"


def parse_args_with_profile(
    parser: argparse.ArgumentParser, argv: list[str]
) -> argparse.Namespace:
    """``parser.parse_args`` with ``--profile`` support layered on top."""
    spec = _profile_spec_from_argv(argv)
    if spec is None:
        return parser.parse_args(argv)

    path = find_profile(spec)
    settings = load_profile(path)
    key_map = _actions_by_key(parser)
    dest_actions = _actions_by_dest(_profileable_actions(parser))

    known = _known_profile_keys()
    unknown = [key for key in settings if key not in known]
    if unknown:
        valid = ", ".join(sorted(known))
        raise TeamTalkConfigurationError(
            f"profile {path}: unknown setting(s) {', '.join(unknown)}; "
            f"valid settings are: {valid}"
        )

    explicit = _explicit_dests(argv, key_map)
    injected: list[str] = []
    applied: list[str] = []
    for_other_tools: list[str] = []
    for key, value in settings.items():
        action = key_map.get(key)
        if action is None:
            for_other_tools.append(key)
            continue
        if action.dest in explicit:
            continue  # an explicit flag always wins over the profile
        token = _injection_token(action, key, value, dest_actions)
        if token is None:
            continue
        injected.append(token)
        applied.append(key)

    args = parser.parse_args(injected + list(argv))
    name = path.name
    if applied:
        print(f"Profile {name}: applied {', '.join(applied)}.")
    if for_other_tools:
        listed = ", ".join(for_other_tools)
        print(f"Profile {name}: not used by this tool: {listed}.")
    return args


def _profile_spec_from_argv(argv: list[str]) -> Optional[str]:
    """Pull the ``--profile`` value out of argv before the real parse."""
    pending = False
    for token in argv:
        if token == "--":
            break
        if pending:
            return token
        if token.startswith("--profile="):
            return token.split("=", 1)[1]
        if token == "--profile":
            pending = True
    return None


# --------------------------------------------------------------------------- #
# Interactive profile creator (run through ./profile_creator.sh)
# --------------------------------------------------------------------------- #

def _creator_sections() -> list[tuple[str, list[tuple[argparse.Action, str]]]]:
    """Every settable key once, shared connection settings first.

    Two actions can share one destination — ``--kick-resistance`` and its
    off-flag ``--no-kick-resistance`` are one setting, not two — so the
    walk dedupes on destination and asks for the polarity-neutral key
    (``kick_resistance``) of the first-declared action only.
    """
    sections: list[tuple[str, list[tuple[argparse.Action, str]]]] = []
    seen: set[str] = set()
    seen_dests: set[str] = set()

    def _entries(actions: Iterable[argparse.Action]) -> list[tuple[argparse.Action, str]]:
        collected = []
        for action in actions:
            key = normalize_key(_long_options(action)[0])
            if key in seen or action.dest in seen_dests:
                continue
            seen.add(key)
            seen_dests.add(action.dest)
            collected.append((action, key))
        return collected

    shared = argparse.ArgumentParser()
    add_connection_arguments(shared)
    sections.append(
        ("Connection settings (every API tool)", _entries(_profileable_actions(shared)))
    )

    for module_name in TOOL_MODULES:
        module = importlib.import_module(module_name)
        parser = module.build_parser()
        title = (parser.description or module_name).splitlines()[0].strip()
        entries = _entries(_profileable_actions(parser))
        if entries:
            sections.append((f"{module_name}.py — {title}", entries))
    return sections


def _prompt_setting(action: argparse.Action, key: str) -> Optional[str]:
    """Ask for one setting; Enter (empty answer) skips it."""
    label = key
    if action.help:
        label += f" — {action.help}"
    if action.choices is not None:
        label += f" (choices: {'/'.join(str(c) for c in action.choices)})"
    print(label)
    while True:
        if isinstance(action, argparse._StoreConstAction):
            answer = input("Set it? y or n (Enter to skip): ").strip().lower()
            if not answer:
                return None
            if answer in {"y", "yes"}:
                return "true"
            if answer in {"n", "no"}:
                return "false"
            print("Please answer y or n.")
        else:
            answer = input("Value (Enter to skip): ").strip()
            if not answer:
                return None
            if action.choices is not None and answer not in action.choices:
                choices = "/".join(str(c) for c in action.choices)
                print(f"Please choose one of: {choices}.")
                continue
            if action.type is not None:
                try:
                    action.type(answer)
                except (ValueError, TypeError, argparse.ArgumentTypeError) as exc:
                    print(f"That value is not valid: {exc}")
                    continue
            return answer


def render_profile(
    name: str, chosen: dict[str, str], skipped: list[str]
) -> str:
    """Lay out the profile file: chosen settings active, skipped as comments."""
    lines = [
        f"# TT Annoying Tools profile: {name}",
        f"# Created {date.today().isoformat()} by ./profile_creator.sh",
        "# Use with any tool, e.g.: python3 tt_spammer.py --profile <name>",
        "# Explicit command-line flags beat this file; settings no tool",
        "# recognizes are an error, settings other tools own are ignored.",
        "# Plain text file: keep real credentials out of it (it is gitignored).",
    ]
    for key, value in chosen.items():
        lines.append(f"{key} = {value}")
    if skipped:
        lines.append("")
        lines.append("# Skipped at creation time; uncomment and set to use:")
        for key in skipped:
            lines.append(f"# {key} =")
    return "\n".join(lines) + "\n"


def create_profile_interactive(name: Optional[str] = None) -> Path:
    """Walk every settable key and write the profile the answers describe."""
    print("Profile creator for the TT Annoying Tools.")
    print("Each setting is shown with its help text; press Enter to skip one.")
    print("Skipped settings are written to the file as comments.")
    if name is None:
        name = input("Profile name (Enter uses 'myserver'): ").strip() or "myserver"
    if not name.endswith(PROFILE_SUFFIX):
        name += PROFILE_SUFFIX
    path = Path.cwd() / name

    chosen: dict[str, str] = {}
    skipped: list[str] = []
    for section_title, entries in _creator_sections():
        print()
        print(f"=== {section_title} ===")
        for action, key in entries:
            value = _prompt_setting(action, key)
            if value is None:
                skipped.append(key)
            else:
                chosen[key] = value

    path.write_text(render_profile(name, chosen, skipped), encoding="utf-8")
    applied = ", ".join(chosen) if chosen else "nothing"
    print()
    print(f"Wrote {path} with: {applied}.")
    print("Run any tool with --profile " + name.removesuffix(PROFILE_SUFFIX) + " to use it.")
    return path


if __name__ == "__main__":
    import sys

    _argv = sys.argv[1:]
    if _argv and _argv[0] == "--create":
        create_profile_interactive(_argv[1] if len(_argv) > 1 else None)
        raise SystemExit(0)
    print(
        "This module is the shared --profile engine for the TT Annoying Tools.\n"
        "Run ./profile_creator.sh to create a profile, or pass --profile NAME\n"
        "to any tool."
    )