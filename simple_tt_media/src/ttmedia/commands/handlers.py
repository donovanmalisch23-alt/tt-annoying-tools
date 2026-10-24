"""All chat commands and their (detailed) replies.

Each handler takes a :class:`CommandContext` and replies through ``ctx.reply``.
Network-bound handlers already run on a worker thread (see the dispatcher), so
they may block on resolution without stalling the bot.
"""

from __future__ import annotations

from typing import List

from ..errors import CommandError
from ..models import RepeatMode, Track, fmt_duration
from . import Command, CommandContext, Dispatcher


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _parse_time(text: str) -> float:
    text = text.strip()
    if not text:
        raise CommandError("Give a time, e.g. 'seek 1:30' or 'seek 90'.")
    parts = text.split(":")
    if not all(p.isdigit() for p in parts) or len(parts) > 3:
        raise CommandError("Time must be seconds, m:ss, or h:mm:ss.")
    seconds = 0
    for p in parts:
        seconds = seconds * 60 + int(p)
    return float(seconds)


def _track_line(i: int, t: Track) -> str:
    return f"{i}. {t.label()}" + (f" — {t.requested_by}" if t.requested_by else "")


def _enqueue_and_report(ctx: CommandContext, tracks: List[Track]) -> None:
    player = ctx.player
    try:
        pos = player.enqueue(tracks)
    except ValueError as exc:
        raise CommandError(str(exc))
    if len(tracks) > 1:
        total = sum(t.duration or 0 for t in tracks)
        extra = f" (~{fmt_duration(total)})" if total else ""
        ctx.reply(f"Added {len(tracks)} tracks to the queue{extra}.")
    elif pos == 0:
        pass  # the player announces "Now playing" to the channel already
    else:
        ctx.reply(f"Added to the queue at #{pos}: {tracks[0].label()}")


# --------------------------------------------------------------------------
# playback
# --------------------------------------------------------------------------

def cmd_play(ctx: CommandContext) -> None:
    arg = ctx.arg
    if not arg:
        # No argument: toggle pause/resume if something is loaded.
        if ctx.player.current is None:
            raise CommandError("Usage: p <search words, or a link>. "
                               "Example: p daft punk get lucky")
        paused = ctx.player.toggle_pause()
        ctx.reply("Paused." if paused else "Resumed.")
        return
    # A bare number replays a result from the last `search`.
    if arg.isdigit():
        return _select(ctx, int(arg))
    ctx.reply(f"Searching {ctx.sources.service if not ctx.sources.is_url(arg) else 'the link'} ...")
    if ctx.sources.is_url(arg):
        tracks = ctx.sources.from_input(arg, requested_by=ctx.requester)
    else:
        tracks = ctx.sources.search(arg, requested_by=ctx.requester, limit=1)
    _reject_too_long(ctx, tracks)
    _enqueue_and_report(ctx, tracks)


