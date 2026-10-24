"""Interactive first-run setup: creates config.toml.

Run via ``ttmedia-setup`` (installed by pip) or ``python -m ttmedia.setup_wizard``.
Every prompt has a sensible default; nothing is written until you confirm, and
the SDK is actually loaded so problems surface now rather than at first run.
"""

from __future__ import annotations

import sys
from pathlib import Path

from .config import (AudioConfig, BotConfig, Config, DEFAULT_CONFIG_PATH,
                     ServerConfig, SourcesConfig, dump_config, save_config)
from .errors import SdkError
from .sdk import load_sdk
from .tools import find_deno, find_ffmpeg


def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return value or default


def ask_bool(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    while True:
        v = ask(f"{prompt} ({d})").lower()
        if not v:
            return default
        if v in ("y", "yes"):
            return True
        if v in ("n", "no"):
            return False
        print("  Please answer y or n.")


def ask_int(prompt: str, default: int) -> int:
    while True:
        try:
            return int(ask(prompt, str(default)))
        except ValueError:
            print("  Please enter a whole number.")


def header(t: str) -> None:
    print(f"\n=== {t} ===")


def configure_server() -> ServerConfig:
    header("TeamTalk server")
    s = ServerConfig()
    s.host = ask("Server address (host)", s.host)
    s.tcp_port = ask_int("TCP port", s.tcp_port)
    s.udp_port = ask_int("UDP port", s.udp_port)
    s.encrypted = ask_bool("Encrypted (TLS) server?", False)
    print("\nThe bot needs its own account with the 'transmit voice' right.")
    s.username = ask("Bot account username", s.username)
    s.password = ask("Bot account password (blank = set TTMEDIA_PASSWORD instead)", "")
    s.nickname = ask("Display nickname", s.nickname)
    s.channel = ask("Channel to join (path, / is root)", s.channel)
    s.channel_password = ask("Channel password (if any)", "")
    s.status_message = ask("Status message", s.status_message)
    return s


def configure_bot() -> BotConfig:
    header("Control & permissions")
    b = BotConfig()
    print("Anyone can private-message the bot to control playback.")
    admins = ask("Admin usernames for 'quit'/'say' (comma-separated, optional)", "")
    b.admins = [a.strip() for a in admins.split(",") if a.strip()]
    if ask_bool("Also accept commands typed in the channel (with a prefix)?", False):
        b.channel_prefix = ask("Channel command prefix", "!")
    b.announce_now_playing = ask_bool("Announce 'Now playing' in the channel?", True)
    return b


def configure_sources() -> SourcesConfig:
    header("Streaming services")
    s = SourcesConfig()
    s.youtube = ask_bool("Enable YouTube (search + links)?", True)
    s.soundcloud = ask_bool("Enable SoundCloud (search + links)?", True)
    s.applemusic = ask_bool("Enable Apple Music links/search?", True)
    s.spotify = ask_bool("Enable Spotify links?", True)
    if s.spotify:
        print("Single Spotify track links work with no setup. For albums and "
              "playlists, add a free app from developer.spotify.com:")
        s.spotify_client_id = ask("  Spotify client id (optional)", "")
        s.spotify_client_secret = ask("  Spotify client secret (optional)", "")
    s.direct_url = ask_bool("Enable direct media / internet-radio links?", True)
    return s


def configure_audio() -> AudioConfig:
    header("Audio")
    a = AudioConfig()
    a.default_volume = ask_int("Default volume (percent)", a.default_volume)
    a.max_volume = ask_int("Maximum volume users may set (percent)", a.max_volume)
    a.max_track_seconds = ask_int("Max track length in seconds (0 = no limit)", 0)
    return a


def check_tools(sources: SourcesConfig) -> None:
    header("Tools")
    ff = find_ffmpeg()
    print(f"[ok] ffmpeg: {ff}" if ff else
          "[!!] ffmpeg NOT found. Run Setup.bat, or install ffmpeg. "
          "Playback needs it.")
    deno = find_deno()
    if sources.youtube:
        print(f"[ok] JS runtime for YouTube: {deno}" if deno else
              "[!!] No deno/node found. YouTube needs one; Setup.bat installs deno.")


def verify_sdk() -> None:
    header("TeamTalk SDK")
    print("Loading the bundled SDK ...")
    try:
        tt = load_sdk("")
        print(f"[ok] SDK loaded (version {tt.ttstr(tt.getVersion())}).")
    except SdkError as exc:
        print(f"[!!] {exc}")
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] Unexpected error loading the SDK: {exc}")


def main() -> int:
    print("Simple TT Media — setup")
    print("-----------------------")
    if not sys.stdin.isatty():
        print("This wizard needs an interactive terminal.")
        return 1

    verify_sdk()
    cfg = Config(
        server=configure_server(),
        bot=configure_bot(),
        sources=configure_sources(),
        audio=configure_audio(),
    )
    check_tools(cfg.sources)

    header("Review")
    print(dump_config(cfg))

    path = DEFAULT_CONFIG_PATH
    if not ask_bool(f"Save this configuration to {path}?", True):
        path = ask("Enter a different filename (or leave blank to cancel)", "")
        if not path:
            print("Not saved. Run Setup again when you're ready.")
            return 1
    if Path(path).exists() and not ask_bool(f"{path} already exists. Overwrite it?", True):
        print("Not saved. Run Setup again when you're ready.")
        return 1
    try:
        save_config(cfg, path)
    except OSError as exc:
        print(f"[!!] Could not write {path}: {exc}")
        return 1
    if not Path(path).is_file():
        print(f"[!!] {path} was not created.")
        return 1
    print(f"\n[ok] Saved {path}.")
    print("\nStart the bot with Start.bat (or: ttmedia -c config.toml).")
    print(f"Then private-message '{cfg.server.nickname}' the letter 'h' for help.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
