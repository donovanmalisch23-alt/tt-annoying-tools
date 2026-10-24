# Setup guide (Windows)

From nothing to a running bot in a few minutes.

## 1. Install Python (once)

Download **64-bit Python 3.11+** from <https://www.python.org/downloads/> and
tick **“Add python.exe to PATH”**. The bundled TeamTalk DLL is 64-bit, so
32-bit Python will not work — Setup checks this and tells you.

## 2. Create the bot's account on your TeamTalk server

The bot logs in like any user and needs the **transmit voice** right (it sends
audio as if it were talking). Using the TeamTalk client as a server admin:

1. **Server → Accounts…** (or your server's admin tool).
2. Create a normal user account, e.g. username `mediabot` with a password.
3. Ensure its rights include **transmit voice / microphone** (default accounts
   usually do).
4. Pick or create the channel you want the bot to sit in.

To let specific people use the admin-only commands (`say`, `quit`, and locking),
note their **TeamTalk account usernames** — you'll enter them in the wizard.
Admin checks use the account name (authenticated by the server), not the
display nickname, so they can't be spoofed. Server administrators are always
treated as admins.

## 3. Run Setup

Double-click **`Setup.bat`**. It will:

1. create an isolated environment in `.venv` (nothing is installed
   system-wide);
2. install the bot and `yt-dlp` (pip runs with timeouts and retries, so it
   won't hang);
3. download **ffmpeg** and **Deno** into `tools\`, in parallel and with time
   limits — a slow mirror can't stall setup (ffmpeg plays the audio; Deno lets
   yt-dlp solve YouTube's challenges);
4. run the **wizard**, which asks for your server details, the bot account, the
   channel, admin usernames, which services to enable, and volume limits.

It writes **`config.toml`** and verifies the SDK loads. If something is wrong it
says so and does **not** report success.

### Optional: Spotify albums & playlists

Single Spotify **track** links work immediately. For **albums/playlists**,
create a free app at <https://developer.spotify.com/dashboard>, copy its
**Client ID** and **Client Secret**, and enter them in the wizard (or set the
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` environment variables).

## 4. Start the bot

Double-click **`Start.bat`**. It connects, joins the channel, and reconnects on
its own if the connection drops. In TeamTalk, private-message the bot `help`.

## Keeping secrets out of the file

These environment variables override `config.toml` when set, so a password
never has to live in the file:

| Variable | Replaces |
|---|---|
| `TTMEDIA_PASSWORD` | the bot account password |
| `TTMEDIA_CHANNEL_PASSWORD` | the channel password |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Spotify credentials |
| `TTMEDIA_SDK` | path to a different TeamTalk SDK |

## Tuning (optional, in `config.toml`)

| Setting | Meaning |
|---|---|
| `[audio] buffer_ms` | decode read-ahead that absorbs network jitter (default 2500) |
| `[audio] lead_ms` | how far ahead audio is sent; lower = faster stop, higher = safer (default 300) |
| `[audio] max_volume` | the ceiling users may set with `volume` (default 200) |
| `[audio] max_track_seconds` | reject tracks longer than this (0 = no limit) |
| `[bot] rate_limit_per_10s` | commands one user may send per 10 s (default 8) |
| `[bot] max_user_queue` | tracks one user may hold in the queue (0 = unlimited) |
| `[bot] channel_prefix` | if set, also accept `<prefix>command` typed in the channel |

## Updating YouTube support

YouTube changes often; if it stops resolving, update the extractor:

```bat
.venv\Scripts\python -m pip install -U yt-dlp
```

## Running without the one-click scripts

```bat
py -3 -m venv .venv
.venv\Scripts\python -m pip install -e .
.venv\Scripts\python -m ttmedia.bootstrap      REM downloads ffmpeg + deno
.venv\Scripts\python -m ttmedia.setup_wizard   REM the wizard
.venv\Scripts\python -m ttmedia -c config.toml REM run
```
