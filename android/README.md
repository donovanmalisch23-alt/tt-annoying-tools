# TeamTalk Annoying Tools — Android (alpha-soft)

A native **Kotlin + Jetpack Compose** Android port of the desktop suite in the
repository root. It drives the same TeamTalk 5 SDK — BearWare's **Java** SDK for
Android instead of the Python `ctypes` binding — and ports every tool from the
Linux edition.

**The SDK runs inside the app.** The Java bindings and the per-ABI
`libTeamTalk5-jni.so` are linked into the APK, so connections are opened from the
process the tester is holding: there is no desktop bridge, no helper process and
no loopback server between the app and the TeamTalk server. (The unrelated
`android-panel/` wrapper works the other way round — it serves the web panel and
reaches live mode through the Python bridge on a desktop.)

This is an **experimental alpha** built for a small group of testers, not a
store release.

## The screens

| Screen | What it does |
| --- | --- |
| **Tools** | Opens with **"Select a tool below"** and the list of all eight tests, grouped into gentle and heavy-load rows. It carries the target, the allowlist size and the on-device SDK version, and ends with the **admin panel** button. |
| **Tool detail** | One test: its parameters, the gates it will demand, Run/Stop, and the last result. |
| **Log** | The live run log, with copy and clear. |
| **About** | What this is, where the SDK runs, the admin panel, the limits and the credits. |
| **Admin panel** | Allowlist, target server, SDK license and the reset actions — behind the administrator credential. |

## The admin panel

Everything that changes how the app behaves is reached from the bottom of the
Tools page and is gated:

- **First run** asks for an administrator name and password (6 characters
  minimum) and provisions the credential.
- Only a **PBKDF2-HMAC-SHA256** hash, a random 16-byte salt and the iteration
  count are stored, in the app's private storage (`SharedPreferences`). On
  API 24/25, where that provider is unavailable, it falls back to
  PBKDF2-HMAC-SHA1 and records which algorithm it used.
- Hashes are compared with `MessageDigest.isEqual`, and **five failed attempts**
  lock sign-in for 60 seconds.
- The panel **re-locks on every restart** — the unlocked state lives in the view
  model, not in storage.
- Inside, four sections: the **target server** (host, ports, account, default
  channel, behaviour, optional SDK registration), the **allowlist** (add and
  remove hosts one at a time, or edit the file as text; the configured target is
  marked), the **SDK license** decision, and a **reset** section (clear the log,
  reset the allowlist, forget the administrator).

The allowlist is the gate every bulk tool checks, so only the administrator can
widen it.

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

The shared core is a direct port of `tt_teamtalk.py`: SDK loading, the first-run
license gate, `ConnectionConfig`, and `TeamTalkSession` with its single
background event pump, command/event correlation and kick resistance.

## Requirements

- JDK 17 and an Android SDK with **compileSdk 35** and build-tools 35, plus
  Gradle 8.7 or newer (or Android Studio Koala 2024.1+).
- The **TeamTalk 5 Android SDK** from BearWare.dk:
  <https://bearware.dk/?page_id=419>

### Add the SDK (required — it is not redistributed here)

The SDK's license does not permit bundling it in this repository, so the build
expects it locally. The exact steps, using the 5.22a build this project is
developed against:

```bash
curl -LO https://www.bearware.dk/teamtalksdk/v5.22a/tt5sdk_v5.22a_android.7z
7z x tt5sdk_v5.22a_android.7z          # or unzip/7-Zip on your platform

SDK=tt5sdk_v5.22a_android/Client/TeamTalkAndroid
cp "$SDK/libs/TeamTalk5.jar" android/app/libs/

for abi in arm64-v8a armeabi-v7a x86 x86_64; do
  mkdir -p "android/app/src/main/jniLibs/$abi"
  cp "$SDK/src/main/jniLibs/$abi/libTeamTalk5-jni.so" "android/app/src/main/jniLibs/$abi/"
done
```

```text
android/app/libs/TeamTalk5.jar                              # Java bindings
android/app/src/main/jniLibs/arm64-v8a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/armeabi-v7a/libTeamTalk5-jni.so
android/app/src/main/jniLibs/x86_64/libTeamTalk5-jni.so
```

