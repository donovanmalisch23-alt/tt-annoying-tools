#!/usr/bin/env python3
"""Interactive setup wizard for Simple TT Media.

Run this once to create your ``config.toml``:

    python setup_bot.py

It walks you through connecting the bot to a TeamTalk server, personalising
it (nickname, status, who can control it), and choosing which streaming
services to enable. It also checks for ffmpeg and the optional Python
dependencies, and can test the connection before you finish.

The wizard is intentionally forgiving: every prompt has a sensible default
(shown in brackets), and nothing is written until you confirm at the end.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

# Make the package importable whether or not it's installed.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ttmedia.config import (  # noqa: E402
    BotConfig, Config, PlaybackConfig, ServerConfig, SourcesConfig, SdkConfig,
    DEFAULT_CONFIG_PATH, dump_config, save_config,
)
from ttmedia.sdk import (  # noqa: E402
    SDK_DOWNLOAD, SdkNotFoundError, find_sdk_dir, load_sdk,
)


# --- small prompt helpers --------------------------------------------------

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
        value = ask(f"{prompt} ({d})").lower()
        if not value:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("  Please answer y or n.")


def ask_int(prompt: str, default: int) -> int:
    while True:
        value = ask(prompt, str(default))
        try:
            return int(value)
        except ValueError:
            print("  Please enter a number.")


def header(title: str) -> None:
    print(f"\n=== {title} ===")


# --- wizard sections -------------------------------------------------------

def configure_sdk() -> SdkConfig:
    header("TeamTalk SDK")
    found = find_sdk_dir()
    if found:
        print(f"Found the TeamTalk SDK at: {found}")
        if ask_bool("Use this SDK location?", True):
            _verify_sdk(str(found))
            return SdkConfig(path=str(found))
    print(f"The TeamTalk 5 SDK is required. Download it from:\n  {SDK_DOWNLOAD}")
    print("Then point to the folder that contains TeamTalk5.py and the "
          "native library (libTeamTalk5.so / .dylib / TeamTalk5.dll).")
    path = ask("SDK folder path (blank if already on PYTHONPATH)", str(found or ""))
    _verify_sdk(path)
    return SdkConfig(path=path)


def _verify_sdk(path: str) -> bool:
    """Actually load the SDK now so import problems surface at setup time.

    Non-fatal: prints a clear result and returns True/False. Setup continues
    either way so a config file is still written.
    """
    print("Verifying the SDK loads on this machine...")
    try:
        tt = load_sdk(path)
        version = tt.ttstr(tt.getVersion())
        print(f"[ok] TeamTalk SDK loaded (version {version}).")
        return True
    except SdkNotFoundError as exc:
        print(f"[!!] The SDK could not be loaded:\n     {exc}")
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] Unexpected error loading the SDK: {exc}")
        return False


def configure_server() -> ServerConfig:
    header("Server connection")
    s = ServerConfig()
    s.host = ask("Server host (address)", s.host)
    s.tcp_port = ask_int("TCP port", s.tcp_port)
    s.udp_port = ask_int("UDP port", s.udp_port)
    s.encrypted = ask_bool("Is this an encrypted (TLS) server?", False)
    print("\nThe bot needs an account on the server with the "
          "'Transmit media files' right.")
    s.username = ask("Bot account username", s.username)
    s.password = ask("Bot account password (leave blank to set via "
                     "TT_PASSWORD env var)", "")
    s.channel = ask("Channel path to join (e.g. / or /Music)", s.channel)
    s.channel_password = ask("Channel password (if any)", "")
    return s


def configure_bot() -> BotConfig:
    header("Personalise the bot")
    b = BotConfig()
    b.nickname = ask("Display nickname", b.nickname)
    b.status_message = ask("Status message", b.status_message)
    b.command_prefix = ask("Command prefix", b.command_prefix)
    print("\nOperators may use privileged commands (stop, clear).")
    print("Leave blank to let everyone control the bot.")
    ops = ask("Operator nicknames (comma-separated)", "")
    b.operators = [o.strip() for o in ops.split(",") if o.strip()]
    return b


def configure_playback() -> PlaybackConfig:
    header("Playback preferences")
    pb = PlaybackConfig()
    pb.default_volume = ask_int("Default volume (percent)", pb.default_volume)
    loop = ask("Loop mode (off/track/queue)", pb.loop).lower()
    pb.loop = loop if loop in ("off", "track", "queue") else "off"
    pb.shuffle = ask_bool("Shuffle by default?", False)
    pb.autoplay = ask_bool(
        "Autoplay related tracks when the queue empties?", False)
    pb.announce_now_playing = ask_bool("Announce 'Now playing' in channel?", True)
    return pb


def configure_sources() -> SourcesConfig:
    header("Streaming services")
    src = SourcesConfig()
    print("YouTube provides playable audio (via yt-dlp) and powers search.")
    src.youtube_enabled = ask_bool("Enable YouTube?", True)

    print("\nSpotify & Apple Music audio is DRM-protected and cannot be "
          "restreamed.\nWhen enabled, the bot reads their track/playlist info "
          "and plays the\nmatching audio from YouTube. (YouTube must be enabled.)")

    src.spotify_enabled = ask_bool("Enable Spotify links?", False)
    if src.spotify_enabled:
        print("Create a free app at https://developer.spotify.com/dashboard "
              "for these:")
        src.spotify_client_id = ask("Spotify client id (or set "
                                    "SPOTIFY_CLIENT_ID env var)", "")
        src.spotify_client_secret = ask("Spotify client secret (or set "
                                       "SPOTIFY_CLIENT_SECRET env var)", "")

    src.applemusic_enabled = ask_bool(
        "Enable Apple Music links? (no account needed)", False)

    if (src.spotify_enabled or src.applemusic_enabled) and not src.youtube_enabled:
        print("\nNote: Spotify/Apple Music need YouTube for playback. "
              "Enabling YouTube.")
        src.youtube_enabled = True
    return src


# --- environment checks ----------------------------------------------------

def check_environment(sources: SourcesConfig) -> None:
    header("Environment checks")
    if sys.platform == "win32":
        print("[ok] Media decoding is built into the bundled TeamTalk5.dll "
              "(no ffmpeg needed).")
    elif shutil.which("ffmpeg"):
        print("[ok] ffmpeg found.")
    else:
        print("[!!] ffmpeg NOT found. The TeamTalk SDK needs ffmpeg/avconv to "
              "decode media.\n     Install it (e.g. `apt install ffmpeg`) "
              "before streaming.")

    def have(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:  # noqa: BLE001
            return False

    if sources.youtube_enabled:
        print("[ok] yt-dlp installed." if have("yt_dlp")
              else "[!!] yt-dlp missing - run `pip install yt-dlp`.")
    if sources.spotify_enabled:
        print("[ok] spotipy installed." if have("spotipy")
              else "[!!] spotipy missing - run `pip install spotipy`.")


def maybe_test_connection(cfg: Config) -> None:
    if not ask_bool("\nTest the connection to the server now?", False):
        return
    print("Connecting... (Ctrl-C to abort)")
    try:
        from ttmedia.bot import MediaBot
        bot = MediaBot(cfg)
        # Run a few event-loop ticks to reach login/join, then disconnect.
        bot._connect_sequence()  # noqa: SLF001 - intentional for the wizard
        for _ in range(8):
            bot._client.runEventLoop(500)  # noqa: SLF001
            if bot._my_user_id > 0:
                break
        if bot._my_user_id > 0:  # noqa: SLF001
            print("[ok] Logged in successfully.")
        else:
            print("[!!] Connected but did not log in - check credentials.")
        bot._safe_disconnect()  # noqa: SLF001
        bot._client.closeTeamTalk()  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001
        print(f"[!!] Connection test failed: {exc}")


# --- main ------------------------------------------------------------------

def main() -> int:
    print("Simple TT Media - setup wizard")
    print("------------------------------")
    if not sys.stdin.isatty():
        print("This wizard needs an interactive terminal.")
        return 1

    cfg = Config(
        sdk=configure_sdk(),
        server=configure_server(),
        bot=configure_bot(),
        sources=configure_sources(),
        playback=configure_playback(),
    )
    check_environment(cfg.sources)

    header("Review")
    print(dump_config(cfg))

    path = ask("Save to", DEFAULT_CONFIG_PATH)
    if Path(path).exists() and not ask_bool(
            f"{path} exists. Overwrite?", False):
        print("Not saved.")
        return 1
    try:
        save_config(cfg, path)
    except OSError as exc:
        print(f"\n[!!] Failed to write {path}: {exc}")
        return 1
    if not Path(path).is_file():
        print(f"\n[!!] {path} was not created.")
        return 1
    print(f"\n[ok] Saved {path}.")

    maybe_test_connection(cfg)

    print("\nAll set! Start the bot with:")
    print("  python -m ttmedia --config", path)
    print("Then, in your TeamTalk channel, type:  "
          f"{cfg.bot.command_prefix}play never gonna give you up")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nAborted.")
        sys.exit(130)
