# Simple TT Media

A fast, clean **media player bot for [TeamTalk 5](https://bearware.dk) on Windows**.
Drop it on a server and anyone in the channel can play music from **YouTube,
SoundCloud, Spotify, Apple Music**, or any **direct / radio stream** — just by
private-messaging the bot.

```text
play daft punk - get lucky
queue
seek 1:30
volume 60
```

> **Windows-only by design.** The TeamTalk SDK (including media decoding) and a
> one-click setup are tuned for Windows. The whole project lives in this one
> folder and is unrelated to anything else in the repository.

---

## Why it's nice

- **No virtual audio cable.** Audio is decoded with ffmpeg and injected
  straight into TeamTalk's voice stream. Unlike mpv-based bots there's no
  VB-Cable to install or configure.
- **Smooth, never stuttering.** A reader thread keeps a ~2.5-second decode
  buffer so network hiccups don't break the sound, while a separate sender
  keeps the stop/skip response fast.
- **Highest quality available.** yt-dlp picks the best audio-only stream (Opus
  first), decoded at 48 kHz to match TeamTalk's codec.
- **Blazing-fast search.** Searches use flat extraction and the next track is
  resolved in the background while the current one plays, so playback starts
  quickly and tracks change seamlessly.
- **Simple, consistent commands.** One clear verb per action, short aliases,
  and `help <command>` for details on anything.
- **Safe by default.** Per-user rate limiting, a bounded worker pool, an
  ffmpeg protocol whitelist and I/O timeout, authenticated admin checks
  (by account, not spoofable nickname), and a password-protected `lock`.
- **One-click setup.** `Setup.bat` builds an isolated environment and downloads
  ffmpeg and Deno for you (in parallel, with timeouts — it never hangs). The
  TeamTalk SDK is already bundled.

---

## Requirements

1. **Windows 10/11, 64-bit.**
2. **64-bit Python 3.11+** — <https://www.python.org/downloads/> (tick
   *“Add Python to PATH”*).
3. **A TeamTalk account for the bot** on your server, with the *transmit voice*
   right.

Everything else — ffmpeg, the Deno runtime (for YouTube) and the TeamTalk SDK —
is handled for you. Internet access is required to reach the services.

---

## Quick start

1. Copy this folder onto the Windows machine.
2. Double-click **`Setup.bat`**. It creates `.venv`, installs the bot, downloads
   ffmpeg + Deno into `tools\`, and runs a short wizard that writes
   `config.toml`.
3. Double-click **`Start.bat`** to run the bot.
4. In TeamTalk, **private-message** the bot `help`.

Command line instead:

```bat
.venv\Scripts\python -m ttmedia -c config.toml
```

See **[docs/SETUP.md](docs/SETUP.md)** for a full walkthrough (including making
the bot's server account) and **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**
if anything misbehaves.

---

## Commands

Full reference with usage and examples: **[docs/COMMANDS.md](docs/COMMANDS.md)**.
A quick taste:

| Command | Does |
|---|---|
| `play <search or link>` | Search & play, or play a link (no argument = pause/resume) |
| `playnext <…>` | Queue something to play next |
| `search <words>` → `pick <n>` | Search, then choose a result |
| `url <link>` | Play a direct file or internet-radio stream |
| `queue` / `now` | Show the queue / the current track and position |
| `skip` `back` `pause` `resume` `stop` `restart` | Transport |
| `volume <0-200>` `seek <m:ss>` `forward` `rewind` | Volume & seeking |
| `repeat <off/one/all>` `shuffle` `remove <n>` `clear` | Queue control |
| `service <name>` | Choose which service free-text searches (YouTube default) |
| `lock` | Lock/unlock the bot with a password |
| `help` / `help <command>` | Help, or details for one command |

Admins (set in `config.toml`) also get `say` and `quit`.

---

## How it works

```text
          private message              resolve                  decode + inject
 user ──────────────────▶ commands ───────────────▶ sources ───────────────────────▶ TeamTalk
                             │        (yt-dlp /       (ffmpeg → PCM → reader buffer    voice
                             ▼         iTunes /        → paced sender → InsertAudioBlock)
                          player ◀── queue ──▶         Spotify)
```

| Area | File |
|---|---|
| Config (TOML + env vars) | `src/ttmedia/config.py` |
| SDK loader (Windows) | `src/ttmedia/sdk.py` |
| TeamTalk client (connect/login/join/reconnect, messaging) | `src/ttmedia/teamtalk/client.py` |
| Audio engine (ffmpeg → reader buffer → paced sender) | `src/ttmedia/teamtalk/audio.py` |
| Queue & policy (repeat/shuffle/history/prefetch) | `src/ttmedia/player.py` |
| Sources (YouTube, SoundCloud, Spotify, Apple, direct) | `src/ttmedia/sources/` |
| Commands (dispatch + handlers) + rate limiting | `src/ttmedia/commands/` |
| Lock / unlock | `src/ttmedia/lock.py` |
| Setup wizard / tool bootstrap | `src/ttmedia/setup_wizard.py`, `bootstrap.py` |

### Spotify & Apple Music

Their audio is DRM-protected and can't be restreamed, so the bot reads the
*track information* and plays the matching audio from YouTube. Single Spotify
track links work with no setup; Spotify **albums/playlists** need a free
developer app (client id/secret, entered in setup). Apple Music **songs and
albums** work with no account; Apple **playlists** aren't available (Apple only
exposes them to its paid developer API).

---

## Developing

```bat
.venv\Scripts\python -m pip install -e ".[dev,fast]"
.venv\Scripts\python -m pytest
```

Adding a service is small and self-contained: subclass `Source` in
`src/ttmedia/sources/`, implement `matches` / `from_url` (and `search`), and
register it in `sources/__init__.py`.

---

## Licensing

This project's own code is MIT (see `LICENSE`). The bundled **TeamTalk 5 SDK**
is © BearWare.dk and governed by `sdk/License.txt` (it runs in trial mode
without a license key). **ffmpeg** and **Deno**, downloaded by setup, carry
their own licenses. Use of the streaming services is subject to their terms.
