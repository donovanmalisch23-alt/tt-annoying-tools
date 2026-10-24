"""Configuration: load/validate/save a human-editable TOML file.

Secrets may be supplied via environment variables (which always win over the
file), so a committed or shared config never has to contain a password.
The dataclasses are the single source of truth for valid settings and keep
the wizard, the bot, and the tests in agreement.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, List

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore

from .errors import ConfigError

DEFAULT_CONFIG_PATH = "config.toml"


@dataclass
class ServerConfig:
    host: str = ""
    tcp_port: int = 10333
    udp_port: int = 10333
    encrypted: bool = False
    username: str = ""
    password: str = ""
    nickname: str = "MediaBot"
    status_message: str = "Send me a private message with 'h' for help"
    channel: str = "/"
    channel_password: str = ""


@dataclass
class BotConfig:
    # Admin usernames (TeamTalk account names). Server administrators are
    # always treated as admins too. Admin-only commands are refused otherwise.
    admins: List[str] = field(default_factory=list)
    # Prefix required for commands typed in a *channel*. Private messages are
    # always treated as commands. Empty string disables channel commands.
    channel_prefix: str = ""
    reconnect_seconds: int = 5
    # Announce the current track in the channel as it starts.
    announce_now_playing: bool = True


@dataclass
class AudioConfig:
    sample_rate: int = 48000
    channels: int = 2
    frame_ms: int = 20            # PCM frame size fed to the SDK
    lead_ms: int = 350            # how far ahead of real time we buffer
    default_volume: int = 100     # percent
    max_volume: int = 200         # percent ceiling users may set
    max_track_seconds: int = 0    # 0 = unlimited; else refuse longer tracks


@dataclass
class SourcesConfig:
    youtube: bool = True
    soundcloud: bool = True
    spotify: bool = True          # metadata -> resolved via YouTube
    spotify_client_id: str = ""   # optional; enables Spotify albums/playlists
    spotify_client_secret: str = ""
    applemusic: bool = True       # metadata -> resolved via YouTube
    direct_url: bool = True       # direct media URLs and internet radio
    max_search_results: int = 20
    max_playlist_tracks: int = 500


@dataclass
class ToolsConfig:
    # Paths to ffmpeg and the deno JS runtime. Empty = auto-detect the copies
    # under tools/ (installed by setup) and then fall back to PATH.
    ffmpeg_path: str = ""
    deno_path: str = ""


@dataclass
class SdkConfig:
    path: str = ""                # override; empty = use the bundled SDK


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    bot: BotConfig = field(default_factory=BotConfig)
    audio: AudioConfig = field(default_factory=AudioConfig)
    sources: SourcesConfig = field(default_factory=SourcesConfig)
    tools: ToolsConfig = field(default_factory=ToolsConfig)
    sdk: SdkConfig = field(default_factory=SdkConfig)
    log_level: str = "INFO"
    log_file: str = ""

    # ---- validation -----------------------------------------------------

    def validate(self) -> None:
        if not self.server.host:
            raise ConfigError("server.host is required (run Setup to create a config).")
        if not self.server.username:
            raise ConfigError("server.username is required.")
        if self.audio.channels not in (1, 2):
            raise ConfigError("audio.channels must be 1 or 2.")
        if self.audio.frame_ms < 10 or self.audio.frame_ms > 100:
            raise ConfigError("audio.frame_ms must be between 10 and 100.")
        if self.audio.default_volume < 0 or self.audio.default_volume > self.audio.max_volume:
            raise ConfigError("audio.default_volume must be between 0 and max_volume.")

    # ---- (de)serialisation ---------------------------------------------

    @classmethod
    def from_dict(cls, data: dict) -> "Config":
        def sub(klass, key):
            raw = data.get(key) or {}
            fields = set(klass.__dataclass_fields__)
            return klass(**{k: v for k, v in raw.items() if k in fields})

        cfg = cls(
            server=sub(ServerConfig, "server"),
            bot=sub(BotConfig, "bot"),
            audio=sub(AudioConfig, "audio"),
            sources=sub(SourcesConfig, "sources"),
            tools=sub(ToolsConfig, "tools"),
            sdk=sub(SdkConfig, "sdk"),
            log_level=data.get("log_level", "INFO"),
            log_file=data.get("log_file", ""),
        )
        cfg._apply_env()
        return cfg

    def _apply_env(self) -> None:
        env = os.environ
        self.server.password = env.get("TTMEDIA_PASSWORD", self.server.password)
        self.server.channel_password = env.get(
            "TTMEDIA_CHANNEL_PASSWORD", self.server.channel_password)
        self.sources.spotify_client_id = env.get(
            "SPOTIFY_CLIENT_ID", self.sources.spotify_client_id)
        self.sources.spotify_client_secret = env.get(
            "SPOTIFY_CLIENT_SECRET", self.sources.spotify_client_secret)
        if env.get("TTMEDIA_SDK"):
            self.sdk.path = env["TTMEDIA_SDK"]


def load_config(path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> Config:
    if tomllib is None:
        raise ConfigError("Python 3.11 or newer is required.")
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"Config file not found: {p}. Run Setup.bat to create one.")
    try:
        with p.open("rb") as fh:
            data = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {p} is not valid TOML: {exc}") from exc
    return Config.from_dict(data)


# --- minimal TOML writer (we only emit scalars, strings and string lists) ---

def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    s = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{s}"'


def dump_config(cfg: Config) -> str:
    sections = {
        "server": asdict(cfg.server),
        "bot": asdict(cfg.bot),
        "audio": asdict(cfg.audio),
        "sources": asdict(cfg.sources),
        "tools": asdict(cfg.tools),
        "sdk": asdict(cfg.sdk),
    }
    lines = [
        "# Simple TT Media configuration.",
        "# Secrets may instead be set via environment variables:",
        "#   TTMEDIA_PASSWORD, TTMEDIA_CHANNEL_PASSWORD, TTMEDIA_SDK",
        "",
        f"log_level = {_toml_scalar(cfg.log_level)}",
        f"log_file = {_toml_scalar(cfg.log_file)}",
        "",
    ]
    for name, values in sections.items():
        lines.append(f"[{name}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_scalar(value)}")
        lines.append("")
    return "\n".join(lines)


def save_config(cfg: Config, path: str | os.PathLike = DEFAULT_CONFIG_PATH) -> None:
    Path(path).write_text(dump_config(cfg), encoding="utf-8")