`app/libs/*.jar` and `src/main/jniLibs/**` are the only paths the build wires up,
and both are gitignored — copy every ABI you want to build for, then narrow the
packaged set with `ndk.abiFilters` in `app/build.gradle.kts` (it ships as
`arm64-v8a` only, which covers the phones this alpha targets). If your SDK build
arrives as an `.aar` instead, drop it in `app/libs/` and add
`implementation(":teamtalk-sdk@aar")` to `app/build.gradle.kts` (the
`flatDir { dirs("app/libs") }` repository is already configured).

## Build

The Gradle wrapper **jar** is intentionally not committed (it is a binary), so
either generate it once, open the folder in Android Studio, or call a system
Gradle:

```bash
cd android
export JAVA_HOME=/path/to/jdk17
export ANDROID_HOME=/path/to/android-sdk
gradle assembleDebug      # -> app/build/outputs/apk/debug/app-debug.apk
```

The build also copies `sdk/License.txt` into the APK's assets, so the license the
app shows on first run is the same text as the Linux tools'.

For a small tester group a debug APK is usually enough — it is signed with the
Android debug key, so testers see the usual unknown-developer prompt. If you want
a signed release, provide the key material through `local.properties` (never
committed) or the environment:

```properties
# android/local.properties
tt.keystore=../tester.keystore
tt.keystore.password=...
tt.key.alias=tester
tt.key.password=...
```

or `TT_KEYSTORE`, `TT_KEYSTORE_PASSWORD`, `TT_KEY_ALIAS`, `TT_KEY_PASSWORD`.
`gradle assembleRelease` then produces a signed APK.

Install on a tester's device:

```bash
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

### Size

The TeamTalk JNI library is ~69 MB per ABI and carries a lot of debug data, so
`packaging { jniLibs { useLegacyPackaging = true } }` keeps it **compressed** in
the APK (about 27 MB instead of 69 MB) at the cost of extracting it during
install. One ABI plus the Compose runtime gives an APK of roughly 42 MB.

## First run

1. The TeamTalk 5 SDK license is shown once; choose **I accept** to persist the
   decision (the equivalent of the desktop `.tt-sdk-license-accepted` marker).
   Declining keeps runs disabled. It can be withdrawn again in the admin panel.
2. Open the **admin panel** from the bottom of the Tools page and set the
   administrator name and password.
3. In the same panel, set the target server (host, ports, optionally an account)
   and add that host to the **allowlist**.
4. Go back to Tools, pick a test, review the parameters, confirm, and **Run**.

Runs execute on a foreground service, so the connections survive the app going
to the background; a notification shows the running test with a **Stop** action.

## Safety gates (same as the desktop suite)

- **Exact-host allowlist** — the suite, idle bots and ramp test refuse any host
  that is not on the allowlist, and the allowlist is only editable by the
  administrator.
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

## What is verified, and what is not

- **Compiled and assembled.** The APK in the GitHub release was built from this
  source with JDK 17.0.20, Gradle 8.9, Android SDK platform 35 / build-tools
  35.0.0, and the TeamTalk SDK 5.22a — the same versions the workflow-less build
  above uses. `bun run android-app-check` guards the wiring that a compiler would
  not catch: the SDK paths and packaged ABIs, the credential (stretched, salted,
  constant-time, no plaintext), the panel actually gating the allowlist, the
  Tools page ordering, the tool registry's gates, and every Kotlin file's
  balanced delimiters.
- **Not run on hardware.** No device or emulator was available where this was
  built, so the first launch on a tester's phone is still the first launch ever.
  Treat it as a bring-up: expect SDK-path or ABI surprises rather than design
  surprises.
- `minSdk` is 24 (PBKDF2-HMAC-SHA256 needs 26; the app falls back to SHA1 on
  24/25 automatically). Only the ABIs you copy `.so` files for can run.

## Layout

```text
android/app/src/main/java/com/teamtalk/annoying/
  core/      SDK loading, license gate, config store, admin credential, log bus, TeamTalkSession
  tools/     one file per desktop tool + the tool registry and executor
  run/       ToolRunManager (the single running tool)
  service/   RunService (foreground service keeping the run alive)
  ui/        Compose theme, nav, components, screens (tools list, tool detail, log, about, admin panel)
```

## Credits

The desktop tools this ports credited **blindelectron**, **RD-Productions**,
**Simpter** and **Patrick Wilson**. TeamTalk and the TeamTalk SDK are by
**BearWare.dk**.
