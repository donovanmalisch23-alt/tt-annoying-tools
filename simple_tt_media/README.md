# Simple TT Media

An advanced, easy-to-run **media player bot for [TeamTalk 5](https://bearware.dk) on Windows**.
Drop it on a server, and anyone in the channel can play music from **YouTube,
SoundCloud, Spotify, Apple Music**, or any **direct/radio stream** — just by
sending the bot a private message.

```text
p daft punk get lucky
q
seek 1:30
v 60
```

> **Windows-only by design.** The TeamTalk SDK (including media decoding) and a
> one-click setup are tuned for Windows. The whole project lives in this one
> folder and is unrelated to anything else in the repository.

---

## Highlights

- **No virtual audio cable.** Audio is decoded with ffmpeg and injected
  straight into TeamTalk's voice stream (`InsertAudioBlock`). Unlike mpv-based
  bots, there's no VB-Cable to install and configure.
- **Many sources, one command.** `p <words>` searches; `p <link>` /
  `url <link>` play. YouTube, SoundCloud, Spotify and Apple Music links all
  work (Spotify/Apple audio, which is DRM-protected, is matched and played from
  YouTube), plus direct files and internet radio.
- **A real player.** Queue, now-playing, skip/previous, pause/resume, seek,
  live volume, repeat (track/queue), shuffle — all controllable by message.
- **Friendly and chatty.** Every command replies with a clear, detailed
  message, and an unknown command returns an explicit error.
- **Robust.** Automatic reconnect, one retry on an expired stream URL, bounded
  queues/history, next-track prefetch, and responsive stop/skip (~0.3 s).
- **One-click setup.** `Setup.bat` builds an isolated environment and downloads
  ffmpeg and the Deno runtime for you; the SDK is already bundled.

---

## Requirements

1. **Windows 10/11, 64-bit.**
2. **64-bit Python 3.11 or newer** — <https://www.python.org/downloads/>
   (tick *“Add Python to PATH”* when installing).
3. **A TeamTalk account for the bot** on your server, with the
   *“transmit voice”* right. Nothing else is needed — ffmpeg, the Deno runtime
   (for YouTube) and the TeamTalk SDK are all handled for you.

Internet access is required so the bot can reach the streaming services.

---

## Quick start

1. Download/clone this folder onto the Windows machine.
2. Double-click **`Setup.bat`**. It will:
   - create a local Python environment under `.venv`,
   - install the bot and its dependencies,
   - download ffmpeg and Deno into `tools\`,
   - run a short wizard that asks for your server details and writes
     `config.toml`.
3. Double-click **`Start.bat`** to run the bot.
4. In TeamTalk, open a **private message** to the bot and send **`h`** for the
   list of commands.

Prefer the command line? After `Setup.bat` once:

```bat
.venv\Scripts\activate
ttmedia -c config.toml
```

See **[docs/SETUP.md](docs/SETUP.md)** for a step-by-step guide (including
creating the bot's server account) and **[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)**
if something doesn't work.

---

## Commands

Full list with usage and examples: **[docs/COMMANDS.md](docs/COMMANDS.md)**.
A quick taste:

| Command | Does |
|---|---|
| `p <words or link>` | Search & play, or play a link (no args = pause/resume) |
| `s <words>` then `p <n>` | Search, then pick a result by number |
| `url <link>` | Play a direct media file or internet-radio stream |
| `q` / `np` | Show the queue / the current track and position |
| `next` `prev` `pause` `resume` `stop` | Transport controls |
| `v <0-200>` `seek <m:ss>` `ff`/`rw` | Volume and seeking |
| `repeat <off/track/queue>` `shuffle` | Playback modes |
| `sv <service>` | Choose which service free-text searches (YouTube default) |
| `h` / `h <command>` | Help, or details for one command |

Administrators (set during setup) also get `say` and `quit`.

---

## How it works

```text
            private message                resolve                 decode + inject
  user  ───────────────────▶  commands ─────────────▶ sources ─────────────────────▶ TeamTalk
                                  │                   (yt-dlp /        (ffmpeg → PCM →   voice
                                  ▼                    iTunes /         InsertAudioBlock) stream
                               player  ◀── queue ──▶   Spotify)
                                  │
                                  ▼
                              audio engine (paces audio in real time)
```

| Area | File |
|---|---|
| Config (TOML + env) | `src/ttmedia/config.py` |
| SDK loader (Windows) | `src/ttmedia/sdk.py` |
| TeamTalk client (connect/login/join/reconnect, messaging) | `src/ttmedia/teamtalk/client.py` |
| Audio engine (ffmpeg → `InsertAudioBlock`, volume/pause/seek) | `src/ttmedia/teamtalk/audio.py` |
| Queue & policy (repeat/shuffle/history/prefetch) | `src/ttmedia/player.py` |
| Sources (YouTube, SoundCloud, Spotify, Apple, direct) | `src/ttmedia/sources/` |
| Commands (dispatch + handlers) | `src/ttmedia/commands/` |
| Setup wizard / tool bootstrap | `src/ttmedia/setup_wizard.py`, `bootstrap.py` |

### A note on Spotify & Apple Music

Their audio is DRM-protected and cannot be restreamed, so the bot reads the
*track information* from them and plays the matching audio from YouTube. Single
Spotify track links work with no setup; Spotify **albums/playlists** need a free
developer app (client id/secret, entered in setup). Apple Music **songs and
albums** work with no account; Apple **playlists** aren't available (Apple only
exposes them to its paid developer API).

---

## Developing

```bat
.venv\Scripts\activate
pip install -e ".[dev,fast]"
pytest
```

Adding a streaming service is a small, self-contained job: subclass `Source`
in `src/ttmedia/sources/`, implement `matches` / `from_url` (and `search`), and
register it in `sources/__init__.py`.

---

## Licensing

This project's own code is MIT (see `LICENSE`). The bundled **TeamTalk 5 SDK**
is © BearWare.dk and is governed by `sdk/License.txt` (it runs in trial mode
without a license key). **ffmpeg** and **Deno**, downloaded by setup, carry
their own licenses. You are responsible for complying with the terms of the
streaming services you use.
