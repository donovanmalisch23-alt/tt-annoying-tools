# TeamTalk Annoying Tools — Linux SDK Edition

This repository now uses the official TeamTalk 5 Python interface and
`libTeamTalk5.so` for TeamTalk operations. The Python programs no longer
control a desktop TeamTalk window, use Wine, or depend on xdotool/clipboard
helpers.

Use these tools only on a TeamTalk server you own or administer, and only
where the participants have agreed to the test. The old bot's
automatic-insult behavior is not included; its Linux entry point is a
trigger-based benign reply bot instead.

## Install the SDK

Download the Linux TeamTalk 5 SDK from [BearWare.dk's SDK download page](https://bearware.dk/?page_id=419).
The SDK includes the `TeamTalk5.py` Python interface and the native Linux
library. The low-level API is documented in the [TeamTalk C-API reference](https://www.bearware.dk/teamtalksdk/v5.22a/docs/C-API/).

This checkout reads SDK and connection settings from a local `teamtalk.env`
file, which is intentionally ignored by Git because it contains credentials
and machine-specific paths. Copy `teamtalk.env.example` to `teamtalk.env`,
then edit it or set the variables manually. Exported variables override the
local file.

For a self-contained local test server:

```bash
./local_server/start.sh
```

The local server uses `loadtest/loadtest` and creates `/LoadTest`. The Python
tools load those defaults automatically. You can pass the same values as
command-line options. For a nonstandard SDK layout, use
`--sdk-python /path/to/TeamTalk5.py` and `--sdk-library
/path/to/libTeamTalk5.so`.

The SDK itself may require a valid TeamTalk SDK license/trial according to its
license terms. No native SDK binaries are bundled in this repository.

## Linux entry points

All of these are ordinary Python 3 programs and call TeamTalk directly:

| Program | Purpose |
| --- | --- |
| `tt_message_spammer.py` | Send a configurable channel or private text message sequence. |
| `tt_spammer.py` | Run repeated TeamTalk login/logout cycles. |
| `tt_leave_join_spammer.py` | Run a configurable channel leave/join test. |
| `ttbot_the_offender.py` | Run the safe trigger-based response bot described above. |
| `tt_suite.py` | Discover channels/users and run consent-aware combined tests. |

Hyphenated filename-compatible launchers are also provided as
`tt-message-spammer.py`, `tt-leave-join-spammer.py`, and
`ttbot-the-offender.py`. The combined runner is also available as
`tt-suite.sh`.

Running a tool with no arguments opens prompts, just like the original tools:

```bash
./tt-message-spammer.sh
./tt-leave-join-spammer.sh
./tt-suite.sh
./ttbot-the-offender.py
```

The message tool prompts for the original text, count, delay in milliseconds,
and startup wait in seconds, then asks for the API target and connection
values, including an explicit encrypted-connection prompt. Enter accepts the
configured defaults from `teamtalk.env`. The login/logout tool keeps one SDK
connection open while it repeats authenticated login/logout cycles without
joining a channel. The leave/join tool joins the configured channel, then
leaves and rejoins it for each requested cycle.
No desktop window focus or paste step is needed.

Command-line options remain available for scripted runs:

```bash
python3 tt_message_spammer.py --message 'Hello' --count 1
python3 tt_message_spammer.py --message 'Test' --count 3 --interval 0
python3 tt_spammer.py --cycles 5 --interval 0
python3 tt_leave_join_spammer.py --cycles 1 --interval 0
python3 ttbot_the_offender.py --allow-all
./tt-suite.sh --all-channels --all-users --join-leave-cycles 1 \
  --channel-message 'channel test' --private-message 'private test' \
  --message-count 1 --confirm
```

The combined runner reads an exact, one-host-per-line allowlist from
`whitelist.txt` before it connects. Copy `whitelist.txt.example` to
`whitelist.txt` and add only servers that are approved for testing. Use
`--all-channels` (or `--channel-path all`) to select every discovered channel,
and `--all-users` (or `--user-id all`) to select every discovered online user.
Bulk channel and private-message actions require `--confirm`; `--dry-run`
discovers and prints targets without joining or sending.

Message sends and leave/join tests accept a zero delay; there is no enforced
one-second delay between messages. The message tool accepts any positive send
count and its interactive private-message picker can select up to 20 users per
run. Login/logout and leave/join tools also accept any positive cycle count.
The response bot requires
an explicit allowlist or `--allow-all`, responds only to `!hello` by default,
applies a per-user cooldown, and stops after 100 replies unless configured
otherwise.

Run `python3 <program> --help` for all connection, channel, and SDK options.
Passwords should normally be supplied through `TT_PASSWORD` rather than the
command line so they do not appear in shell history or process listings.

## Compatibility launchers

The original shell names remain as small launchers:

```bash
./tt-message-spammer.sh
./tt-leave-join-spammer.sh
./tt-exe-runner.sh
```

`tt-exe-runner.sh` now selects the Python implementations; it no longer
invokes Wine. The `.exe` files and the original VBS files are retained as
historical compatibility artifacts only and are not part of the Linux path.

## Audio files

The `.ogg`, `.wav`, and `.flac` files remain media inputs. They are not played
automatically by the Python tools; use TeamTalk's own media controls or an
approved audio-routing setup, at safe volume, on a consenting test channel.

## Credits

The original project credited **blindelectron**, **RD-Productions**,
**Simpter**, and **Patrick Wilson**.
