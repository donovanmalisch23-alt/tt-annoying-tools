# Command reference

Send these to the bot in a **private message** on TeamTalk. If an admin turned on a channel prefix during setup, you can also type `<prefix>command` in the channel.

- Arguments: `<required>`, `[optional]`.
- The bot replies to every command, and sends a clear error if a command is unknown or can't be run.

## Playback

| Command | Aliases | Usage | Description |
|---|---|---|---|
| `play` | `p` | `play <search or link>` | Play or queue a search or link; no argument pauses/resumes |
| `playnext` | `pn` | `playnext <search or link>` | Add to the front of the queue (plays next) |
| `url` | `stream` | `url <link>` | Play a direct audio file or internet-radio stream |
| `pause` | — | `pause` | Pause playback |
| `resume` | — | `resume` | Resume playback |
| `skip` | `next` | `skip [how many]` | Skip the current track (or several) |
| `back` | `prev` | `back` | Play the previous track |
| `restart` | — | `restart` | Restart the current track |
| `stop` | — | `stop` | Stop playback and clear the queue |
| `volume` | `vol` | `volume [0-max]` | Show or set the volume (percent) |
| `seek` | — | `seek <m:ss>` | Jump to a position in the current track |
| `forward` | `fwd` | `forward [seconds]` | Skip ahead (default 15s) |
| `rewind` | `rwd` | `rewind [seconds]` | Skip back (default 15s) |
| `repeat` | `loop` | `repeat <off|one|all>` | Set the repeat mode |
| `shuffle` | — | `shuffle [on|off|now]` | Turn shuffle on/off, or shuffle the queue now |

## Finding

| Command | Aliases | Usage | Description |
|---|---|---|---|
| `search` | `find` | `search <words>` | Search and list results to choose from |
| `pick` | — | `pick <number>` | Play a numbered result from your last search |
| `service` | `sv` | `service [name]` | Show or choose which service free-text searches |

## Queue

| Command | Aliases | Usage | Description |
|---|---|---|---|
| `queue` | `q` | `queue` | Show what's playing and what's next |
| `now` | `np` | `now` | Show the current track and position |
| `remove` | `rm` | `remove <number>` | Remove a track from the queue |
| `clear` | — | `clear` | Clear the queue (keep playing) |

## Access

| Command | Aliases | Usage | Description |
|---|---|---|---|
| `help` | `h`, `?` | `help [command]` | Show commands, or details for one |
| `about` | — | `about` | About this bot |
| `lock` | — | `lock` | Lock the bot (admins) or unlock it with the password |

## Admin

| Command | Aliases | Usage | Description |
|---|---|---|---|
| `say` | — | `say <message>` | Post a message to the channel _(admin only)_ |
| `quit` | `shutdown` | `quit` | Shut the bot down _(admin only)_ |

## Locking the bot

`lock` does two jobs with one command:

1. An **admin** (listed in `config.toml`) sends `lock`. The bot asks privately for a password to set; the admin's next private message becomes it. The bot locks and announces it in the channel, and all other commands are refused.
2. While locked, **anyone** can send `lock`. The bot asks privately for the password and unlocks (announcing it) if the next private message matches. Repeated wrong guesses trigger a short cooldown.

A non-admin who sends `lock` while unlocked gets an error. Send `cancel` at a password prompt to abort. The lock lives in memory, so restarting clears it.

## Examples

```text
play daft punk - get lucky        search and play the best match
play https://youtu.be/dQw4w9WgXcQ play a link (a playlist/album queues in full)
playnext lofi radio               queue something to play next
search chill beats                list results, then:  pick 3
url https://ice1.somafm.com/groovesalad-128-mp3   play internet radio
service soundcloud                search SoundCloud instead of YouTube
volume 60                         set the volume to 60%
seek 1:30                         jump to 1 minute 30 seconds
repeat all                        loop the whole queue
queue                             see what's playing and what's next
```
