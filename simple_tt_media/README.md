# Simple TT Media

A simple yet robust media-player bot for [TeamTalk 5](https://bearware.dk)
servers. Point it at a server, drop it in a channel, and let people queue
music from YouTube, Spotify, or Apple Music with plain chat commands.

> This project is self-contained and unrelated to anything else in the
> surrounding repository.

```
!play daft punk around the world
!queue
!skip
```

---

## What it does

- Connects to any TeamTalk 5 server, logs in, joins a channel, and reconnects
  automatically if the connection drops.
- Plays audio into the channel by streaming media files/URLs through the
  official TeamTalk SDK.
- Resolves requests from several services behind one tidy `!play` command:
  - **YouTube** — videos, playlists, and free-text search (via `yt-dlp`).
  - **Spotify** — track / album / playlist links (metadata).
  - **Apple Music** — song / album links (metadata, no account needed).
  - **Local files & direct URLs** — anything ffmpeg can open, including
    internet-radio streams.
- A queue with now-playing announcements, skip, stop, and clear.
- Optional per-command permissions (operators).

### An honest note on Spotify & Apple Music

Spotify and Apple Music audio is DRM-protected and **cannot be restreamed**.
When you paste one of their links, Simple TT Media reads the *track info*
(title, artist, playlist contents) from the service and then plays the
matching audio from **YouTube**. That means YouTube must be enabled for
Spotify/Apple Music links to work, and the exact recording you hear may differ
from the original. This is the standard, compliant approach music bots take.

---

## Requirements

1. **Python 3.11+**
2. **The TeamTalk 5 SDK** — download it from
   <https://bearware.dk/?page_id=419> and unzip it. You need the folder that
   contains `TeamTalk5.py` and the native library for your OS
   (`libTeamTalk5.so` on Linux, `libTeamTalk5.dylib` on macOS,
   `TeamTalk5.dll` on Windows). The SDK is **not** on PyPI and is not bundled
   here.
   > The SDK runs in *trial mode* unless you supply a license key from
   > BearWare; see their site for licensing terms for non-evaluation use.
3. **ffmpeg** — the SDK uses it to decode media. Install via your OS, e.g.
   `sudo apt install ffmpeg` or `brew install ffmpeg`.
4. **A bot account on the server** with the *Transmit media files* user right.

Install the Python dependencies:

```bash
pip install -r requirements.txt
```

---

## Quick start

```bash
# 1. Install deps (see above) and have the TeamTalk SDK downloaded.
# 2. Run the interactive setup wizard — it writes config.toml for you.
python setup_bot.py

# 3. Start the bot.
python -m ttmedia --config config.toml
```

The wizard asks for your server details, personalises the bot (nickname,
status, operators), lets you pick which streaming services to enable, checks
for ffmpeg and the optional libraries, and can test the connection before you
commit.

Prefer to configure by hand? Copy `config.example.toml` to `config.toml` and
edit it.

---

## Chat commands

All commands are typed in the bot's channel (or as a private message to it)
and start with the prefix (default `!`).

| Command | Aliases | What it does |
|---|---|---|
| `!play <link or search>` | `!p` | Queue a track/playlist, or start it if idle |
| `!skip` | `!s`, `!next` | Skip the current track |
| `!queue` | `!q` | Show what's playing and what's next |
| `!nowplaying` | `!np` | Show the current track |
| `!stop` | | Stop playback and clear the queue *(operator)* |
| `!clear` | | Clear the queue, keep playing *(operator)* |
| `!sources` | | List enabled services |
| `!help` | `!h` | Show the command list |

If no operators are configured, everyone may use every command.

---

## Configuration

Config lives in `config.toml` (TOML). Secrets can be kept out of the file and
supplied via environment variables, which always win:

| Env var | Overrides |
|---|---|
| `TT_PASSWORD` | `server.password` |
| `TT_CHANNEL_PASSWORD` | `server.channel_password` |
| `SPOTIFY_CLIENT_ID` | `sources.spotify_client_id` |
| `SPOTIFY_CLIENT_SECRET` | `sources.spotify_client_secret` |
| `TEAMTALK_SDK` | `sdk.path` |

See `config.example.toml` for every option with comments.

---

## How it's put together

The code is split into small, single-responsibility pieces so it's easy to
read and extend:

```
ttmedia/
  config.py      Load/save TOML config (+ env overrides) and validate it.
  sdk.py         Locate and load the official TeamTalk SDK robustly.
  bot.py         Connect/login/join, event loop, reconnect, send messages,
                 and start/stop media streams (the transport layer).
  player.py      The queue + now-playing state machine (no SDK, testable).
  commands.py    Parse "!" commands and dispatch them.
  models.py      Track / ResolveResult data types.
  sources/       One plugin per service; a registry dispatches queries.
    base.py        The Source contract.
    youtube.py     yt-dlp audio provider + search backend.
    spotify.py     Spotify metadata -> YouTube.
    applemusic.py  Apple Music metadata (iTunes API) -> YouTube.
    local.py       Local files and direct URLs.
setup_bot.py     Interactive first-run wizard.
tests/           Unit tests (run with `pytest`; no SDK or network needed).
```

Key design choices:

- **The SDK is loaded lazily and robustly.** `sdk.py` finds `TeamTalk5.py`,
  pre-loads the native library by absolute path, and imports the wrapper — so
  you don't have to fiddle with `LD_LIBRARY_PATH`.
- **The player has no dependency on the SDK or the network**, which keeps the
  queue/skip/stop logic fully unit-tested. The bot injects itself as the
  "streamer".
- **Resolving a query never blocks the event loop** — it runs on a worker
  thread, so the bot stays responsive.
- **One source crashing never takes the bot down** — sources fail soft with a
  readable message.

### Adding a new streaming service

1. Create `ttmedia/sources/myservice.py` with a `Source` subclass implementing
   `matches()` and `resolve()`.
2. If the service has no restreamable audio, return tracks with a
   `search_query` (like Spotify/Apple Music) and the registry will play the
   match from YouTube.
3. Register it in `ttmedia/sources/__init__.py` (and add a config toggle in
   `config.py` if it needs one).

That's the whole contract — nothing else changes.

---

## Testing

```bash
pip install pytest
pytest
```

The suite covers config round-tripping, the queue state machine, the source
registry (including metadata back-fill), and command dispatch/permissions —
all without a live server or the SDK.

---

## Troubleshooting

- **"Could not find the TeamTalk 5 SDK"** — set `sdk.path` (or `TEAMTALK_SDK`)
  to the folder with `TeamTalk5.py` and the native library.
- **"could not load it … platform mismatch"** — the native library doesn't
  match your Python (e.g. 32- vs 64-bit, or wrong OS build). Re-download the
  matching SDK.
- **Login rejected** — check the username/password and that the account
  exists on the server.
- **Audio doesn't play** — confirm ffmpeg is installed and the bot account has
  the *Transmit media files* right in its channel.
- **Spotify/Apple Music links do nothing** — make sure YouTube is enabled
  (they rely on it for playback) and, for Spotify, that your client id/secret
  are set.

---

## License

MIT for this project's own code. The TeamTalk SDK and ffmpeg carry their own
licenses — review them before any non-evaluation use.
