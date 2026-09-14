# TeamTalk Annoying Tools — Android (alpha-soft)

A native **Kotlin + Jetpack Compose** Android port of the desktop suite in the
repository root. It drives the same TeamTalk 5 SDK — BearWare's **Java** SDK for
Android instead of the Python `ctypes` binding — and ports every tool from the
Linux edition.

This is an **experimental alpha** built for a small group of testers, not a
store release.

## What is ported

| Desktop tool | Android |
| --- | --- |
| `tt_message_spammer.py` | **Message sender** — channel or private message sequences |
| `tt_spammer.py` | **Login / logout cycles** |
| `tt_leave_join_spammer.py` | **Channel leave / join** |
| `tt_concurrent_bots.py` | **Idle bots** |
| `ttbot_the_offender.py` | **Response bot** — trigger-based, allowlisted, benign replies |
| `tt_suite.py` | **Combined suite** — discovery, sequential ops, concurrent bots |
| `tt_loic.py` | **Local flood test** — this device only |
| `tt_ramp.py` | **Ramp / breaking point** |

The shared core is a direct port of `tt_teamtalk.py`: SDK loading, the
first-run license gate, `ConnectionConfig`, and `TeamTalkSession` with its
single background event pump, command/event correlation and kick resistance.

## Requirements

- Android Studio **Koala (2024.1)** or newer, or a JDK 17 + Android SDK
  (compileSdk 35) command line setup.
- The **TeamTalk 5 Android SDK** from BearWare.dk:
  <https://bearware.dk/?page_id=419>

### Add the SDK (required — it is not redistributed here)

The SDK's license does not permit bundling it, so the build expects it locally:

```text
android/app/libs/TeamTalk5.jar                              # Java bindings
android/app/src/main/jniLibs/arm64-v8a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/armeabi-v7a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/x86_64/libTeamTalk5-jni.so
```

Copy **every** native library the SDK ships for an ABI into that ABI's folder
(the JNI wrapper may load more than one `.so`). `app/libs/*.jar` and
`src/main/jniLibs/**` are the only paths the build wires up. If your SDK build
is delivered as an `.aar` instead, drop it in `app/libs/` and add

```kotlin
implementation(":teamtalk-sdk@aar")
```

to `app/build.gradle.kts` (the `flatDir { dirs("app/libs") }` repository is
already configured in `settings.gradle.kts`).

## Build

The Gradle wrapper **jar** is intentionally not committed (it is a binary).
Generate it once, or just open the folder in Android Studio:

```bash
cd android
gradle wrapper            # if you have a system Gradle; or use Android Studio
./gradlew assembleDebug   # -> app/build/outputs/apk/debug/app-debug.apk
```

Install on a tester's device:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

For a small tester group a debug APK is usually enough. If you want a signed
release, provide the key material through `local.properties` (never committed)
or the environment:

```properties
# android/local.properties
tt.keystore=../tester.keystore
tt.keystore.password=...
tt.key.alias=tester
tt.key.password=...
```

or `TT_KEYSTORE`, `TT_KEYSTORE_PASSWORD`, `TT_KEY_ALIAS`, `TT_KEY_PASSWORD`.
`./gradlew assembleRelease` then produces a signed APK.

## First run

1. The TeamTalk 5 SDK license is shown once; choose **I accept** to persist the
   decision (the equivalent of the desktop `.tt-sdk-license-accepted` marker).
   Declining keeps runs disabled.
2. Open **Server** and fill in host, ports, and (optionally) credentials. Blank
   username/password means an anonymous login.
3. Open **Allowlist** and add the servers you are approved to test.
4. Open **Tools**, pick a test, review the parameters, confirm, and **Run**.

Runs execute on a foreground service, so the connections survive the app going
to the background; a notification shows the running test with a **Stop** action.

## Safety gates (same as the desktop suite)

- **Exact-host allowlist** — the suite, idle bots and ramp test refuse any host
  that is not listed on the Allowlist tab.
- **Confirmation** — the heavy tools require an explicit confirm toggle.
- **Local-only** — the flood test refuses any target that is not an address on
  this device.
- **Benign response bot** — the desktop tool's automatic-insult behaviour is not
  reproduced; this bot answers one explicit trigger, only allowlisted users,
  with a per-user cooldown.

## Behaviour differences from the desktop suite

These are deliberate, and the app says so where they matter:

- **No worker processes.** The desktop suite forks worker processes to stay
  under the native `select()` FD ceiling. Android runs bots as threads in one
  process, so `IdleBots` caps at **128** bots and concurrent suite mode caps at
  **64**; exceeding that is refused with a clear message rather than crashing.
- **No interactive prompts.** The CLI's numbered channel/recipient pickers are
  replaced by the discovery output plus the parameter form.
- **Recipient selection** uses the usernames/nicknames field; per-send identity
  resolution is by name, matching the desktop "track users by username" rule.
- **Flood duration** stays capped at 60 s per stage, and the ramp thread ceiling
  stays at 1024.

## Known alpha limitations

- The app has not been run on physical hardware in this repository, because the
  sandbox that produced it has no Android SDK/Gradle toolchain. Treat the first
  build as a bring-up: expect to fix SDK-path and ABI details for your own SDK
  download.
- `minSdk` is 24. Only the ABIs you copy `.so` files for will run.
- Minification is off so tester crash reports stay readable; the ProGuard rules
  already keep `dk.bearware.**` if you enable it later.

## Layout

```text
android/app/src/main/java/com/teamtalk/annoying/
  core/      SDK loading, license gate, config store, log bus, TeamTalkSession
  tools/     one file per desktop tool + the tool registry and executor
  run/       ToolRunManager (the single running tool)
  service/   RunService (foreground service keeping the run alive)
  ui/        Compose theme, nav, screens, view model
```

## Credits

The desktop tools this ports credited **blindelectron**, **RD-Productions**,
**Simpter** and **Patrick Wilson**. TeamTalk and the TeamTalk SDK are by
**BearWare.dk**.
