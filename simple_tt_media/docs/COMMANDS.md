# Command reference

Send these to the bot in a **private message** on TeamTalk. (If you enabled a channel prefix during setup, you can also type `<prefix>command` in the channel.)

Every reply is detailed, and an unknown command always gets an error that points you back to `h`.

## Playback

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `p` | `play` | `p <words or link>` | Play/queue a search or link (no args toggles pause) |
| `url` | — | `url <link>` | Play a direct media or radio stream link |
| `s` | `search`, `find` | `s <words>` | Search and show a numbered list to choose from |
| `sel` | `select` | `sel <number>` | Play a numbered result from your last search |
| `stop` | — | `stop` | Stop playback and clear the queue |
| `pause` | — | `pause` | Pause playback |
| `resume` | — | `resume` | Resume playback |
| `pp` | `playpause` | `pp` | Toggle pause/resume |
| `next` | `skip`, `n` | `next` | Skip to the next track |
| `prev` | `previous`, `b` | `prev` | Play the previous track |
| `v` | `vol`, `volume` | `v [0-max]` | Show or set the volume (percent) |
| `seek` | — | `seek <m:ss | seconds>` | Jump to a position in the current track |
| `ff` | `fwd` | `ff [seconds]` | Skip forward (default 15s) |
| `rw` | `rewind` | `rw [seconds]` | Skip backward (default 15s) |

## Queue

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `repeat` | `loop` | `repeat <off|track|queue>` | Set the repeat mode |
| `shuffle` | `sh` | `shuffle [on|off|queue]` | Toggle shuffle, or shuffle the queue now |
| `q` | `queue` | `q` | Show the queue and what's playing |
| `clear` | `cl` | `clear` | Clear the queue (keep playing) |

## Information

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `np` | `now`, `nowplaying` | `np` | Show the current track and position |
| `sv` | `service` | `sv [name]` | Show or set which service free-text searches |
| `services` | — | `services` | List searchable services |
| `h` | `help`, `?`, `commands` | `h [command]` | Show this help (or details for one command) |
| `about` | `info` | `about` | About this bot |

## Admin

| Command | Aliases | Usage | What it does |
|---|---|---|---|
| `say` | — | `say <message>` | Make the bot post a message in its channel _(admin only)_ |
| `quit` | `shutdown` | `quit` | Shut the bot down _(admin only)_ |

## Examples

```text
p daft punk get lucky              play the top match on the current service
p https://youtu.be/dQw4w9WgXcQ     play a link (a playlist/album queues in full)
s lofi beats                       show a numbered list, then:  p 3
url https://ice1.somafm.com/groovesalad-128-mp3   play internet radio
sv soundcloud                      search SoundCloud instead of YouTube
v 60                               set the volume to 60%
seek 1:30                          jump to 1 minute 30 seconds
repeat queue                       loop the whole queue
h seek                             detailed help for one command
```
