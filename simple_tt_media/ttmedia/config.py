"""Configuration loading and saving.

Config lives in a human-editable TOML file (``config.toml`` by default).
Secrets can also come from environment variables so you never have to commit
them; an env var always wins over the file.  The dataclasses below are the
single source of truth for what a valid configuration looks like, which keeps
the setup wizard, the bot, and the tests in agreement.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - fallback for older runtimes
    tomllib = None  # type: ignore


DEFAULT_CONFIG_PATH = "config.toml"


class ConfigError(Exception):
    """Raised when configuration is missing or invalid."""


@dataclass
class ServerConfig:
    host: str = ""
    tcp_port: int = 10333
    udp_port: int = 10333
    username: str = ""
    password: str = ""          # may be supplied via TT_PASSWORD instead
    channel: str = "/"          # channel path to join, "/" is root
    channel_password: str = ""
    encrypted: bool = False     # TLS (TeamTalk 5 "encrypted server")


@dataclass
class BotConfig:
    nickname: str = "MediaBot"
    status_message: str = "Ready to play music"
    gender: str = "neutral"             # neutral | male | female
    command_prefix: str = "!"
    # Only these nicknames may use privileged commands (stop/clear/volume).
    # Empty list means "everyone may control the bot".
    operators: list = field(default_factory=list)
    reconnect_seconds: int = 5          # delay between reconnect attempts


@dataclass
class PlaybackConfig:
    announce_now_playing: bool = True   # post "Now playing ..." to the channel
    max_queue: int = 200
    # SDK media stream offset/volume knobs can grow here later.


@dataclass
class SourcesConfig:
    youtube_enabled: bool = True
    spotify_enabled: bool = False
    spotify_client_id: str = ""
    spotify_client_secret: str = ""
    applemusic_enabled: bool = False    # metadata via public iTunes Search API


@dataclass
class SdkConfig:
    # Directory that contains TeamTalk5.py and libTeamTalk5.so (or the
    # Windows/macOS equivalents).  Empty means "already importable / on path".
    path: str = ""


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    playback: PlaybackConfig = field(default_factory=PlaybackConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    sdk: SdkConfig = field(default_factory=SdkConfig)
    log_level: str = "INFO"

    # ---- validation -----------------------------------------------------

    def validate(self) -> None:
        if not self.server.host:
            raise ConfigError("server.host is required (run the setup wizard)")
        if not self.server.username:
            raise ConfigError("server.username is required")
        if self.sources.spotify_enabled and not (
            self.sources.spotify_client_id and self.sources.spotify_client_secret
        ):
            raise ConfigError(
                "Spotify is enabled but spotify_client_id / "
                "spotify_client_secret are not set"
            )

    # ---- (de)serialisation ---------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        def sub(klass, key):
            return klass(**{k: v for k, v in (data.get(key) or {}).items()
                            if k in {f for f in klass.__dataclass_fields__}})
        cfg = cls(
            server=sub(ServerConfig, "server"),
            bot=sub(BotConfig, "bot"),
            playback=sub(PlaybackConfig, "playback"),
            sources=sub(SourcesConfig, "sources"),
            sdk=sub(SdkConfig, "sdk"),
            log_level=data.get("log_level", "INFO"),
        )
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        """Let environment variables override file values for secrets."""
        env = os.environ
        self.server.password = env.get("TT_PASSWORD", self.server.password)
        self.server.channel_password = env.get(
            "TT_CHANNEL_PASSWORD", self.server.channel_password)
        self.sources.spotify_client_id = env.get(
            "SPOTIFY_CLIENT_ID", self.sources.spotify_client_id)
        self.sources.spotify_client_secret = env.get(
            "SPOTIFY_CLIENT_SECRET", self.sources.spotify_client_secret)
        if env.get("TEAMTALK_SDK"):
            self.sdk.path = env["TEAMTALK_SDK"]


def load_config(path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> Config:
    """Read a config file, applying environment overrides."""
    if tomllib is None:
        raise ConfigError("Python 3.11+ is required to read TOML config")
    p = Path(path)
    if not p.exists():
        raise ConfigError(
            f"config file not found: {p}\n"
            "Run `python setup_bot.py` to create one."
        )
    with p.open("rb") as fh:
        data = tomllib.load(fh)
    return Config.from_dict(data)


# --- minimal TOML writer (we only emit simple scalars, strings, lists) -----

def _toml_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    # string: escape backslashes and quotes
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_config(cfg: Config) -> str:
    """Serialise a :class:`Config` to TOML text (round-trips with load)."""
    data = {
        "server": asdict(cfg.server),
        "bot": asdict(cfg.bot),
        "playback": asdict(cfg.playback),
        "sources": asdict(cfg.sources),
        "sdk": asdict(cfg.sdk),
    }
    lines = [
        "# Simple TT Media configuration.",
        "# Secrets may instead be provided via environment variables:",
        "#   TT_PASSWORD, TT_CHANNEL_PASSWORD,",
        "#   SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, TEAMTALK_SDK",
        "",
        f"log_level = {_toml_value(cfg.log_level)}",
        "",
    ]
    for section, values in data.items():
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")
        lines.append("")
    return "\n".join(lines)


def save_config(cfg: Config, path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> None:
    Path(path).write_text(dump_config(cfg), encoding="utf-8")
