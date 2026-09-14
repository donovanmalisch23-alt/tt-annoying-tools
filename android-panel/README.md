# Webby Panel — Android wrapper

A small Kotlin app that puts the [web panel](../README.md#web-panel-pwa) on an
Android device. It serves the built panel from inside the app over
`http://127.0.0.1:8788` and opens it in a `WebView`.

That loopback origin is the entire point. The panel is a PWA, and a PWA needs a
real HTTP origin: a `file://` or `data:` page gets no service worker (so no
offline shell), no manifest, and `localStorage` that does not persist. Serving
the bundle over loopback gives it all three, so the app *is* the PWA shell.

This is unrelated to [`../android/`](../android/README.md), which is the native
TeamTalk SDK port. This wrapper needs no TeamTalk SDK and always builds.

## What works on the device

| | |
| --- | --- |
| Simulated mode | **Fully offline.** All eight tools and the in-tab server model are inside the APK. |
| Live mode | Needs a `webby` bridge on a desktop, reached over the local network — see below. |
| Admin panel | Works against that bridge: sign in and edit its allowlist file from the phone. |

`webby/` is Python and cannot run on Android, so the phone cannot start a
bridge itself. Point it at one on your network instead:

```bash
# On the desktop, listening on the LAN rather than loopback only:
./run_webby.sh start --host 0.0.0.0
```

Then in the app tap **Bridge**, enter `http://<desktop-ip>:8787`, and the panel
comes up in live mode. The bridge answers any origin, so no extra configuration
is needed. Leave the field empty to stay on the simulator.

## Building

The APK ships the *built* panel, so the assets step runs first:

```bash
bun install
bun run android:assets    # vite build, then copy dist/ -> app/src/main/assets/panel/
cd android-panel && gradle assembleDebug
```

`bun run android:apk` does both in one go. `gradle assembleDebug` needs a JDK 17
and an Android SDK with platform 35 (`ANDROID_HOME`, or `sdk.dir` in a
`local.properties`). Android Studio can open **this** directory directly — open
`android-panel/`, not the repository root, because the root has no Gradle build
and the sibling `android/` is a separate one.

The assets directory is generated and git-ignored apart from its `.gitkeep`; a
build that skips `bun run android:assets` will make an APK whose page says so.

## Releasing

`.github/workflows/android-panel-release.yml` builds and publishes on a runner:
Bun installs, the panel builds, the assets are copied, Gradle assembles, and the
APK is attached to a GitHub release. Start it from the Actions tab (**Run
workflow**) or push a tag:

```bash
git tag panel-v0.1.0-alpha-soft && git push origin panel-v0.1.0-alpha-soft
```

Unsigned, it is a **debug-signed** APK — fine for sideloading, with the usual
unknown-developer warning. To get a release-signed APK, add these repository
secrets:

| Secret | |
| --- | --- |
| `ANDROID_KEYSTORE_BASE64` | `base64 -w0 release.jks` |
| `ANDROID_KEYSTORE_PASSWORD` | keystore password |
| `ANDROID_KEY_ALIAS` | key alias |
| `ANDROID_KEY_PASSWORD` | key password |

Locally, the same values work through `android-panel/local.properties`
(`panel.keystore`, `panel.keystore.password`, `panel.key.alias`,
`panel.key.password`); the keystore path is resolved relative to `android-panel/`.
Nothing is signed unless you supply a keystore.

## How it is put together

| File | Job |
| --- | --- |
| `MainActivity.kt` | Starts the server, hosts the WebView, owns the header actions. |
| `server/PanelServer.kt` | A loopback-only static server for `assets/panel/`. Pure framework classes, no dependencies. |
| `server/Mime.kt` | Content types for what a Vite build emits. |
| `BootScript.kt` | The seed script injected into `index.html` before the panel runs. |
| `PanelPrefs.kt` | The one setting the wrapper owns: the bridge address. |

Two details worth knowing:

- **Why the wrapper can seed the panel's settings.** The panel keeps its state in
  `localStorage` under `tt-web.bridge.v1`. Serving `index.html` is the app's own
  job, so `PanelServer` injects a `<script src="/_webby-boot.js">` before the
  bundle and writes `{mode: "live", base: <bridge address>}` when an address is
  configured. It applies the address once and then leaves the panel alone (it
  records what it applied), so switching the panel back to simulated mode
  sticks; changing the address in the app, or **Reset panel data**, re-applies it.
- **Why the port is 8788.** `localStorage` is per-origin, and the port is part of
  the origin — a random port would hand the panel a blank slate on every launch.
  The server binds the first free port in `8788..8807` and fails loudly rather
  than silently changing the page's identity. 8788 keeps it clear of the bridge's
  8787.

`android:usesCleartextTraffic` is on because the panel's own origin is plain
http and live mode may reach a bridge over plain http on the LAN.

## Limits

- **Built and assembled once, for real.** JDK 17 (Temurin 17.0.20), Gradle 8.9,
  AGP 8.7.2, Kotlin 2.0.20 and build-tools 35.0.0 produced the APK on the
  `panel-v0.1.0-alpha-soft` release. The first build found two compile errors —
  a trailing lambda binding to the constructor's `ports` parameter instead of
  `bootScript`, and `readAsset` handing a `ByteArray?` to the `index.html` text
  path. Both are fixed, and the workflow runs the same steps on a runner, so a
  repeat build should be uneventful.
- The app talks to a `webby` bridge, never to a TeamTalk server. Everything the
  panel can do on a real server goes through the bridge.
- Android 8.0 (API 26) is the floor: the icon is an adaptive icon only, and
  older WebViews cannot run the panel's ES2020 bundle.
- Only one bridge at a time, as everywhere else, and the app is a wrapper — the
  panel inside it is the same code the browser gets.

## Checking it without a device

```bash
bun run android-check
```

That resolves every `R.*` reference against the declared resources, every
`@type/name` reference in the layouts and the manifest, every `BuildConfig.*`
field and every version-catalog alias; balances the delimiters in each Kotlin
file; confirms the manifest's activity exists at the right package; and checks
the seams where the wrapper and the panel have to agree (the `localStorage` key,
the port range, the asset directory, the theme colour). It is not a substitute
for a compiler, but it catches the renames and typos that would otherwise only
show up in CI.
