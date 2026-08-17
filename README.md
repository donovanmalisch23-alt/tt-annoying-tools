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
license terms. The TeamTalk 5 SDK files are bundled under `sdk/` (see above).

## Building standalone binaries

`build_binaries.py` freezes each tool into a one-file executable with
PyInstaller, bundling `TeamTalk5.py` and the native library so the result runs
without a separate SDK install. Locally (on a machine with pip and the SDK
extracted):

```bash
pip install pyinstaller
TEAMTALK_SDK_PATH=/path/to/extracted/sdk python3 build_binaries.py
# executables appear in dist/
```

The GitHub Actions workflow `.github/workflows/build-binaries.yml` builds both
platforms automatically — it downloads the TeamTalk SDK for each OS, builds, and
uploads `tt-linux-x86_64` and `tt-windows-x64` artifacts. Trigger it from the
Actions tab or:

```bash
gh workflow run build-binaries.yml
gh run watch        # wait for completion
gh run download <run-id>   # pulls both artifact zips
```

### Trial expiration (the 30-day SDK limit)

The binaries bundle BearWare.dk's **public TeamTalk SDK trial**. It runs in
"TRAIL MODE" and **self-disables after 30 days of use**. The TeamTalk *tools*
themselves never expire — only the bundled SDK connection layer does. After the
trial lapses a tool still launches and prints `--help`, but it can no longer
connect to a server.

To keep the binaries working past 30 days, do one of the following:

- **Re-download (or rebuild) a fresh binary.** Each freshly built binary bundles
  the then-current SDK trial, giving a new trial window. Re-download from the
  GitHub *Releases* page, or re-run the build workflow. Re-downloading the
  *same* build will not reset a trial that has already expired — to get a new
  window you need a build that bundles a **newer** SDK release. When BearWare.dk
  publishes a newer SDK, bump the `sdk_url` values in
  `.github/workflows/build-binaries.yml` and re-run the workflow. In practice:
  come back roughly every 30 days and grab the latest release.
- **Activate your own TeamTalk SDK license (removes the limit entirely).** A
  one-time, royalty-free TeamTalk SDK license from BearWare.dk (Standard edition,
  ~€990, all platforms) comes with a registration name and key. Pass them to the
  tools and the 30-day limit is gone for everyone who downloads the release:

  ```bash
  # environment variables (preferred — keeps the key out of shell history):
  export TT_LICENSE_NAME="Your Name"
  export TT_LICENSE_KEY="your-serial-key"
  ./tt-suite --host your.server --all-channels --all-users --channel-message 'hi' --confirm

  # or flags:
  ./tt-suite --license-name "Your Name" --license-key "your-serial-key" \
    --host your.server --all-channels --all-users --channel-message 'hi' --confirm
  ```

  For a release that is permanently non-expiring, build against the **licensed**
  SDK (set the workflow's `sdk_url` to the licensed edition) — the licensed
  DLLs are not time-bombed. The license key still needs to reach the running
  binary, so either distribute the key alongside the release and have users set
  `TT_LICENSE_NAME` / `TT_LICENSE_KEY` (the license is royalty-free, so this is
  permitted), or wire a build-time default into `build_binaries.py` if you want
  the key shipped inside the executable.

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

There is no built-in ceiling on how many bots, messages, or cycles you request
— `--churn-bots`, `--message-count`, and `--churn-cycles` accept any positive
integer, so the practical limit is your server's own max-user setting and what
your test machine can sustain. Integer count arguments accept ``_`` and ``,``
thousands separators, so `--churn-bots 10,999` and `--churn-bots 10999` are
equivalent. The discovery connection and every bot still go through
`whitelist.txt` + `--confirm` + `--dry-run`. Without `--concurrent` the suite
keeps its original sequential single-session behavior.

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
