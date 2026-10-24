# Troubleshooting

The bot logs to the console (and to `log_file` if you set one in
`config.toml`). Most problems show up there first. For more detail, set
`log_level = "DEBUG"` and reproduce.

## Setup

**“Python … was not found” / wrong architecture**
Install 64-bit Python 3.11+, tick “Add Python to PATH”, and re-run `Setup.bat`.

**Setup didn't finish / no `config.toml`**
Scroll up for the real error, fix it, and run `Setup.bat` again. Setup only
reports success once `config.toml` exists.

**A download seemed to stall**
It won't hang — ffmpeg and Deno download in parallel with time limits, and
`pip` runs with timeouts and retries. If a mirror fails, Setup says so and you
can re-run it, or install ffmpeg / Deno yourself (below).

**ffmpeg or Deno didn't install**
Re-run `Setup.bat` (it retries), or install them and either put them on PATH or
set `tools.ffmpeg_path` / `tools.deno_path` in `config.toml`.
ffmpeg: <https://ffmpeg.org/download.html>. Deno: <https://deno.land>.

## Connecting

**“connect() failed” / can't reach the server**
Check `host`, `tcp_port`, `udp_port`, and `encrypted` in `config.toml`, and that
the machine can reach the server through any firewall.

**“Login was rejected”**
Wrong username/password, or the account doesn't exist. Set the password via
`TTMEDIA_PASSWORD` if you left it blank in the file.

**Connects but never “ready”**
It couldn't join the channel. Check the `channel` path (`/` is root) and
`channel_password`; the log says if the channel wasn't found (it falls back to
root).

**Keeps reconnecting**
The server keeps dropping it (kicked/banned, or the account lacks rights). Give
the bot account the *transmit voice* right. It retries every
`reconnect_seconds`.

## Playback

**Commands reply but there's no sound**
- The bot account needs the **transmit voice** right.
- ffmpeg must be found — the log warns at startup if it isn't.
- Some tracks start quietly; check `now` and try `volume 100`.

**YouTube says “private, restricted, or unavailable” or asks to sign in**
YouTube sometimes demands a sign-in check from new IPs, and some videos are
age/region restricted. Try another video, run `pip install -U yt-dlp`, or
search a different service with `service soundcloud`.

**Audio stutters**
Rare, thanks to the decode buffer. If a network is very unstable, raise
`[audio] buffer_ms` (e.g. to 4000). If stop/skip feels slow, lower
`[audio] lead_ms` (e.g. to 200); note the listener's own TeamTalk client also
adds a little playback delay that the bot can't control.

**“You can't seek within a live stream”**
Internet radio and live streams have no fixed length, so seeking/restart are
disabled for them.

## Services

**“Spotify albums and playlists need a free Spotify developer app”**
Add `spotify_client_id` / `spotify_client_secret` (see SETUP.md). Single track
links work without them.

**“Apple Music playlists aren't supported”**
Apple only exposes playlists through its paid developer API. Song and album
links work.

## Control & access

**“You're sending commands too fast”**
The per-user rate limit kicked in. Wait a few seconds, or raise
`[bot] rate_limit_per_10s`.

**The bot is locked and nobody knows the password**
The lock is in memory only — restart the bot (`Start.bat`) and it comes back
unlocked.
