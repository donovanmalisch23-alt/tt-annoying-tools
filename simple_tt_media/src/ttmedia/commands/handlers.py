"""All chat commands and their replies.

Each handler takes a :class:`CommandContext` and answers through ``ctx.reply``
(a private message, or the channel if the command came from there). Handlers
run on the dispatcher's worker pool, so they may block on network work without
stalling the bot.

Style: replies are short, consistent, and action-first. Errors explain what to
do next. Usage strings use ``<required>`` and ``[optional]``.
"""

from __future__ import annotations

from typing import List

from ..errors import CommandError
from ..models import RepeatMode, Track, clean_text, fmt_duration
from . import Command, CommandContext, Dispatcher

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


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


def _line(i: int, t: Track) -> str:
    who = f" (by {clean_text(t.requested_by, 32)})" if t.requested_by else ""
    return f"{i}. {t.label()}{who}"


def _reject_too_long(ctx: CommandContext, tracks: List[Track]) -> None:
    limit = ctx.cfg.audio.max_track_seconds
    if not limit:
        return
    for t in tracks:
        if t.duration and t.duration > limit and not t.is_live:
            raise CommandError(
                f"'{clean_text(t.title, 80)}' is {fmt_duration(t.duration)} long; "
                f"the limit is {fmt_duration(limit)}.")


def _enforce_user_quota(ctx: CommandContext, adding: int) -> None:
    cap = int(getattr(ctx.cfg.bot, "max_user_queue", 0) or 0)
    if cap <= 0:
        return
    mine = sum(1 for t in ctx.player.queue_snapshot()
               if t.requested_by == ctx.requester)
    if mine + adding > cap:
        raise CommandError(
            f"You already have {mine} track(s) queued (limit {cap}). "
            "Wait for some to play, or use 'remove'.")


def _enqueue(ctx: CommandContext, tracks: List[Track], front: bool = False) -> None:
    _reject_too_long(ctx, tracks)
    _enforce_user_quota(ctx, len(tracks))
    try:
        pos = (ctx.player.enqueue_front(tracks) if front
               else ctx.player.enqueue(tracks))
    except ValueError as exc:
        raise CommandError(str(exc))
    if len(tracks) > 1:
        total = sum(t.duration or 0 for t in tracks)
        extra = f", ~{fmt_duration(total)}" if total else ""
        ctx.reply(f"Added {len(tracks)} tracks to the queue ({len(tracks)}{extra}).")
    elif pos == 0:
        pass  # the player announces "Now playing" in the channel
    else:
        where = "next" if front else f"#{pos}"
        ctx.reply(f"Queued {where}: {tracks[0].label()}")


# ---------------------------------------------------------------------------
# playback
# ---------------------------------------------------------------------------


def cmd_play(ctx: CommandContext) -> None:
    arg = ctx.arg
    if not arg:
        if ctx.player.current is None:
            raise CommandError("Usage: play <search words or a link>. "
                               "Example: play daft punk - get lucky")
        ctx.reply("Paused." if ctx.player.toggle_pause() else "Resumed.")
        return
    if arg.isdigit():                     # "play 3" == pick the 3rd result
        return _pick(ctx, int(arg))
    _resolve_and_enqueue(ctx, arg, front=False)


