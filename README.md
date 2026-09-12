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

This repository **bundles the TeamTalk 5 SDK** under `sdk/`
(`TeamTalk5.py`, `libTeamTalk5.so`, and the upstream `License.txt`), so the
tools work out of the box without a separate SDK download. You can still
point at your own SDK build with `TEAMTALK_SDK_PYTHON` / `TEAMTALK_SDK_LIBRARY`
or `--sdk-python` / `--sdk-library`. The low-level API is documented in the
[TeamTalk C-API reference](https://www.bearware.dk/teamtalksdk/v5.22a/docs/C-API/).

### First-run license acceptance

The TeamTalk 5 SDK `License.txt` states that use of the SDK is not permitted
until you have read and agreed to its terms. On the **first run** of any tool
the SDK is used, the tool prints the bundled license text and prompts:

```
Do you accept the binding terms of the TTSDK license? (Y/N)
```

Answer `Y` to continue; the decision is saved in `.tt-sdk-license-accepted`
next to the tools so later runs skip the prompt. Answer `N` (or pass
`--decline-sdk-license` / `TT_ACCEPT_SDK_LICENSE=0`) to refuse — the SDK will
not be loaded. For scripted / CI runs, pre-approve with
`--accept-sdk-license` or `TT_ACCEPT_SDK_LICENSE=1`.

This checkout reads connection settings from a local `teamtalk.env`
file, which is intentionally ignored by Git because it can contain
credentials. Copy `teamtalk.env.example` to `teamtalk.env`,
then edit it or set the variables manually. Exported variables override the
local file. Username and password default to blank, and blank credentials log
in anonymously — leave `TT_USERNAME`/`TT_PASSWORD` unset for servers that
accept anonymous logins.

For a self-contained local test server:

```bash
python3 local_server/start.py
```

The local server uses `loadtest/loadtest` and creates `/LoadTest`. The Python
tools load those defaults automatically. You can pass the same values as
command-line options. For a nonstandard SDK layout, use
`--sdk-python /path/to/TeamTalk5.py` and `--sdk-library
/path/to/libTeamTalk5.so`.

### Tests

The suite in `src/tests/` is hermetic: `python3 -m pytest src/tests/` runs it
with no server and no SDK connection. One module is the exception —
`test_tt_live_roundtrip.py` connects to the local server above and verifies
that messages really travel through TeamTalk: login/logout, channel and
private (user) messages through the session layer, the same through the
`tt_message_spammer` tool, the `tt_spammer` login/logout cycles, and the
`tt_concurrent_bots` idle bots (messages must still flow while they occupy the
channel). It skips itself when the local server is not running, so start
`python3 local_server/start.py` first to include it.

The SDK itself may require a valid TeamTalk SDK license/trial according to its
license terms. The TeamTalk 5 SDK files are bundled under `sdk/` (see above).

## SDK trial expiration (the 30-day limit)

The bundled SDK is BearWare.dk's **public TeamTalk SDK trial**. It runs in
"TRAIL MODE" and **self-disables after 30 days of use**. The TeamTalk *tools*
themselves never expire — only the SDK connection layer does. After the trial
lapses a tool still launches and prints `--help`, but it can no longer connect
to a server.

To keep connecting past 30 days, do one of the following:

- **Refresh the vendored SDK.** When BearWare.dk publishes a newer SDK, copy its
  `TeamTalk5.py` and `libTeamTalk5.so` into `sdk/` — each newer trial gives a
  new 30-day window. Check the version you carry with
  `python3 -c "import sys; sys.path.insert(0, 'sdk'); import TeamTalk5; print(TeamTalk5.TT_GetVersion())"`.
- **Activate your own TeamTalk SDK license (removes the limit entirely).** A
  one-time, royalty-free TeamTalk SDK license from BearWare.dk (Standard edition,
  ~€990, all platforms) comes with a registration name and key. Pass them to the
  tools and the 30-day limit is gone:

  ```bash
  # environment variables (preferred — keeps the key out of shell history):
  export TT_LICENSE_NAME="Your Name"
  export TT_LICENSE_KEY="your-serial-key"
  python3 tt_suite.py --host your.server --all-channels --all-users --channel-message 'hi' --confirm

  # or flags:
  python3 tt_suite.py --license-name "Your Name" --license-key "your-serial-key" \
    --host your.server --all-channels --all-users --channel-message 'hi' --confirm
  ```

  The licensed DLLs are not time-bombed, so replacing `sdk/` with the licensed
  edition makes the limit disappear for every run.

When no license name/key are supplied the activation call is skipped, so trial
builds behave exactly as before. See BearWare.dk's
[SDK license page](https://bearware.dk/?page_id=316) and the
[C-API license notes](https://www.bearware.dk/teamtalksdk/v5.22a/docs/C-API/license.html)
for pricing and terms.

## Linux entry points

All of these are ordinary Python 3 programs and call TeamTalk directly:

| Program | Purpose |
| --- | --- |
| `tt_message_spammer.py` | Send a configurable channel or private text message sequence. |
| `tt_spammer.py` | Run repeated TeamTalk login/logout cycles. |
| `tt_leave_join_spammer.py` | Run a configurable channel leave/join test. |
| `ttbot_the_offender.py` | Run the safe trigger-based response bot described above. |
| `tt_suite.py` | Discover channels/users and run consent-aware combined tests, including an optional concurrent multi-bot mode. |
| `tt_loic.py` | LOIC-style TCP/UDP flood modes against a TeamTalk server on this machine, with before/during/after service probes. |
| `tt_ramp.py` | Ramped breaking-point capacity test: steps the `tt_loic` flood in geometric stages and classifies each as healthy / degraded / broken, stopping at the first break. Per-stage durations (`--stage-durations`), all-stages-at-once (`--simultaneous`), a fixed whole-ramp time frame (`--total-time`), and a wall-clock cap (`--max-total-time`) are supported. |

Running a tool with no arguments opens prompts, just like the original tools:

```bash
python3 tt_message_spammer.py
python3 tt_leave_join_spammer.py
python3 tt_suite.py
python3 tt_loic.py
python3 tt_ramp.py
python3 ttbot_the_offender.py
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
python3 tt_suite.py --all-channels --all-users --join-leave-cycles 1 \
  --channel-message 'channel test' --private-message 'private test' \
  --message-count 1 --confirm
```

The suite's `--concurrent` mode splits the per-user, per-channel, and login/out
work across concurrent bots, each on its own SDK connection: one user-bot
private-messages every discovered user, one channel-bot messages every
discovered channel, and any number of churn-bots each repeat login/logout
cycles. It reuses the same `whitelist.txt` gate and requires `--confirm` (or
`--dry-run` to preview the bot plan):

```bash
# Preview the discovered targets and the bot plan without sending:
python3 tt_suite.py --dry-run --concurrent \
  --private-message 'private test' --channel-message 'channel test'

# Run: one user-bot (DMs every discovered user), one channel-bot (messages every
# discovered channel), and three churn-bots (each repeats 10 login/logout cycles):
python3 tt_suite.py --concurrent \
  --private-message 'private test' --channel-message 'channel test' \
  --message-count 2 --churn-bots 3 --churn-cycles 10 --interval 0.1 --confirm
```

`--churn-bots`, `--message-count`, and `--churn-cycles` accept any positive
integer, so the practical limit on messages and cycles is your server's own
max-user setting and what your test machine can sustain. Integer count
arguments accept ``_`` and ``,`` thousands separators, so `--churn-bots 10,999`
and `--churn-bots 10999` are equivalent. The discovery connection and every bot
still go through `whitelist.txt` + `--confirm` + `--dry-run`. Without
`--concurrent` the suite keeps its original sequential single-session behavior.

There **is** a per-process ceiling on the number of *concurrent* bots, because
each bot opens its own SDK connection and the native library drives each one
with a `select()` reactor that cannot address file descriptors at or above
`FD_SETSIZE` (1024). The suite computes the safe maximum from the process's
file-descriptor limit (roughly `(1024 − 16) / 4 ≈ 252` bots on a typical Linux
box). When you ask for more than that, the suite **splits the bots across
multiple worker processes** — each running up to the ceiling — and supervises
them from the parent, so a request for 1000 churn-bots becomes four workers of
~252 bots each instead of a core dump. Raising the process's descriptor limit
(`ulimit -n`) raises the per-worker ceiling and therefore reduces the number
of workers.

### One bot per channel / one bot per user

`--bot-per-channel` and `--bot-per-user` (both require `--concurrent`) scale the
concurrent mode out to one dedicated bot per target, each on its **own SDK
connection**:

- `--bot-per-channel` spawns one channel-bot per discovered/selected channel
  (each joins and messages only its channel) instead of a single channel-bot
  handling every channel.
- `--bot-per-user` spawns one user-bot per selected user (each private-messages
  only its assigned user) instead of a single user-bot messaging everyone. With
  `--all-users` this snapshots the currently online users (one bot each); it
  does **not** run the continuous new-joiner mode.

```bash
# One channel-bot per channel and one user-bot per user (3 users, 4 channels):
python3 tt_suite.py --concurrent --bot-per-channel --bot-per-user   --all-channels --user amy,bob,carol   --channel-message 'channel test' --private-message 'private test'   --message-count 1 --join-leave-cycles 1 --confirm

# Preview the per-target bot plan without sending:
python3 tt_suite.py --dry-run --concurrent --bot-per-channel --bot-per-user   --all-channels --all-users --channel-message 'hi' --private-message 'hi'
```

The number of per-target bots is bounded by the same file-descriptor ceiling as
`--churn-bots` (each bot is a separate SDK connection and login); the suite
refuses to start if the total concurrent bot count would exhaust the native
library's `select()` reactor (see the concurrent-mode note above).

With `--all-users` (or `--user-id all`) the user-bot runs in **continuous
mode**: instead of messaging a fixed list once, it keeps re-discovering the
online users and messages every joiner it has not messaged yet, each receiving
`--message-count` messages, with no repeats. It stays running until you stop it
with Ctrl+C (the churn-bots and channel-bot still finish their finite counts and
exit on their own). This is the mode to use when you want the bot to notice
people who connect *after* the run starts.

Discovery works by pumping the SDK's event queue each sweep — the TeamTalk
client only learns a user logged in once it processes the incoming
`USER_LOGGEDIN` event, so without pumping the roster freezes at the bot's own
login time and late joiners are invisible. The sweep cadence is
`--sweep-interval` (default `0.5`, i.e. every 500 ms): the bot drains pending
events, waits that long, then re-checks `getServerUsers()` and messages anyone
new. `--interval` remains the pause *between* the `--message-count` messages
sent to a single user, independent of the sweep cadence.

```bash
# Keep DMing every current user and any new joiner, re-checking every 500 ms:
python3 tt_suite.py --concurrent --all-users \
  --private-message 'welcome aboard' --message-count 1 --sweep-interval 0.5 \
  --interval 1.0 --confirm
```

Continuous mode only changes the user-bot. The `--channel-message` bot and any
`--churn-bots` still terminate after their finite counts as usual, so the run
ends when you interrupt the user-bot and those bots have finished.

### Kick resistance (all tools)

Every tool reconnects and resumes after a kick or disconnect. When a bot loses
its connection, it waits `--reconnect-delay` seconds (default `3.5`, matching
the "3.5 s" check interval), checks whether it is still online, and if not it
rebuilds its SDK connection, logs back in, rejoins its working channel, and
resumes the loop from where it was. Idle bots (the response bot) run the same
online check on a watchdog while they wait for events, so they notice a server
kick within the delay. Turn it off with `--no-kick-resistance` (or
`TT_KICK_RESISTANCE=0`) to make a tool stop when disconnected instead.

```bash
# churn bot that reconnects after every kick, 2 s between checks:
python3 tt_spammer.py --cycles 1000 --interval 0.1 --reconnect-delay 2 --confirm
```

After releasing the SDK client, every tool waits `TT_SHUTDOWN_SETTLE_SECONDS`
seconds (default `1.0`) before the process exits. The native library tears
down its internal threads asynchronously, and exiting immediately could
segfault the process while they were still winding down. Set the variable to
`0` to skip the wait if you do not need it.

**Protection keeps the counts exact.** When server flood protection kicks in
mid-run, the interrupted operation — the one message, login cycle, or
leave/join pair that was in flight — is retried after the reconnect, and only
operations that actually completed count toward the total. The numbering never
restarts and no operation is ever sent twice, so the server sees exactly the
number of messages, login attempts, and cycles you asked for: `--message-count
3 --cycles 5` is always 3 delivered messages and 5 completed cycles, no matter
how often protection fires in between. Retries are bounded (three consecutive
failures against the same target give up cleanly) so a permanently unreachable
target cannot turn into a reconnect/retry storm against the server.

### Users are tracked by username, not user ID

Server-assigned user IDs change on every login, so every tool keys what it
remembers about a person on their **username** instead:

- Select private-message recipients with `--user amy,bob` (repeat the flag or
  comma-separate) in `tt_suite.py` and `tt_message_spammer.py`, and allowlist
  response-bot users with `--allow-user amy` in `ttbot_the_offender.py`.
  Matching is case-insensitive and also accepts a user's nickname.
- The legacy `--user-id` / `--allow-user-id` flags still work: each ID is
  resolved to that user's username once, against the discovery roster, and the
  run tracks the name from then on. `--user-id all` still selects every
  discovered user. Prefer the name flags — an ID only identifies a login
  session, not a person.
- A recipient's current ID is looked up by name at every send — not only
  after a reconnect — so a user who relogs mid-run (same nickname, brand-new
  server ID) is retargeted automatically: the new ID becomes the target for
  their remaining messages, and only their remaining ones.
- The message tool's interactive recipient picker also returns your selection
  as names (username, or nickname when there is no username), never as IDs.
  A user with no name at all is the one exception: an ID is the only
  identity they have.
- The response bot's allowlist and per-user cooldown also key on the username,
  so someone cannot dodge their cooldown by reconnecting, and an allowlisted
  user stays allowlisted across a relog.
- Discovery prints users by name (`Amy — /Lobby`, or `Bobby (@bobby)` when the
  nickname differs from the username), never as bare IDs.

### LOIC-style flood test (this machine only)

`tt_loic.py` reproduces LOIC's two flood modes — a TCP junk-data flood and a
UDP junk-datagram flood — as a plain CLI (LOIC itself is a Windows GUI app,
unusable with a screen reader), then measures what the flood actually does to
a TeamTalk server: connection latency, login, and message round-trips before,
during, and after the flood, with a plain-language verdict.

It is **local-only by construction**: the target must resolve to an address on
this machine (loopback or one of its own interfaces) — anything else is
refused — the flood length is capped at 60 seconds, and `--confirm` is
required on the flag path. Point it only at a server you run on this machine,
such as the localhost server shipped with the `tt5-loadtest` project. Running
it with no arguments opens the same prompts as every other tool — server
host, TCP port, UDP port, account; Enter accepts the `teamtalk.env`
defaults — then applies the local-only check and asks one go/no-go question
that defaults to No before the flood starts.

```bash
# Same prompts as every other tool, then a go/no-go that defaults to No:
python3 tt_loic.py

# 10 s of TCP+UDP junk against the local server, 8 threads per mode,
# probing service impact throughout (needs the local server running):
python3 tt_loic.py --confirm

# UDP only, 16 threads, 30 s, no SDK probes:
python3 tt_loic.py --mode udp --threads 16 --duration 30 --no-probe --confirm
```

The probe logs in with `--probe-username`/`--probe-password` (default blank:
a blank username and password log the probe in anonymously, which servers
without user accounts accept) and measures a fresh
TCP connect plus a message round trip per probe (a TeamTalk server relays a
channel message to the other users in the channel, never back to its sender,
so the probe logs in twice: a sender and a receiver). If the flood
knocks the probe out, that is reported as a finding, not hidden. If the
configured `--probe-channel` does not exist on the server, the probe says so
and measures from the root channel instead, which every TeamTalk server has.

### Ramped breaking-point test (whitelisted hosts)

`tt_ramp.py` builds on the `tt_loic` flood to find where a TeamTalk
server actually stops coping. It ramps the flood in geometric stages
(1 → 2 → 4 → 8 → … threads by default, tunable with `--start-threads`,
`--ramp-factor`, and `--max-threads`), and at each stage runs the same
sender+receiver service probe to classify the stage as **healthy**,
**degraded** (round-trip ≥ 2× baseline, or any probe failed), or **broken**
(zero probes succeeded). It stops at the first broken stage and prints a
plain-language summary: "BREAKS at N threads", where degradation first
starts, and the highest load that still held cleanly.

It keeps every `tt_loic` safety gate except the local-only check — `--dry-run`
to preview the stage plan, `--confirm` required on the flag path — and reuses
the `whitelist.txt` gate from `tt_suite`, which is the sole authorization
gate: any host listed in `whitelist.txt` is a legal target, whether or not it
is this machine. Its own thread ceiling
(`--max-threads`, default 64, max 1024) is independent of `tt_loic`'s
64-thread cap, so the two tools do not interfere. Running it with no
arguments opens the same prompts as every other tool — server host, TCP port,
UDP port, account; Enter accepts the `teamtalk.env` defaults — then applies
the whitelist check, asks how long you would like the test to run for in
total (0 keeps the default per-stage plan; any other number is the frame
the stages are sized to fill), and asks one go/no-go question that
defaults to No before the ramp starts.

The schedule is tunable four ways (each also has an environment variable,
so a ramp can be configured without flags):

- `--stage-durations 5,10,20` — give each stage its own flood length.
  Stage 1 runs 5 s, stage 2 runs 10 s, and every later stage repeats the
  last entry (20 s). Without it, every stage uses `--stage-duration`.
- `--simultaneous` — flood every stage at the same time instead of one
  after another: all stages start together, each stops after its own
  duration, and the server sees the combined load (the sum of all stage
  threads) at once.
- `--total-time 70` — a fixed time frame for the whole ramp: sequential
  stages split it evenly (70 s over the default 7 stages is 10 s each), so
  the run fills the frame exactly, and with `--simultaneous` every stage
  runs the full frame. It replaces `--stage-duration`/`--stage-durations`
  and is refused if a stage would fall outside the 1–60 s per-stage bound
  (fix it by changing the frame or the stage count).
- `--max-total-time 90` — a hard wall-clock cap for the whole ramp. When
  it expires, every flood stops and the ramp reports the results collected
  so far.

```bash
# Same prompts as every other tool, then a go/no-go that defaults to No:
python3 tt_ramp.py

# Preview the stage plan without flooding:
python3 tt_ramp.py --confirm --dry-run

# Ramp 1 → 64 threads, 10 s per stage (needs the local server running):
python3 tt_ramp.py --host 127.0.0.1 --confirm

# Soak: ramp 64 → 1024 threads, 60 s per stage, to find the real ceiling:
python3 tt_ramp.py --host 127.0.0.1 --confirm --start-threads 64 \
  --ramp-factor 2 --max-threads 1024 --stage-duration 60

# Stage-specific lengths: stage 1 for 5 s, stage 2 for 10 s, later stages 20 s:
python3 tt_ramp.py --host 127.0.0.1 --confirm --stage-durations 5,10,20

# All stages flood at the same time, whole run capped at 90 s:
python3 tt_ramp.py --host 127.0.0.1 --confirm --simultaneous --max-total-time 90

# Fixed frame: the whole ramp auto-sizes to fill exactly 70 s (10 s a stage):
python3 tt_ramp.py --host 127.0.0.1 --confirm --total-time 70

# Same thing, configured through the environment instead of flags:
TT_RAMP_STAGE_DURATIONS=5,10,20 TT_RAMP_SIMULTANEOUS=1 \
  TT_RAMP_MAX_TOTAL_TIME=90 python3 tt_ramp.py --host 127.0.0.1 --confirm
TT_RAMP_STAGE_DURATIONS=5,10,20 TT_RAMP_SIMULTANEOUS=1 \
  TT_RAMP_MAX_TOTAL_TIME=90 python3 tt_ramp.py --host 127.0.0.1 --confirm
```

Use the breaking point it reports to size server hardening: the accept
backlog / `somaxconn`, per-IP connection-rate limiting, and a
max-connections cap.

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
run, tracked by name from the moment you pick them. Login/logout and
leave/join tools also accept any positive cycle count.
The response bot requires
an explicit allowlist (`--allow-user` by username, or `--allow-user-id`) or
`--allow-all`, responds only to `!hello` by default, applies a per-user
cooldown keyed on the username, and stops after 100 replies unless configured
otherwise.

Run `python3 <program> --help` for all connection, channel, and SDK options.
Passwords should normally be supplied through `TT_PASSWORD` rather than the
command line so they do not appear in shell history or process listings.

## Audio files

The `.ogg`, `.wav`, and `.flac` files in `media/` remain media inputs. They are
not played automatically by the Python tools; use TeamTalk's own media controls
or an approved audio-routing setup, at safe volume, on a consenting test
channel.

## Credits

The original project credited **blindelectron**, **RD-Productions**,
**Simpter**, and **Patrick Wilson**.
