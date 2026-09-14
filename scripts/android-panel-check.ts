/**
 * Structural check for the Android wrapper in `android-panel/`.
 *
 * There is no JDK, Gradle or Android SDK in this workspace, so the wrapper
 * cannot be compiled here — the same gap the sibling `android/` module has. The
 * next best thing is to check what a compiler would catch first, and what a
 * rename would silently break:
 *
 *  - every `R.<type>.<name>` reference resolving to a declared resource;
 *  - every `@type/name` reference inside the layouts and the manifest;
 *  - every `BuildConfig.<field>` reference being declared;
 *  - the launcher activity named in the manifest existing, in the right package;
 *  - every version-catalog alias existing;
 *  - the wrapper and the panel agreeing on the storage key, the port range, the
 *    asset directory and the theme colour, since those are the seams.
 *
 *   bun scripts/android-panel-check.ts
 *
 * Exit code 1 means at least one check failed.
 */
import { existsSync, readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const MODULE = path.join(ROOT, "android-panel");
const APP = path.join(MODULE, "app");
const MAIN = path.join(APP, "src", "main");
const JAVA = path.join(MAIN, "java");
const RES = path.join(MAIN, "res");
const MANIFEST = path.join(MAIN, "AndroidManifest.xml");
const ASSETS = path.join(MAIN, "assets", "panel");
const SOURCE = path.join(JAVA, "com", "teamtalk", "webbypanel");

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

function read(file: string): string {
  return readFileSync(file, "utf8");
}

function relative(file: string): string {
  return path.relative(ROOT, file);
}

/**
 * Count the delimiters a Kotlin file must balance, ignoring comments, string
 * literals and char literals — a truncated file or a stray brace is the failure
 * mode this is here to catch, and the strings are full of braces and slashes.
 */
function balance(text: string): { braces: number; parens: number; brackets: number } {
  let braces = 0;
  let parens = 0;
  let brackets = 0;
  let index = 0;
  const end = text.length;

  while (index < end) {
    const ch = text[index];
    const next = index + 1 < end ? text[index + 1] : "";

    if (ch === "/" && next === "/") {
      while (index < end && text[index] !== "\n") index += 1;
      continue;
    }
    if (ch === "/" && next === "*") {
      index += 2;
      while (index < end && !(text[index] === "*" && text[index + 1] === "/")) index += 1;
      index += 2;
      continue;
    }
    if (ch === '"' && next === '"' && text[index + 2] === '"') {
      index += 3;
      while (index < end && !(text[index] === '"' && text[index + 1] === '"' && text[index + 2] === '"')) {
        index += 1;
      }
      index += 3;
      continue;
    }
    if (ch === '"') {
      index += 1;
      while (index < end && text[index] !== '"') {
        if (text[index] === "\\") index += 1;
        index += 1;
      }
      index += 1;
      continue;
    }
    if (ch === "'") {
      index += 1;
      while (index < end && text[index] !== "'") {
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

// ----- the resource table ---------------------------------------------------- //

const resFiles = walk(RES);
const declared = new Map<string, Set<string>>();

// Not named `declare`: Bun's TypeScript runtime reads that name as an ambient
// declaration and silently drops the body.
function addResource(type: string, name: string): void {
  const set = declared.get(type) ?? new Set<string>();
  set.add(name);
  declared.set(type, set);
}

/** Resource types that are named by their file, e.g. `layout/activity_main.xml`. */
const FILE_TYPES = new Set([
  "layout",
  "drawable",
  "mipmap",
  "menu",
  "xml",
  "raw",
  "font",
  "anim",
  "color",
]);

for (const file of resFiles) {
  if (file.includes(`${path.sep}values`)) {
    for (const match of read(file).matchAll(/<([a-z][a-z0-9_]*) name="([^"]+)"/g)) {
      addResource(match[1], match[2]);
    }
  }
  const folder = path.basename(path.dirname(file)).split("-")[0];
  if (FILE_TYPES.has(folder)) addResource(folder, path.basename(file, path.extname(file)));
  if (folder === "layout") {
    for (const match of read(file).matchAll(/@\+id\/([A-Za-z0-9_]+)/g)) addResource("id", match[1]);
  }
}

// ----- sources --------------------------------------------------------------- //

const kotlinFiles = walk(JAVA).filter((file) => file.endsWith(".kt"));
const appBuild = read(path.join(APP, "build.gradle.kts"));
const manifest = existsSync(MANIFEST) ? read(MANIFEST) : "";

console.log("source layout");
check("the wrapper has Kotlin sources", kotlinFiles.length > 0, String(kotlinFiles.length));
check("the manifest is present", manifest.length > 0, MANIFEST);

const packageProblems: string[] = [];
for (const file of kotlinFiles) {
  const expected = path.dirname(path.relative(JAVA, file)).split(path.sep).join(".");
  const declaredPackage = /^\s*package\s+([A-Za-z0-9_.]+)/m.exec(read(file))?.[1] ?? "";
  if (declaredPackage !== expected) {
    packageProblems.push(`${path.relative(JAVA, file)}: '${declaredPackage || "none"}' should be '${expected}'`);
  }
}
check("every file's package matches its directory", packageProblems.length === 0, packageProblems.join("; "));

const unbalanced: string[] = [];
for (const file of kotlinFiles) {
  const { braces, parens, brackets } = balance(read(file));
  if (braces !== 0 || parens !== 0 || brackets !== 0) {
    unbalanced.push(`${path.basename(file)}: {${braces}} (${parens}) [${brackets}]`);
  }
}
check("every file is delimiter-balanced", unbalanced.length === 0, unbalanced.join("; "));

// ----- references ------------------------------------------------------------ //

const unresolvedResources: string[] = [];
for (const file of kotlinFiles) {
  // The lookbehind keeps `android.R.string.ok` out of it: that is the framework's
  // resource table, not ours.
  for (const match of read(file).matchAll(/(?<![\w.])R\.([a-z_]+)\.([A-Za-z0-9_]+)/g)) {
    if (!declared.get(match[1])?.has(match[2])) {
      unresolvedResources.push(`${path.basename(file)}: R.${match[1]}.${match[2]}`);
    }
  }
}
check(
  "every R.* reference resolves to a declared resource",
  unresolvedResources.length === 0,
  unresolvedResources.join("; "),
);

const unresolvedXml: string[] = [];
for (const file of [...resFiles, MANIFEST].filter((file) => existsSync(file))) {
  for (const match of read(file).matchAll(/@(string|style|color|drawable|mipmap|layout|id)\/([A-Za-z0-9_.]+)/g)) {
    if (!declared.get(match[1])?.has(match[2])) {
      unresolvedXml.push(`${relative(file)}: @${match[1]}/${match[2]}`);
    }
  }
}
check(
  "every @type/name reference in the resources resolves",
  unresolvedXml.length === 0,
  unresolvedXml.join("; "),
);

const buildFields = new Set([
  "DEBUG",
  "VERSION_NAME",
  "VERSION_CODE",
  "APPLICATION_ID",
  "BUILD_TYPE",
  "FLAVOR",
]);
for (const match of appBuild.matchAll(/buildConfigField\("String",\s*"([A-Za-z0-9_]+)"/g)) {
  buildFields.add(match[1]);
}
const unresolvedBuildConfig: string[] = [];
for (const file of kotlinFiles) {
  for (const match of read(file).matchAll(/\bBuildConfig\.([A-Za-z0-9_]+)/g)) {
    if (!buildFields.has(match[1])) {
      unresolvedBuildConfig.push(`${path.basename(file)}: BuildConfig.${match[1]}`);
    }
  }
}
check(
  "every BuildConfig.* reference is declared",
  unresolvedBuildConfig.length === 0,
  unresolvedBuildConfig.join("; "),
);

const catalog = read(path.join(MODULE, "gradle", "libs.versions.toml"));
const aliases = new Set(
  Array.from(catalog.matchAll(/^([a-z0-9-]+)\s*=\s*\{[^}]*id\s*=/gm)).map((match) => match[1]),
);
const usedAliases: string[] = [];
for (const file of walk(MODULE).filter((file) => file.endsWith(".gradle.kts"))) {
  // `libs.plugins.android.application` is the accessor for the catalog's
  // `android-application` key, so dots become dashes before comparing.
  for (const match of read(file).matchAll(/alias\(libs\.plugins\.([a-z0-9.-]+)\)/g)) {
    usedAliases.push(match[1].replace(/\./g, "-"));
  }
}
check("Gradle uses at least one plugin alias", usedAliases.length > 0, String(usedAliases.length));
check(
  "every plugin alias exists in the version catalog",
  usedAliases.every((alias) => aliases.has(alias)),
  usedAliases.filter((alias) => !aliases.has(alias)).join(", "),
);

const namespace = /namespace\s*=\s*"([^"]+)"/.exec(appBuild)?.[1] ?? "";
const activity = /android:name="\.([A-Za-z0-9_.]+)"/.exec(manifest)?.[1] ?? "";
const activityFile = path.join(JAVA, namespace.split(".").join(path.sep), `${activity}.kt`);
check(
  "the manifest's launcher activity exists on disk",
  namespace !== "" && activity !== "" && existsSync(activityFile),
  `${namespace}.${activity} -> ${relative(activityFile)}`,
);
check(
  "the manifest declares an INTERNET permission",
  manifest.includes("android.permission.INTERNET"),
);
check(
  "cleartext is allowed, so the loopback origin can load",
  manifest.includes('android:usesCleartextTraffic="true"'),
);

console.log();
console.log("the wrapper and the panel agree");

const serverKt = read(path.join(SOURCE, "server", "PanelServer.kt"));
const bootKt = read(path.join(SOURCE, "BootScript.kt"));
const activityKt = read(path.join(SOURCE, "MainActivity.kt"));
const storeTs = read(path.join(ROOT, "src", "app", "live", "store.ts"));
const apiTs = read(path.join(ROOT, "src", "app", "live", "api.ts"));
const copyTs = read(path.join(ROOT, "scripts", "android-panel-assets.ts"));

const panelKey = /const STORAGE_KEY = "([^"]+)"/.exec(storeTs)?.[1] ?? "";
check(
  "the boot script writes the storage key the panel reads",
  panelKey !== "" && bootKt.includes(`"${panelKey}"`),
  `panel key '${panelKey}'`,
);
check("the boot script clears its marker when no address is set", bootKt.includes("removeItem(S)"));
check(
  "the boot script leaves an existing session alone",
  bootKt.includes("JSON.parse") && bootKt.includes("c.mode="),
);

const bridgePort = Number(/DEFAULT_BRIDGE_PORT = (\d+)/.exec(apiTs)?.[1] ?? "0");
const range = /DEFAULT_PORTS = (\d+)\.\.(\d+)/.exec(serverKt);
const firstPort = Number(range?.[1] ?? "0");
const lastPort = Number(range?.[2] ?? "0");
check(
  "the wrapper's port range is above the bridge's port",
  bridgePort > 0 && firstPort > bridgePort && lastPort >= firstPort,
  `bridge ${bridgePort}, wrapper ${firstPort}..${lastPort}`,
);
check(
  "the range is narrow, so the panel's origin stays stable",
  lastPort - firstPort >= 0 && lastPort - firstPort < 32,
  `${lastPort - firstPort + 1} ports`,
);
check("the wrapper serves the panel over loopback", activityKt.includes("http://127.0.0.1:"));

const assetRoot = /const val ASSET_ROOT = "([^"]+)"/.exec(serverKt)?.[1] ?? "";
const copyTarget = /assets", "([a-z]+)"\)/.exec(copyTs)?.[1] ?? "";
check(
  "the copy script writes where the server reads",
  assetRoot !== "" && copyTarget === assetRoot,
  `server '${assetRoot}', copy script '${copyTarget}'`,
);
check(
  "the wrapper page and the res colour are the same",
  activityKt.includes("#0B1220") &&
    /panel_background">#0B1220</.test(read(path.join(RES, "values", "colors.xml"))),
);
check(
  "the theme is a framework theme, so no extra dependency is needed",
  read(path.join(RES, "values", "themes.xml")).includes('parent="@android:style/'),
);
check("the app module pulls in no external dependency", !/^\s*(implementation|api)\(/m.test(appBuild));

const minSdk = Number(/minSdk\s*=\s*(\d+)/.exec(appBuild)?.[1] ?? "0");
check("minSdk allows an adaptive-icon-only launcher", minSdk >= 26, String(minSdk));
check("compileSdk is set", /compileSdk\s*=\s*(\d+)/.test(appBuild));
check("targetSdk is set", /targetSdk\s*=\s*(\d+)/.test(appBuild));
check(
  "the release channel is surfaced in the wrapper",
  /buildConfigField\("String",\s*"RELEASE_CHANNEL"/.test(appBuild),
);

console.log();
console.log("the build wiring");

const pkg = JSON.parse(read(path.join(ROOT, "package.json"))) as {
  scripts?: Record<string, string>;
};
check(
  "`bun run android:assets` builds the panel and copies it in",
  (pkg.scripts?.["android:assets"] ?? "").includes("android-panel-assets"),
  pkg.scripts?.["android:assets"] ?? "missing",
);

const workflowPath = path.join(ROOT, ".github", "workflows", "android-panel-release.yml");
const workflow = existsSync(workflowPath) ? read(workflowPath) : "";
check("the release workflow exists", workflow.length > 0, relative(workflowPath));
check("it can be run by hand", workflow.includes("workflow_dispatch"));
check("it may write releases", workflow.includes("contents: write"));
check("it builds the panel before assembling", workflow.includes("bun run android:assets"));
check("it assembles with Gradle", /\bgradle\b/.test(workflow) && workflow.includes("assembleDebug"));
check("it publishes a release", workflow.includes("action-gh-release"));
check("the release is marked a prerelease", workflow.includes("prerelease: true"));

if (existsSync(path.join(ASSETS, "index.html"))) {
  const entries = readdirSync(ASSETS);
  check("the bundled panel has a service worker", existsSync(path.join(ASSETS, "sw.js")));
  check("the bundled panel has a manifest", existsSync(path.join(ASSETS, "manifest.webmanifest")));
  check("the bundled panel has its JS bundle", existsSync(path.join(ASSETS, "assets")));
  check("there is more than a placeholder in the assets", entries.length > 1, entries.join(", "));
} else {
  skip("the bundled panel is complete", "run `bun run android:assets` first");
}

console.log();
if (failures > 0) {
  console.log(`  ${failures} failing android wrapper check(s)`);
  process.exit(1);
}
console.log("  the android wrapper is wired up");
process.exit(0);
