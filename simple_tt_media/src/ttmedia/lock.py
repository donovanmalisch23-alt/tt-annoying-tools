"""Lock / unlock the bot.

Flow (driven by the single ``lock`` command):

* An **admin** sending ``lock`` while the bot is **unlocked** is asked, in a
  private message, for a password to set. Their next private message becomes
  that password; the bot then locks and announces it in the channel.
* While **locked**, every other command is refused. **Anyone** may send
  ``lock`` to start unlocking: the bot asks them (privately) for the password,
  and unlocks if their next private message matches.
* A **non-admin** sending ``lock`` while unlocked just gets an error.

The password is held in memory only (it is chosen fresh each time the bot is
locked), so a restart always comes back unlocked. The per-user "I'm waiting for
your password" state is what makes the next private message a password rather
than a command; sending ``cancel`` aborts it.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional, Tuple

from .log import get_logger

log = get_logger(__name__)

_SET = "set"
_UNLOCK = "unlock"


class LockManager:
    def __init__(self, client) -> None:
        self._client = client
        self._lock = threading.Lock()
        self._locked = False
        self._password = ""
        # user_id -> (kind, nickname) while we wait for that user's password.
        self._pending: Dict[int, Tuple[str, str]] = {}

    @property
    def locked(self) -> bool:
        return self._locked

    def has_pending(self, user_id: int) -> bool:
        with self._lock:
            return user_id in self._pending

    # ---- the `lock` command --------------------------------------------

    def request(self, user_id: int, nickname: str, is_admin: bool,
                is_private: bool, reply) -> None:
        """Handle a ``lock`` command from a user."""
        with self._lock:
            locked = self._locked
        if not locked:
            if not is_admin:
                reply("You are not allowed to lock the bot — only administrators can.")
                return
            with self._lock:
                self._pending[user_id] = (_SET, nickname)
            self._client.reply_private(
                user_id, "You are about to LOCK the bot. Reply here with the "
                         "password you want to set (or send 'cancel' to abort).")
            if not is_private:
                reply("Check your private messages to set the lock password.")
        else:
            with self._lock:
                self._pending[user_id] = (_UNLOCK, nickname)
            self._client.reply_private(
                user_id, "The bot is LOCKED. Reply here with the unlock "
                         "password (or send 'cancel' to abort).")
            if not is_private:
                reply("Check your private messages to enter the unlock password.")

    # ---- the follow-up password message --------------------------------

    def submit_password(self, user_id: int, text: str) -> None:
        """Handle the private message a pending user sends as their password."""
        with self._lock:
            entry = self._pending.pop(user_id, None)
        if entry is None:
            return
        kind, nickname = entry
        password = text.strip()

        if password.lower() == "cancel":
            self._client.reply_private(user_id, "Cancelled; nothing changed.")
            return

        if kind == _SET:
            if not password:
                self._client.reply_private(
                    user_id, "That password was empty, so the bot was not "
                             "locked. Send 'lock' to try again.")
                return
            with self._lock:
                self._password = password
                self._locked = True
            log.info("Bot locked by %s.", nickname)
            self._client.reply_private(user_id, "Done — the bot is now locked.")
            self._client.send_channel(
                f"The bot has been locked by {nickname}. "
                "Commands are disabled until it is unlocked.")
            return

        # kind == _UNLOCK
        with self._lock:
            correct = bool(self._password) and password == self._password
            if correct:
                self._locked = False
                self._password = ""
        if correct:
            log.info("Bot unlocked by %s.", nickname)
            self._client.reply_private(user_id, "Correct — the bot is now unlocked.")
            self._client.send_channel(f"The bot has been unlocked by {nickname}.")
        else:
            self._client.reply_private(
                user_id, "Incorrect password. The bot is still locked. "
                         "Send 'lock' to try again.")
