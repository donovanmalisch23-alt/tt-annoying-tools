/**
 * Structural check for the native Android app in `android/`.
 *
 * The app is compiled with a local TeamTalk SDK and a Gradle/Android toolchain
 * that is not part of this repository, so this file guards the things a compiler
 * would not catch anyway — the seams of the design and the claims the app makes:
 *
 *  - the SDK is wired up (jar, per-ABI libraries, packaged ABIs) and never
 *    committed, because its license does not allow redistribution here;
 *  - the admin credential is stretched, salted and compared in constant time,
 *    and only a hash reaches storage;
 *  - the allowlist, the target and the license are reachable only through the
 *    admin panel, which is the point of the gate;
 *  - the Tools page says "Select a tool below", lists every registered tool and
 *    puts the admin panel button under that list;
 *  - every Kotlin file still balances its delimiters, and no screen still points
 *    at the tabs that were replaced by the panel.
 *
 *   bun scripts/android-app-check.ts
 *
 * Exit code 1 means at least one check failed.
 */
import { execFileSync } from "node:child_process";
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MODULE = path.join(ROOT, "android");
const APP = path.join(MODULE, "app");
const MAIN = path.join(APP, "src", "main");
const SOURCE = path.join(MAIN, "java", "com", "teamtalk", "annoying");
const CORE = path.join(SOURCE, "core");
const TOOLS = path.join(SOURCE, "tools");
const UI = path.join(SOURCE, "ui");
const SCREENS = path.join(UI, "screens");
const GRADLE = path.join(APP, "build.gradle.kts");
const MANIFEST = path.join(MAIN, "AndroidManifest.xml");
const APK = path.join(APP, "build", "outputs", "apk", "debug", "app-debug.apk");

let failures = 0;

