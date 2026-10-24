# Setup guide (Windows)

This walks you from nothing to a running bot in a few minutes.

## 1. Install Python (once)

Download **64-bit Python 3.11+** from <https://www.python.org/downloads/>.
During installation, tick **“Add python.exe to PATH”**. (The bot's DLL is
64-bit, so 32-bit Python will not work — Setup checks this and tells you.)

## 2. Create the bot's account on your TeamTalk server

The bot logs in like any user and needs the **“transmit voice”** right (it
sends audio as if it were talking). On most servers:

1. Connect to the server as an administrator in the TeamTalk client.
2. **Server → Accounts…** (or your server's admin tool).
3. Create a normal user account, e.g. username `mediabot` with a password.
4. Make sure its rights include **transmit voice / microphone**. (Default
   accounts usually have this.)
5. Optionally create or choose the channel you want the bot to sit in.

To let specific people use the admin-only commands (`say`, `quit`), note their
**TeamTalk account usernames** — you'll enter them in the wizard. Server
administrators are always treated as admins.

## 3. Run Setup

Double-click **`Setup.bat`** in this folder. It will:

- create an isolated environment in `.venv` (nothing is installed system-wide);
- install the bot and `yt-dlp`;
- download **ffmpeg** and **Deno** into `tools\` (ffmpeg plays the audio; Deno
  lets `yt-dlp` solve YouTube's challenges);
- start the **wizard**, which asks for:
  - server address, TCP/UDP ports, and whether it's an encrypted (TLS) server;
  - the bot account's username/password and display nickname;
  - the channel to join;
  - admin usernames (optional);
  - which services to enable;
  - default/maximum volume and an optional maximum track length.

It writes **`config.toml`** and confirms the SDK loads. If anything is wrong it
tells you and does **not** falsely report success.

### Optional: Spotify albums & playlists

Single Spotify **track** links work immediately. For **albums/playlists**,
create a free app at <https://developer.spotify.com/dashboard>, copy its
**Client ID** and **Client Secret**, and enter them in the wizard (or set the
`SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` environment variables).

## 4. Start the bot

Double-click **`Start.bat`**. You should see it connect and join the channel.
In TeamTalk, send the bot a **private message** with `h` to see every command.

## Keeping secrets out of the file

Any of these environment variables override `config.toml` if set, so you never
have to store a password in the file:

| Variable | Replaces |
|---|---|
| `TTMEDIA_PASSWORD` | the bot account password |
| `TTMEDIA_CHANNEL_PASSWORD` | the channel password |
| `SPOTIFY_CLIENT_ID` / `SPOTIFY_CLIENT_SECRET` | Spotify credentials |
| `TTMEDIA_SDK` | path to a different TeamTalk SDK |

## Updating

To update the YouTube extractor (YouTube changes often):

```bat
.venv\Scripts\activate
pip install -U yt-dlp
```

## Running without the one-click scripts

```bat
py -3 -m venv .venv
.venv\Scripts\activate
pip install -e .
python -m ttmedia.bootstrap     REM downloads ffmpeg + deno
ttmedia-setup                   REM the wizard
ttmedia -c config.toml          REM run
```
