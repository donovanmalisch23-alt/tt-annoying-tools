"""Command dispatch for private-message (and optional channel) commands.

The dispatcher parses ``<name> <args>``, enforces admin-only commands, runs
the handler on a worker thread (so network work never blocks the SDK event
loop), and turns any :class:`CommandError` into a friendly reply. An unknown
name always gets an explicit error reply pointing at ``h``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from ..errors import CommandError, PermissionDenied, UnknownCommand
from ..log import get_logger

log = get_logger(__name__)


@dataclass
class CommandContext:
    dispatcher: "Dispatcher"
    player: object
    sources: object
    client: object
    cfg: object
    requester: str
    user_id: int
    is_admin: bool
    is_private: bool
    reply: Callable[[str], None]
    arg: str = ""


Handler = Callable[[CommandContext], None]


@dataclass
class Command:
    name: str
    handler: Handler
    aliases: List[str] = field(default_factory=list)
    admin: bool = False
    usage: str = ""
    summary: str = ""
    help: str = ""
    group: str = "Playback"


class Dispatcher:
    def __init__(self, player, sources, client, cfg, lock_manager=None) -> None:
        self.player = player
        self.sources = sources
        self.client = client
        self.cfg = cfg
        self.lock_manager = lock_manager
        self._commands: Dict[str, Command] = {}
        self._by_name: Dict[str, Command] = {}
        # Per-user transient state (last search results for `select`).
        self._results: Dict[int, List] = {}
        self._results_lock = threading.Lock()

    # ---- registration ---------------------------------------------------

    def register(self, command: Command) -> None:
        self._by_name[command.name] = command
        for key in [command.name, *command.aliases]:
            k = key.lower()
            if k in self._commands:
                raise ValueError(f"duplicate command key: {k}")
            self._commands[k] = command

    def commands(self) -> List[Command]:
        return list(self._by_name.values())

    def lookup(self, name: str) -> Optional[Command]:
        return self._commands.get(name.lower())

    # ---- per-user results ----------------------------------------------

    def set_results(self, user_id: int, tracks: List) -> None:
        with self._results_lock:
            self._results[user_id] = tracks

    def get_results(self, user_id: int) -> List:
        with self._results_lock:
            return self._results.get(user_id, [])

    # ---- dispatch -------------------------------------------------------

    def handle(self, *, text: str, requester: str, user_id: int,
               is_admin: bool, is_private: bool,
               reply: Callable[[str], None]) -> None:
        text = text.strip()
        if not text:
            return
        lm = self.lock_manager
        # A private message from someone we asked for a password is the
        # password, not a command — capture it before any parsing.
        if is_private and lm is not None and lm.has_pending(user_id):
            lm.submit_password(user_id, text)
            return
        name, _, arg = text.partition(" ")
        name = name.strip()
        command = self.lookup(name)
        is_lock_cmd = command is not None and command.name == "lock"
        # While locked, every command except `lock` itself is refused.
        if lm is not None and lm.locked and not is_lock_cmd:
            reply("The bot is locked. Send 'lock' and the unlock password "
                  "to use it again.")
            return
        ctx = CommandContext(
            dispatcher=self, player=self.player, sources=self.sources,
            client=self.client, cfg=self.cfg, requester=requester,
            user_id=user_id, is_admin=is_admin, is_private=is_private,
            reply=reply, arg=arg.strip())
        threading.Thread(target=self._run, args=(ctx, name), daemon=True,
                         name=f"cmd-{name}").start()

    def _run(self, ctx: CommandContext, name: str) -> None:
        try:
            command = self.lookup(name)
            if command is None:
                raise UnknownCommand(
                    f"Unknown command: '{name}'. Send 'h' for the list of commands.")
            if command.admin and not ctx.is_admin:
                raise PermissionDenied(
                    "That command is for administrators only.")
            command.handler(ctx)
        except CommandError as exc:
            ctx.reply(str(exc))
        except Exception as exc:  # noqa: BLE001
            log.exception("command %s failed", name)
            ctx.reply(f"Sorry, something went wrong: {exc}")


def build_dispatcher(player, sources, client, cfg, lock_manager=None) -> Dispatcher:
    from . import handlers
    d = Dispatcher(player, sources, client, cfg, lock_manager=lock_manager)
    handlers.register_all(d)
    return d
