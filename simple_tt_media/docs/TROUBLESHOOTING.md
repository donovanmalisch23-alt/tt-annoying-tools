# Troubleshooting

The bot writes log messages to the console (and to `log_file` if you set one in
`config.toml`). Most problems show up there first.

## Setup

**“Python … was not found” / wrong architecture**
Install 64-bit Python 3.11+ and tick “Add Python to PATH”. Re-run `Setup.bat`.

**“Setup did not finish” / no `config.toml`**
Scroll up for the real error. Fix it and run `Setup.bat` again. Setup only
reports success once `config.toml` actually exists.

**ffmpeg or deno didn't download**
Re-run `Setup.bat` (it retries), or install them yourself and either put them
on your PATH or set `tools.ffmpeg_path` / `tools.deno_path` in `config.toml`.
ffmpeg: <https://ffmpeg.org/download.html>. Deno: <https://deno.land>.

## Connecting

**“connect() failed” / can't reach the server**
Check `host`, `tcp_port`, `udp_port`, and `encrypted` in `config.toml`. Make
sure the machine can reach the server and that a firewall isn't blocking it.

**“Login was rejected”**
The username/password is wrong, or the account doesn't exist. Set the password
via `TTMEDIA_PASSWORD` if you left it blank in the file.

**The bot connects but never seems “ready”**
It couldn't join the channel. Check the `channel` path (use `/` for root) and
`channel_password`. The log says if the channel wasn't found (it falls back to
root).

**It keeps reconnecting**
The server is dropping it (kicked/banned, or the account lacks rights). Give the
account the *transmit voice* right. The bot retries every `reconnect_seconds`.

## Playback

**Commands reply but no sound comes out**
- Make sure the bot account has the **transmit voice** right.
- Confirm ffmpeg is found: the log warns at startup if it isn't.
- Some tracks start quietly; try `np` to confirm it's playing and `v 100`.

**YouTube says “private, restricted, or unavailable” or asks to sign in**
YouTube sometimes requires a sign-in check from new IPs/data centers, and some
videos are age/region restricted. Try another video, run `pip install -U
yt-dlp`, or search a different service with `sv soundcloud`.

**A track stutters or stops early**
Network hiccup or an expired URL. The bot automatically re-resolves once on an
error. For very long live streams this is normal; just replay.

**“Can't seek in a live stream”**
Internet radio and live streams have no fixed length, so seeking is disabled.

## Spotify / Apple Music

**“Spotify albums and playlists need a free Spotify developer app”**
Add `spotify_client_id` / `spotify_client_secret` (see SETUP.md). Single track
links work without them.

**“Apple Music playlists aren't supported”**
Apple only exposes playlists through its paid developer API. Song and album
links work.

## Still stuck?

Set `log_level = "DEBUG"` in `config.toml`, reproduce the problem, and read the
log — it names the failing step (resolve, ffmpeg, SDK, or connection).