def cmd_url(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError("Usage: url <direct media or stream link>")
    if not ctx.sources.is_url(ctx.arg):
        raise CommandError("That isn't a URL. Use 'p' to search by words.")
    tracks = ctx.sources.play_direct(ctx.arg, requested_by=ctx.requester)
    _enqueue_and_report(ctx, tracks)


def cmd_search(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError(f"Usage: s <words>. Searches {ctx.sources.service}.")
    results = ctx.sources.search(ctx.arg, requested_by=ctx.requester)
    ctx.dispatcher.set_results(ctx.user_id, results)
    lines = [f"Top results on {ctx.sources.service} (send 'play N' to play one):"]
    for i, t in enumerate(results[:20], 1):
        lines.append(_track_line(i, t))
    ctx.reply("\n".join(lines))


def _select(ctx: CommandContext, number: int) -> None:
    results = ctx.dispatcher.get_results(ctx.user_id)
    if not results:
        raise CommandError("You have no search results yet. Use 's <words>' first.")
    if number < 1 or number > len(results):
        raise CommandError(f"Pick a number between 1 and {len(results)}.")
    chosen = results[number - 1]
    _reject_too_long(ctx, [chosen])
    _enqueue_and_report(ctx, [chosen])


def cmd_select(ctx: CommandContext) -> None:
    if not ctx.arg.strip().lstrip("-").isdigit():
        raise CommandError("Usage: sel <number from the search results>")
    _select(ctx, int(ctx.arg))


def cmd_skip(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    nxt = ctx.player.skip()
    if nxt is None and ctx.player.current is None:
        ctx.reply("Skipped. The queue is now empty.")


def cmd_previous(ctx: CommandContext) -> None:
    if ctx.player.previous() is None:
        raise CommandError("There is no previous track.")


def cmd_pause(ctx: CommandContext) -> None:
    ctx.reply("Paused." if ctx.player.pause() else "Nothing is playing to pause.")


def cmd_resume(ctx: CommandContext) -> None:
    ctx.reply("Resumed." if ctx.player.resume() else "Nothing is paused.")


def cmd_toggle(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    ctx.reply("Paused." if ctx.player.toggle_pause() else "Resumed.")


def cmd_stop(ctx: CommandContext) -> None:
    ctx.player.stop()
    ctx.reply("Stopped playback and cleared the queue.")


def cmd_clear(ctx: CommandContext) -> None:
    n = ctx.player.clear()
    ctx.reply(f"Cleared {n} track(s) from the queue."
              if n else "The queue was already empty.")


def cmd_volume(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Volume is {ctx.player.volume}%.")
        return
    raw = ctx.arg.strip().rstrip("%")
    try:
        want = int(raw)
    except ValueError:
        raise CommandError(f"Usage: v <0-{ctx.cfg.audio.max_volume}>")
    now = ctx.player.set_volume(want)
    ctx.reply(f"Volume set to {now}%."
              + (f" (capped at {ctx.cfg.audio.max_volume}%)"
                 if want > ctx.cfg.audio.max_volume else ""))


def cmd_seek(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    if ctx.player.current.is_live:
        raise CommandError("Can't seek in a live stream.")
    if ctx.player.seek_to(_parse_time(ctx.arg)):
        ctx.reply(f"Seeked to {fmt_duration(ctx.player.elapsed())}.")
    else:
        raise CommandError("Couldn't seek.")


def _seek_rel(ctx: CommandContext, sign: int) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    step = 15.0
    if ctx.arg.strip():
        try:
            step = float(ctx.arg)
        except ValueError:
            raise CommandError("Usage: ff [seconds] / rw [seconds]")
    if ctx.player.current.is_live:
        raise CommandError("Can't seek in a live stream.")
    ctx.player.seek_relative(sign * step)
    ctx.reply(f"Now at {fmt_duration(ctx.player.elapsed())}.")


def cmd_forward(ctx: CommandContext) -> None:
    _seek_rel(ctx, +1)


def cmd_rewind(ctx: CommandContext) -> None:
    _seek_rel(ctx, -1)


def cmd_repeat(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Repeat is '{ctx.player.repeat.value}'. "
                  "Use: repeat off | track | queue")
        return
    try:
        mode = RepeatMode(ctx.arg.strip().lower())
    except ValueError:
        raise CommandError("Repeat must be: off, track, or queue.")
    ctx.player.set_repeat(mode)
    ctx.reply(f"Repeat set to '{mode.value}'.")


def cmd_shuffle(ctx: CommandContext) -> None:
    a = ctx.arg.strip().lower()
    if a in ("on", "off"):
        ctx.player.set_shuffle(a == "on")
        ctx.reply(f"Shuffle {a}.")
    elif a in ("", "toggle"):
        new = not ctx.player.shuffle
        ctx.player.set_shuffle(new)
        ctx.reply(f"Shuffle {'on' if new else 'off'}.")
    elif a in ("queue", "now"):
        ctx.player.shuffle_queue()
        ctx.reply("Shuffled the current queue.")
    else:
        raise CommandError("Usage: shuffle [on|off|queue]")


# --------------------------------------------------------------------------
# information
# --------------------------------------------------------------------------

def cmd_queue(ctx: CommandContext) -> None:
    items = ctx.player.queue_snapshot()
    cur = ctx.player.current
    lines = []
    if cur is not None:
        tag = " (paused)" if ctx.player.is_paused else ""
        lines.append(f"Now playing{tag}: {cur.label()}")
    if items:
        total = sum(t.duration or 0 for t in items)
        lines.append(f"Up next ({len(items)}"
                     + (f", ~{fmt_duration(total)}" if total else "") + "):")
        for i, t in enumerate(items[:25], 1):
            lines.append(_track_line(i, t))
        if len(items) > 25:
            lines.append(f"...and {len(items) - 25} more.")
    if not lines:
        ctx.reply("The queue is empty. Add something with 'p <words or link>'.")
    else:
        ctx.reply("\n".join(lines))


def cmd_now(ctx: CommandContext) -> None:
    cur = ctx.player.current
    if cur is None:
        ctx.reply("Nothing is playing right now.")
        return
    pos = fmt_duration(ctx.player.elapsed())
    dur = fmt_duration(cur.duration) if cur.duration else "live" if cur.is_live else "?:??"
    bits = [f"Now playing: {cur.label()}"]
    if cur.requested_by:
        bits.append(f"requested by {cur.requested_by}")
    body = " | ".join(bits)
    body += f"\nPosition: {pos} / {dur} | {ctx.player.modes_text()}"
    if cur.url:
        body += f"\nLink: {cur.url}"
    ctx.reply(body)


def cmd_service(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Search service: {ctx.sources.service}. "
                  f"Available: {', '.join(ctx.sources.services())}. "
                  "Change with: sv <name>")
        return
    ctx.sources.set_service(ctx.arg)   # raises SourceError -> CommandError base
    ctx.reply(f"Search service set to {ctx.sources.service}.")


def cmd_services(ctx: CommandContext) -> None:
    ctx.reply("Services you can search: " + ", ".join(ctx.sources.services())
              + f"\nCurrently searching: {ctx.sources.service}"
              + "\nYou can also paste YouTube, SoundCloud, Spotify, Apple Music, "
                "or direct stream links to 'p' or 'url'.")


def cmd_about(ctx: CommandContext) -> None:
    from .. import __version__
    ctx.reply(
        f"Simple TT Media v{__version__} — a media player bot for TeamTalk.\n"
        f"Playing via: {ctx.sources.service} (search) + YouTube/SoundCloud/"
        "Spotify/Apple Music/direct links.\n"
        "Send 'h' for the list of commands, or 'h <command>' for details.")


def cmd_help(ctx: CommandContext) -> None:
    if ctx.arg:
        cmd = ctx.dispatcher.lookup(ctx.arg.strip())
        if cmd is None:
            raise CommandError(f"No such command: '{ctx.arg.strip()}'.")
        names = "/".join([cmd.name, *cmd.aliases])
        text = f"{names}"
        if cmd.usage:
            text += f"\nUsage: {cmd.usage}"
        text += f"\n{cmd.help or cmd.summary}"
        if cmd.admin:
            text += "\n(administrators only)"
        ctx.reply(text)
        return
    groups: dict = {}
    for cmd in ctx.dispatcher.commands():
        if cmd.admin and not ctx.is_admin:
            continue
        groups.setdefault(cmd.group, []).append(cmd)
    lines = ["Simple TT Media — send these to me in a private message:"]
    for group in ("Playback", "Queue", "Information", "Access", "Admin"):
        cmds = groups.get(group)
        if not cmds:
            continue
        lines.append(f"\n[{group}]")
        for cmd in cmds:
            names = "/".join([cmd.name, *cmd.aliases[:1]])
            lines.append(f"  {names} — {cmd.summary}")
    lines.append("\nTip: 'h <command>' shows details and usage for one command.")
    ctx.reply("\n".join(lines))


# --------------------------------------------------------------------------
# admin
# --------------------------------------------------------------------------

def cmd_say(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError("Usage: say <message to post in the channel>")
    ctx.client.send_channel(ctx.arg)
    ctx.reply("Posted to the channel.")


def cmd_quit(ctx: CommandContext) -> None:
    ctx.reply("Shutting down. Goodbye!")
    try:
        ctx.player.stop()
    finally:
        ctx.client.shutdown()


def cmd_lock(ctx: CommandContext) -> None:
    lm = ctx.dispatcher.lock_manager
    if lm is None:
        raise CommandError("Locking is not available.")
    # The lock manager decides everything from the current state and who sent
    # it: admins lock (with a password they set), anyone unlocks (with it), and
    # a non-admin trying to lock an unlocked bot gets an error.
    lm.request(ctx.user_id, ctx.requester, ctx.is_admin, ctx.is_private, ctx.reply)


# --------------------------------------------------------------------------
# shared bits
# --------------------------------------------------------------------------

def _reject_too_long(ctx: CommandContext, tracks: List[Track]) -> None:
    limit = ctx.cfg.audio.max_track_seconds
    if not limit:
        return
    for t in tracks:
        if t.duration and t.duration > limit and not t.is_live:
            raise CommandError(
                f"'{t.title}' is {fmt_duration(t.duration)} long; the limit is "
                f"{fmt_duration(limit)}.")


# --------------------------------------------------------------------------
# registration
# --------------------------------------------------------------------------

def register_all(d: Dispatcher) -> None:
    defs = [
        Command("p", cmd_play, ["play"], usage="p <words or link>",
                summary="Play/queue a search or link (no args toggles pause)",
                help="Searches the current service for your words and queues the "
                     "top hit, or plays/queues a pasted link (track, playlist or "
                     "album). With no argument it pauses or resumes. A bare number "
                     "plays that item from your last search.", group="Playback"),
        Command("url", cmd_url, [], usage="url <link>",
                summary="Play a direct media or radio stream link",
                help="Plays a direct audio file URL or an internet-radio stream.",
                group="Playback"),
        Command("s", cmd_search, ["search", "find"], usage="s <words>",
                summary="Search and show a numbered list to choose from",
                help="Searches the current service and lists results. Play one "
                     "with 'p <number>' or 'sel <number>'.", group="Playback"),
        Command("sel", cmd_select, ["select"], usage="sel <number>",
                summary="Play a numbered result from your last search",
                group="Playback"),
        Command("stop", cmd_stop, [], summary="Stop playback and clear the queue",
                group="Playback"),
        Command("pause", cmd_pause, [], summary="Pause playback", group="Playback"),
        Command("resume", cmd_resume, [], summary="Resume playback", group="Playback"),
        Command("pp", cmd_toggle, ["playpause"], summary="Toggle pause/resume",
                group="Playback"),
        Command("next", cmd_skip, ["skip", "n"], summary="Skip to the next track",
                group="Playback"),
        Command("prev", cmd_previous, ["previous", "b"],
                summary="Play the previous track", group="Playback"),
        Command("v", cmd_volume, ["vol", "volume"], usage="v [0-max]",
                summary="Show or set the volume (percent)", group="Playback"),
        Command("seek", cmd_seek, [], usage="seek <m:ss | seconds>",
                summary="Jump to a position in the current track", group="Playback"),
        Command("ff", cmd_forward, ["fwd"], usage="ff [seconds]",
                summary="Skip forward (default 15s)", group="Playback"),
        Command("rw", cmd_rewind, ["rewind"], usage="rw [seconds]",
                summary="Skip backward (default 15s)", group="Playback"),
        Command("repeat", cmd_repeat, ["loop"], usage="repeat <off|track|queue>",
                summary="Set the repeat mode", group="Queue"),
        Command("shuffle", cmd_shuffle, ["sh"], usage="shuffle [on|off|queue]",
                summary="Toggle shuffle, or shuffle the queue now", group="Queue"),
        Command("q", cmd_queue, ["queue"], summary="Show the queue and what's playing",
                group="Queue"),
        Command("clear", cmd_clear, ["cl"], summary="Clear the queue (keep playing)",
                group="Queue"),
        Command("np", cmd_now, ["now", "nowplaying"],
                summary="Show the current track and position", group="Information"),
        Command("sv", cmd_service, ["service"], usage="sv [name]",
                summary="Show or set which service free-text searches",
                group="Information"),
        Command("services", cmd_services, [], summary="List searchable services",
                group="Information"),
        Command("h", cmd_help, ["help", "?", "commands"], usage="h [command]",
                summary="Show this help (or details for one command)",
                group="Information"),
        Command("about", cmd_about, ["info"], summary="About this bot",
                group="Information"),
        Command("lock", cmd_lock, [], usage="lock",
                summary="Lock the bot (admins), or unlock it with the password",
                help="Admins lock the bot: you'll be asked privately for a "
                     "password to set, then commands are disabled for everyone. "
                     "While locked, anyone can send 'lock' and will be asked "
                     "privately for that password to unlock it. Non-admins "
                     "cannot lock an unlocked bot.", group="Access"),
        Command("say", cmd_say, [], admin=True, usage="say <message>",
                summary="Make the bot post a message in its channel", group="Admin"),
        Command("quit", cmd_quit, ["shutdown"], admin=True,
                summary="Shut the bot down", group="Admin"),
    ]
    for c in defs:
        d.register(c)