function check(label: string, condition: boolean, detail = ""): void {
  if (condition) {
    console.log(`  PASS  ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

function skip(label: string, why: string): void {
  console.log(`  SKIP  ${label} — ${why}`);
}

function read(file: string): string {
  return readFileSync(file, "utf8");
}

function walk(dir: string): string[] {
  if (!existsSync(dir)) return [];
  const found: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) found.push(...walk(full));
    else if (entry.isFile()) found.push(full);
  }
  return found;
}

/**
 * Count the delimiters a Kotlin file must balance, ignoring comments, string
 * literals and char literals — a truncated file or a stray brace is the failure
 * mode this catches, and these files are full of braces inside strings.
 */
function balance(text: string): { braces: number; parens: number; brackets: number } {
  let braces = 0;
  let parens = 0;
  let brackets = 0;
  let index = 0;

  while (index < text.length) {
    const ch = text[index];
    const next = index + 1 < text.length ? text[index + 1] : "";

    if (ch === "/" && next === "/") {
      while (index < text.length && text[index] !== "\n") index += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      index += 2;
      while (index < text.length && !(text[index] === "*" && text[index + 1] === "/")) index += 1;
      index += 2;
      continue;
    }
    if (ch === '"') {
      if (text.startsWith('"""', index)) {
        index += 3;
        while (index < text.length && !text.startsWith('"""', index)) index += 1;
        index += 3;
        continue;
      }
      index += 1;
      while (index < text.length && text[index] !== '"') {
        if (text[index] === "\\") index += 1;
        index += 1;
      }
      index += 1;
      continue;
    }
    if (ch === "'") {
      index += 1;
      while (index < text.length && text[index] !== "'") {
        if (text[index] === "\\") index += 1;
        index += 1;
      }
      index += 1;
      continue;
    }
    if (ch === "{") braces += 1;
    else if (ch === "}") braces -= 1;
    else if (ch === "(") parens += 1;
    else if (ch === ")") parens -= 1;
    else if (ch === "[") brackets += 1;
    else if (ch === "]") brackets -= 1;
    index += 1;
  }

  return { braces, parens, brackets };
}

/** Entry names (and sizes) from a zip's central directory, so no unzip is needed. */
function zipEntries(file: string): { name: string; size: number; packed: number }[] {
  const buffer = readFileSync(file);
  let eocd = -1;
  for (let i = buffer.length - 22; i >= 0 && i > buffer.length - 66_000; i -= 1) {
    if (buffer.readUInt32LE(i) === 0x06054b50) {
      eocd = i;
      break;
    }
  }
  if (eocd < 0) return [];

  const count = buffer.readUInt16LE(eocd + 10);
  let offset = buffer.readUInt32LE(eocd + 16);
  const entries: { name: string; size: number; packed: number }[] = [];

  for (let i = 0; i < count; i += 1) {
    if (buffer.readUInt32LE(offset) !== 0x02014b50) break;
    const packed = buffer.readUInt32LE(offset + 20);
    const size = buffer.readUInt32LE(offset + 24);
    const nameLength = buffer.readUInt16LE(offset + 28);
    const extraLength = buffer.readUInt16LE(offset + 30);
    const commentLength = buffer.readUInt16LE(offset + 32);
    const name = buffer.toString("utf8", offset + 46, offset + 46 + nameLength);
    entries.push({ name, size, packed });
    offset += 46 + nameLength + extraLength + commentLength;
  }
  return entries;
}

const gradle = read(GRADLE);

console.log("the local TeamTalk SDK is wired up");
check(
  "the Java bindings are picked up from app/libs",
  /fileTree\(mapOf\("dir" to "libs"[\s\S]{0,40}"\*\.jar"/.test(gradle),
);
check(
  "the per-ABI native libraries are declared",
  gradle.includes('jniLibs.srcDirs("src/main/jniLibs")'),
);
check("the packaged ABIs are restricted", /ndk\s*\{[\s\S]{0,200}abiFilters/.test(gradle));
check(
  "the native libraries stay compressed in the APK",
  gradle.includes("useLegacyPackaging = true"),
);
check("the app still builds with Compose", gradle.includes("compose = true"));
check("the release channel is still surfaced", gradle.includes("RELEASE_CHANNEL"));
check("tester signing still comes from local.properties", gradle.includes("signingConfigs"));

console.log();
console.log("the SDK itself is never committed");
const ignore = read(path.join(ROOT, ".gitignore"));
check("the bindings jar is ignored", ignore.includes("android/app/libs/*.jar"));
check("the native libraries are ignored", ignore.includes("android/app/src/main/jniLibs/**"));
let tracked: string[] | null = null;
try {
  tracked = execFileSync("git", ["ls-files", "android"], { cwd: ROOT, encoding: "utf8" })
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
} catch {
  tracked = null;
}
if (tracked === null) {
  skip("no SDK binary is tracked by git", "git is not available here");
} else {
  const binaries = tracked.filter((file) => /\.(jar|so|aar)$/.test(file));
  check("no SDK binary is tracked by git", binaries.length === 0, binaries.join(", "));
}

console.log();
console.log("everything runs on the device");
const sdk = read(path.join(CORE, "Sdk.kt"));
check("the loader still funnels through ensureLoaded", sdk.includes("fun ensureLoaded()"));
check("the UI can report the on-device SDK", sdk.includes("fun statusLine()"));
check(
  "no source spawns a helper process",
  !/ProcessBuilder|\bRuntime\.getRuntime\(\)\.exec/.test(walk(SOURCE).map(read).join("\n")),
);
check(
  "no source calls the desktop bridge",
  !/\b8787\b|\/api\/(health|tools|runs|whitelist|admin|events)/.test(walk(SOURCE).map(read).join("\n")),
);

console.log();
console.log("the admin credential");
const auth = read(path.join(CORE, "AdminAuth.kt"));
check("the password is stretched with PBKDF2", auth.includes("PBKDF2WithHmacSHA256"));
check("the salt is random per install", auth.includes("SecureRandom"));
check("hashes are compared in constant time", auth.includes("MessageDigest.isEqual"));
check("short passwords are refused", /MIN_PASSWORD = \d+/.test(auth));
check("repeated failures lock the panel", auth.includes("MAX_ATTEMPTS") && auth.includes("LOCK_WINDOW_MS"));
check("the password never leaves the process", !auth.includes("putString(KEY_ADMIN_PASSWORD"));

const store = read(path.join(CORE, "ConfigStore.kt"));
check(
  "only a hash and a salt reach storage",
  store.includes("KEY_ADMIN_HASH") && store.includes("KEY_ADMIN_SALT"),
);
check("no plaintext password field exists", !/admin_?password/i.test(store));
check("the allowlist can be reset from the panel", store.includes("fun resetWhitelist"));

const viewModel = read(path.join(UI, "AppViewModel.kt"));
check("the panel starts locked", /var adminUnlocked by mutableStateOf\(false\)/.test(viewModel));
check("provisioning unlocks it", viewModel.includes("adminProvision"));
check("signing in unlocks it", viewModel.includes("fun adminSignIn"));
check("locking clears the signed-in name", /fun adminLock\(\)[\s\S]{0,120}adminUnlocked = false/.test(viewModel));
check("hosts can be added one at a time", viewModel.includes("fun addHost"));
check("hosts can be removed again", viewModel.includes("fun removeHost"));

console.log();
console.log("the admin panel gates what it should");
const admin = read(path.join(SCREENS, "AdminScreen.kt"));
check("the panel is driven by the unlocked state", admin.includes("viewModel.adminUnlocked"));
check("first run asks for a new credential", admin.includes("adminProvision"));
check("returning admins sign in", admin.includes("adminSignIn"));
check("it holds the target server section", admin.includes("AdminServerSection(viewModel)"));
check("it holds the allowlist section", admin.includes("AdminAllowlistSection(viewModel)"));
check("it holds the SDK license decision", admin.includes("acceptLicense()"));
check("it can lock itself again", admin.includes("adminLock()"));
check("it can forget the credential", admin.includes("adminForgetCredential()"));

const allowlist = read(path.join(SCREENS, "AdminAllowlistSection.kt"));
check("the allowlist editor adds hosts", allowlist.includes("viewModel.addHost("));
check("the allowlist editor removes hosts", allowlist.includes("viewModel.removeHost("));
check("the allowlist editor keeps a raw mode", allowlist.includes("Edit as text"));

console.log();
console.log("the Tools page leads with the list");
const home = read(path.join(SCREENS, "HomeScreen.kt"));
check("it says what the page is for", home.includes('"Select a tool below"'));
check("it lists every registered tool", home.includes("ToolRegistry.specs.filter"));
const panelIndex = home.indexOf("PanelButton(");
const listIndex = home.indexOf("heavy.forEach");
check("the admin button sits below the tool list", panelIndex > 0 && panelIndex > listIndex);
check("the admin button opens the panel", home.includes("navigate(ROUTE_ADMIN)"));
check("the page reports the on-device SDK", home.includes("Sdk.statusLine()"));

const root = read(path.join(UI, "AppRoot.kt"));
check("the admin route is registered", root.includes("composable(ROUTE_ADMIN)"));
check("tool routes are still registered", root.includes('"$ROUTE_TOOL_PREFIX{id}"'));
const destBlock = root.match(/enum class Dest\([\s\S]*?\n\}/)?.[0] ?? "";
const destinations = (destBlock.match(/^ {4}\w+\("/gm) ?? []).length;
check("the bar is Tools, Log and About", destinations === 3, `${destinations} destinations`);
check("the admin route is exported for the page", root.includes("const val ROUTE_ADMIN"));

console.log();
console.log("nothing was orphaned by the move");
const kotlin = walk(SOURCE).filter((file) => file.endsWith(".kt"));
const all = kotlin.map(read).join("\n");
check("the old connection screen is gone", !all.includes("fun ConnectionScreen("));
check("the old allowlist screen is gone", !all.includes("fun WhitelistScreen("));
check(
  "no screen points at the tabs that were replaced",
  !/Allowlist tab|Server tab|Tools tab/.test(all),
);
check("the old screens are not on disk", !existsSync(path.join(SCREENS, "ConnectionScreen.kt")) && !existsSync(path.join(SCREENS, "WhitelistScreen.kt")));
const unbalanced = kotlin.filter((file) => {
  const counts = balance(read(file));
  return counts.braces !== 0 || counts.parens !== 0 || counts.brackets !== 0;
});
check(
  `every Kotlin file balances its delimiters (${kotlin.length} files)`,
  unbalanced.length === 0,
  unbalanced.map((file) => path.relative(ROOT, file)).join(", "),
);

const manifest = read(MANIFEST);
check("the launcher activity is still declared", manifest.includes(".ui.MainActivity"));
check("the manifest keeps the INTERNET permission", manifest.includes("android.permission.INTERNET"));

console.log();
console.log("the tools still gate themselves");
const registry = read(path.join(TOOLS, "ToolSpec.kt"));
const ids = [
  "MESSAGE_SPAMMER",
  "LOGIN_SPAMMER",
  "LEAVE_JOIN",
  "IDLE_BOTS",
  "RESPONSE_BOT",
  "SUITE",
  "LOIC",
  "RAMP",
];
check("all eight tools are declared", ids.every((id) => registry.includes(`const val ${id} =`)));
check(
  "the allowlist-gated tools still declare it",
  (registry.match(/requiresWhitelist = true/g) ?? []).length >= 3,
);
check(
  "the heavy tools still ask for confirmation",
  (registry.match(/requiresConfirm = true/g) ?? []).length >= 4,
);
check(
  "the local flood test still refuses off-device targets",
  registry.includes("Off-device targets are refused"),
);

console.log();
if (existsSync(APK)) {
  const entries = zipEntries(APK);
  const name = (entry: { name: string }): boolean => /^lib\/[^/]+\/libTeamTalk5-jni\.so$/.test(entry.name);
  const library = entries.find(name);
  check("the built APK carries the SDK library", library !== undefined);
  check(
    "only the declared ABIs are packaged",
    new Set(
      entries
        .filter((entry) => /^lib\//.test(entry.name))
        .map((entry) => entry.name.split("/")[1]),
    ).size <= 2,
  );
  if (library) {
    check(
      "the library is compressed in the APK",
      library.packed > 0 && library.packed < library.size,
      `${library.size} -> ${library.packed}`,
    );
  }
  check(
    "the SDK license text ships with the app",
    entries.some((entry) => entry.name === "assets/teamtalk-sdk-license.txt"),
  );
} else {
  skip("the built APK carries the SDK", "run the Gradle build first");
}

console.log();
if (failures > 0) {
  console.log(`  ${failures} failing android app check(s)`);
  process.exit(1);
}
console.log("  the android app is wired up");
process.exit(0);
