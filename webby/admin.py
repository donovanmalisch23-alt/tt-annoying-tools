"""Admin credentials, sessions and login throttling.

Editing the allowlist is the one privileged action, so it needs a real
credential check: PBKDF2-HMAC-SHA256 with a per-file random salt, a constant-time
comparison, an in-memory session token with an expiry, and a bounded number of
failed attempts per client before it is told to slow down.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 240_000
MIN_PASSWORD_LENGTH = 8


class AdminError(RuntimeError):
    """Raised when the admin credential file cannot be used."""


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def hash_password(
    password: str, *, iterations: int = ITERATIONS, salt: bytes | None = None
) -> str:
    if not password:
        raise AdminError("the admin password cannot be empty")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AdminError(f"the admin password must be at least {MIN_PASSWORD_LENGTH} characters")
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"{ALGORITHM}${iterations}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt, digest = encoded.split("$", 3)
        if algorithm != ALGORITHM:
            return False
        candidate = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _unb64(salt), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


def generate_password(length: int = 20) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass(frozen=True)
class AdminRecord:
    username: str
    password_hash: str
    created_at: float
    updated_at: float


class AdminStore:
    """Reads and writes the admin credential file (0600, atomic replace)."""

    def __init__(self, path: Path, env_user: str | None = None, env_password: str | None = None):
        self.path = path
        self._env_user = env_user or None
        self._env_password = env_password or None

    # ----- environment fallback -------------------------------------------- #

    @property
    def uses_env(self) -> bool:
        return bool(self._env_user and self._env_password)

    def load(self) -> AdminRecord | None:
        if self.uses_env:
            return AdminRecord(
                username=self._env_user or "admin",
                password_hash=f"env${self._env_password}",
                created_at=0.0,
                updated_at=0.0,
            )
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise AdminError(f"cannot read {self.path}: {exc}") from exc
        if not isinstance(data, dict) or not data.get("password_hash"):
            raise AdminError(f"{self.path} does not contain a usable admin record")
        return AdminRecord(
            username=str(data.get("username") or "admin"),
            password_hash=str(data["password_hash"]),
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
        )

    @property
    def configured(self) -> bool:
        if self.uses_env:
            return True
        return self.path.is_file()

    def verify(self, username: str, password: str) -> bool:
        record = self.load()
        if record is None:
            return False
        # Always spend the KDF time, so a wrong username is not faster than a
        # wrong password.
        if record.password_hash.startswith("env$"):
            expected = record.password_hash.split("$", 1)[1]
            username_ok = hmac.compare_digest(username, record.username)
            return username_ok and hmac.compare_digest(password, expected)
        password_ok = verify_password(password, record.password_hash)
        return hmac.compare_digest(username, record.username) and password_ok

    def save(self, username: str, password: str, *, force: bool = True) -> AdminRecord:
        if not force and self.path.exists():
            raise AdminError(f"{self.path} already exists (pass --force to replace it)")
        now = time.time()
        existing = None
        try:
            existing = self.load()
        except AdminError:
            existing = None
        record = AdminRecord(
            username=username,
            password_hash=hash_password(password),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        payload = {
            "username": record.username,
            "password_hash": record.password_hash,
            "algorithm": ALGORITHM,
            "iterations": ITERATIONS,
            "created_at": record.created_at,
            "updated_at": record.updated_at,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent), prefix=".admin-", delete=False
        )
        try:
            with handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(handle.name, 0o600)
            os.replace(handle.name, self.path)
        except BaseException:
            try:
                os.unlink(handle.name)
            except OSError:
                pass
            raise
        return record


@dataclass
class Session:
    token: str
    username: str
    expires_at: float


class SessionStore:
    """In-memory bearer tokens. Restarting the bridge logs everyone out."""

    def __init__(self, ttl: float, token_bytes: int = 32):
        self.ttl = ttl
        self._token_bytes = token_bytes
        self._sessions: dict[str, Session] = {}

    def issue(self, username: str) -> Session:
        self.prune()
        token = secrets.token_urlsafe(self._token_bytes)
        session = Session(token=token, username=username, expires_at=time.time() + self.ttl)
        self._sessions[token] = session
        return session

    def validate(self, token: str | None) -> Session | None:
        if not token:
            return None
        session = self._sessions.get(token)
        if session is None:
            return None
        if session.expires_at <= time.time():
            self._sessions.pop(token, None)
            return None
        return session

    def revoke(self, token: str) -> None:
        self._sessions.pop(token, None)

    def prune(self) -> None:
        now = time.time()
        for token, session in list(self._sessions.items()):
            if session.expires_at <= now:
                self._sessions.pop(token, None)


class LoginThrottle:
    """Bounded failed logins per client address."""

    def __init__(self, max_failures: int = 5, window: float = 60.0):
        self.max_failures = max_failures
        self.window = window
        self._failures: dict[str, list[float]] = {}

    def blocked_for(self, key: str) -> float:
        now = time.time()
        attempts = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window]
        self._failures[key] = attempts
        if len(attempts) < self.max_failures:
            return 0.0
        return max(0.0, self.window - (now - min(attempts)))

    def record_failure(self, key: str) -> None:
        self._failures.setdefault(key, []).append(time.time())

    def record_success(self, key: str) -> None:
        self._failures.pop(key, None)