def cmd_playnext(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError("Usage: playnext <search words or a link>")
    _resolve_and_enqueue(ctx, ctx.arg, front=True)


def _resolve_and_enqueue(ctx: CommandContext, arg: str, front: bool) -> None:
    if ctx.sources.is_url(arg):
        ctx.reply("Loading the link...")
        tracks = ctx.sources.from_input(arg, requested_by=ctx.requester)
    else:
        ctx.reply(f"Searching {ctx.sources.service} for: {clean_text(arg, 120)}")
        tracks = ctx.sources.search(arg, requested_by=ctx.requester, limit=1)
    _enqueue(ctx, tracks, front=front)


def cmd_url(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError("Usage: url <direct audio or radio-stream link>")
    if not ctx.sources.is_url(ctx.arg):
        raise CommandError("That isn't a link. Use 'play' to search by words.")
    _enqueue(ctx, ctx.sources.play_direct(ctx.arg, requested_by=ctx.requester))


def cmd_search(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError(f"Usage: search <words>  (searches {ctx.sources.service})")
    results = ctx.sources.search(ctx.arg, requested_by=ctx.requester)
    ctx.dispatcher.set_results(ctx.user_id, results)
    lines = [f"Results on {ctx.sources.service} — play one with 'pick <number>':"]
    for i, t in enumerate(results[:20], 1):
        lines.append(_line(i, t))
    ctx.reply("\n".join(lines))


def _pick(ctx: CommandContext, number: int) -> None:
    results = ctx.dispatcher.get_results(ctx.user_id)
    if not results:
        raise CommandError("You have no search results yet. Use 'search <words>' first.")
    if number < 1 or number > len(results):
        raise CommandError(f"Pick a number between 1 and {len(results)}.")
    _enqueue(ctx, [results[number - 1]])


def cmd_pick(ctx: CommandContext) -> None:
    if not ctx.arg.strip().isdigit():
        raise CommandError("Usage: pick <number from your last search>")
    _pick(ctx, int(ctx.arg))


def cmd_skip(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    count = 1
    if ctx.arg.strip():
        if not ctx.arg.strip().isdigit():
            raise CommandError("Usage: skip [how many]")
        count = max(1, int(ctx.arg))
    ctx.player.skip(count)
    if ctx.player.current is None:
        ctx.reply("Skipped. The queue is now empty.")


def cmd_back(ctx: CommandContext) -> None:
    if ctx.player.previous() is None:
        raise CommandError("There is no previous track.")


def cmd_restart(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    if ctx.player.restart():
        ctx.reply("Restarted from the beginning.")
    else:
        raise CommandError("This track can't be restarted (it's a live stream).")


def cmd_pause(ctx: CommandContext) -> None:
    ctx.reply("Paused." if ctx.player.pause() else "Nothing is playing to pause.")


def cmd_resume(ctx: CommandContext) -> None:
    ctx.reply("Resumed." if ctx.player.resume() else "Nothing is paused.")


def cmd_stop(ctx: CommandContext) -> None:
    ctx.player.stop()
    ctx.reply("Stopped playback and cleared the queue.")


def cmd_volume(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Volume is {ctx.player.volume}%.")
        return
    try:
        want = int(ctx.arg.strip().rstrip("%"))
    except ValueError:
        raise CommandError(f"Usage: volume <0-{ctx.cfg.audio.max_volume}>")
    now = ctx.player.set_volume(want)
    capped = " (the maximum)" if want > ctx.cfg.audio.max_volume else ""
    ctx.reply(f"Volume set to {now}%{capped}.")


def cmd_seek(ctx: CommandContext) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    if ctx.player.current.is_live:
        raise CommandError("You can't seek within a live stream.")
    if ctx.player.seek_to(_parse_time(ctx.arg)):
        ctx.reply(f"Jumped to {fmt_duration(ctx.player.elapsed())}.")
    else:
        raise CommandError("Couldn't seek.")


def _seek_rel(ctx: CommandContext, sign: int, verb: str) -> None:
    if ctx.player.current is None:
        raise CommandError("Nothing is playing.")
    if ctx.player.current.is_live:
        raise CommandError("You can't seek within a live stream.")
    step = 15.0
    if ctx.arg.strip():
        try:
            step = float(ctx.arg)
        except ValueError:
            raise CommandError(f"Usage: {verb} [seconds]")
    ctx.player.seek_relative(sign * step)
    ctx.reply(f"Now at {fmt_duration(ctx.player.elapsed())}.")


def cmd_forward(ctx: CommandContext) -> None:
    _seek_rel(ctx, +1, "forward")


def cmd_rewind(ctx: CommandContext) -> None:
    _seek_rel(ctx, -1, "rewind")


_REPEAT = {"off": RepeatMode.OFF, "one": RepeatMode.TRACK, "track": RepeatMode.TRACK,
           "all": RepeatMode.QUEUE, "queue": RepeatMode.QUEUE}
_REPEAT_LABEL = {RepeatMode.OFF: "off", RepeatMode.TRACK: "one", RepeatMode.QUEUE: "all"}


def cmd_repeat(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Repeat is '{_REPEAT_LABEL[ctx.player.repeat]}'. "
                  "Use: repeat <off|one|all>")
        return
    mode = _REPEAT.get(ctx.arg.strip().lower())
    if mode is None:
        raise CommandError("Repeat must be: off, one, or all.")
    ctx.player.set_repeat(mode)
    ctx.reply(f"Repeat set to '{_REPEAT_LABEL[mode]}'.")


def cmd_shuffle(ctx: CommandContext) -> None:
    a = ctx.arg.strip().lower()
    if a in ("on", "off"):
        ctx.player.set_shuffle(a == "on")
        ctx.reply(f"Shuffle {a}.")
    elif a in ("", "toggle"):
        new = not ctx.player.shuffle
        ctx.player.set_shuffle(new)
        ctx.reply(f"Shuffle {'on' if new else 'off'}.")
    elif a in ("now", "queue"):
        ctx.player.shuffle_queue()
        ctx.reply("Shuffled the current queue.")
    else:
        raise CommandError("Usage: shuffle [on|off|now]")


# ---------------------------------------------------------------------------
# queue & information
# ---------------------------------------------------------------------------


def cmd_queue(ctx: CommandContext) -> None:
    items = ctx.player.queue_snapshot()
    cur = ctx.player.current
    lines = []
    if cur is not None:
        tag = " (paused)" if ctx.player.is_paused else ""
        lines.append(f"Now playing{tag}: {cur.label()}")
    if items:
        total = sum(t.duration or 0 for t in items)
        lines.append(f"Up next — {len(items)} track(s)"
                     + (f", ~{fmt_duration(total)}" if total else "") + ":")
        for i, t in enumerate(items[:25], 1):
            lines.append(_line(i, t))
        if len(items) > 25:
            lines.append(f"...and {len(items) - 25} more.")
    ctx.reply("\n".join(lines) if lines
              else "The queue is empty. Add something with 'play <words or link>'.")


def cmd_now(ctx: CommandContext) -> None:
    cur = ctx.player.current
    if cur is None:
        ctx.reply("Nothing is playing right now.")
        return
    pos = fmt_duration(ctx.player.elapsed())
    dur = "live" if cur.is_live else fmt_duration(cur.duration) if cur.duration else "?:??"
    body = f"Now playing: {cur.label()}"
    if cur.requested_by:
        body += f" (by {clean_text(cur.requested_by, 32)})"
    body += f"\n{pos} / {dur}  |  {ctx.player.modes_text()}"
    if cur.url:
        body += f"\n{cur.url}"
    ctx.reply(body)


def cmd_remove(ctx: CommandContext) -> None:
    if not ctx.arg.strip().isdigit():
        raise CommandError("Usage: remove <queue number>  (see 'queue')")
    removed = ctx.player.remove(int(ctx.arg))
    if removed is None:
        raise CommandError("There's no track with that number in the queue.")
    ctx.reply(f"Removed: {removed.label()}")


def cmd_clear(ctx: CommandContext) -> None:
    n = ctx.player.clear()
    ctx.reply(f"Cleared {n} track(s) from the queue."
              if n else "The queue is already empty.")


def cmd_service(ctx: CommandContext) -> None:
    if not ctx.arg:
        ctx.reply(f"Searching: {ctx.sources.service}. "
                  f"Available: {', '.join(ctx.sources.services())}. "
                  "Change with: service <name>")
        return
    ctx.sources.set_service(ctx.arg)     # raises SourceError (a CommandError) if unknown
    ctx.reply(f"Now searching {ctx.sources.service}.")


def cmd_about(ctx: CommandContext) -> None:
    from .. import __version__
    ctx.reply(
        f"Simple TT Media v{__version__} — a media player for TeamTalk.\n"
        "Plays from YouTube, SoundCloud, Spotify, Apple Music and direct/radio "
        "links. Send 'help' for commands, or 'help <command>' for details.")


def cmd_help(ctx: CommandContext) -> None:
    if ctx.arg:
        cmd = ctx.dispatcher.lookup(ctx.arg.strip())
        if cmd is None:
            raise CommandError(f"No such command: '{clean_text(ctx.arg, 32)}'.")
        names = " / ".join([cmd.name, *cmd.aliases])
        text = names
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
    lines = ["Simple TT Media — private-message me any of these:"]
    for group in ("Playback", "Finding", "Queue", "Access", "Admin"):
        cmds = groups.get(group)
        if not cmds:
            continue
        lines.append(f"\n[{group}]")
        for cmd in cmds:
            alias = f" ({cmd.aliases[0]})" if cmd.aliases else ""
            lines.append(f"  {cmd.name}{alias} — {cmd.summary}")
    lines.append("\nType 'help <command>' for usage and details.")
    ctx.reply("\n".join(lines))


# ---------------------------------------------------------------------------
# access & admin
# ---------------------------------------------------------------------------


def cmd_lock(ctx: CommandContext) -> None:
    lm = ctx.dispatcher.lock_manager
    if lm is None:
        raise CommandError("Locking is not available.")
    lm.request(ctx.user_id, ctx.requester, ctx.is_admin, ctx.is_private, ctx.reply)


def cmd_say(ctx: CommandContext) -> None:
    if not ctx.arg:
        raise CommandError("Usage: say <message to post in the channel>")
    ctx.client.send_channel(clean_text(ctx.arg, 400))
    ctx.reply("Posted to the channel.")


def cmd_quit(ctx: CommandContext) -> None:
    ctx.reply("Shutting down. Goodbye!")
    try:
        ctx.player.stop()
    finally:
        ctx.client.shutdown()


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------


def register_all(d: Dispatcher) -> None:
    defs = [
        # Playback
        Command("play", cmd_play, ["p"], usage="play <search or link>",
                summary="Play or queue a search or link; no argument pauses/resumes",
                help="Searches the current service for your words and queues the "
                     "best match, or plays a pasted link (a playlist or album "
                     "queues in full). With no argument it pauses or resumes. "
                     "'play <number>' plays that item from your last search.",
                group="Playback"),
        Command("playnext", cmd_playnext, ["pn"], usage="playnext <search or link>",
                summary="Add to the front of the queue (plays next)",
                group="Playback"),
        Command("url", cmd_url, ["stream"], usage="url <link>",
                summary="Play a direct audio file or internet-radio stream",
                group="Playback"),
        Command("pause", cmd_pause, [], summary="Pause playback", group="Playback"),
        Command("resume", cmd_resume, [], summary="Resume playback", group="Playback"),
        Command("skip", cmd_skip, ["next"], usage="skip [how many]",
                summary="Skip the current track (or several)", group="Playback"),
        Command("back", cmd_back, ["prev"], summary="Play the previous track",
                group="Playback"),
        Command("restart", cmd_restart, [], summary="Restart the current track",
                group="Playback"),
        Command("stop", cmd_stop, [], summary="Stop playback and clear the queue",
                group="Playback"),
        Command("volume", cmd_volume, ["vol"], usage="volume [0-max]",
                summary="Show or set the volume (percent)", group="Playback"),
        Command("seek", cmd_seek, [], usage="seek <m:ss>",
                summary="Jump to a position in the current track", group="Playback"),
        Command("forward", cmd_forward, ["fwd"], usage="forward [seconds]",
                summary="Skip ahead (default 15s)", group="Playback"),
        Command("rewind", cmd_rewind, ["rwd"], usage="rewind [seconds]",
                summary="Skip back (default 15s)", group="Playback"),
        Command("repeat", cmd_repeat, ["loop"], usage="repeat <off|one|all>",
                summary="Set the repeat mode", group="Playback"),
        Command("shuffle", cmd_shuffle, [], usage="shuffle [on|off|now]",
                summary="Turn shuffle on/off, or shuffle the queue now",
                group="Playback"),
        # Finding
        Command("search", cmd_search, ["find"], usage="search <words>",
                summary="Search and list results to choose from", group="Finding"),
        Command("pick", cmd_pick, [], usage="pick <number>",
                summary="Play a numbered result from your last search",
                group="Finding"),
        Command("service", cmd_service, ["sv"], usage="service [name]",
                summary="Show or choose which service free-text searches",
                group="Finding"),
        # Queue
        Command("queue", cmd_queue, ["q"], summary="Show what's playing and what's next",
                group="Queue"),
        Command("now", cmd_now, ["np"], summary="Show the current track and position",
                group="Queue"),
        Command("remove", cmd_remove, ["rm"], usage="remove <number>",
                summary="Remove a track from the queue", group="Queue"),
        Command("clear", cmd_clear, [], summary="Clear the queue (keep playing)",
                group="Queue"),
        # Access / help
        Command("help", cmd_help, ["h", "?"], usage="help [command]",
                summary="Show commands, or details for one", group="Access"),
        Command("about", cmd_about, [], summary="About this bot", group="Access"),
        Command("lock", cmd_lock, [], usage="lock",
                summary="Lock the bot (admins) or unlock it with the password",
                help="Admins lock the bot: you'll be asked privately for a "
                     "password to set, then all commands are disabled. While "
                     "locked, anyone can send 'lock' and will be asked privately "
                     "for that password to unlock it. Non-admins can't lock an "
                     "unlocked bot.", group="Access"),
        # Admin
        Command("say", cmd_say, [], admin=True, usage="say <message>",
                summary="Post a message to the channel", group="Admin"),
        Command("quit", cmd_quit, ["shutdown"], admin=True,
                summary="Shut the bot down", group="Admin"),
    ]
    for c in defs:
        d.register(c)
