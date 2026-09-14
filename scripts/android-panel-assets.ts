/**
 * Copy the built web panel into the Android wrapper's assets.
 *
 * The wrapper in `android-panel/` is a shell: it serves a loopback-only copy of
 * this panel (`dist/`) and opens it in a WebView. That copy has to be generated,
 * because the bundle is a build artifact — so this script is the single place
 * the two halves are joined:
 *
 *   bun run android:assets   # vite build, then this
 *
 * It clears everything except `.gitkeep`, so a stale bundle can never linger in
 * the APK, and it refuses to run against a missing or empty `dist/`.
 */
import { existsSync } from "node:fs";
import { cp, mkdir, readdir, rm, stat } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, "..");
const DIST = path.join(ROOT, "dist");
const TARGET = path.join(ROOT, "android-panel", "app", "src", "main", "assets", "panel");

/** Kept in place so the assets directory exists in a fresh checkout. */
const KEEP = ".gitkeep";

/** Bytes on disk under `dir`, recursively. */
async function totalBytes(dir: string): Promise<number> {
  let sum = 0;
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) sum += await totalBytes(full);
    else if (entry.isFile()) sum += (await stat(full)).size;
  }
  return sum;
}

function human(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / (1024 * 1024)).toFixed(2)} MiB`;
}

async function main(): Promise<void> {
  if (!existsSync(path.join(DIST, "index.html"))) {
    throw new Error(
      `no built panel at ${DIST} — run \`bun run build\` first ` +
        "(or just `bun run android:assets`, which does it for you)",
    );
  }

  await mkdir(TARGET, { recursive: true });
  for (const entry of await readdir(TARGET)) {
    if (entry === KEEP) continue;
    await rm(path.join(TARGET, entry), { recursive: true, force: true });
  }

  await cp(DIST, TARGET, { recursive: true });

  const entries = await readdir(TARGET);
  const bytes = await totalBytes(TARGET);
  console.log(
    `panel assets -> ${path.relative(ROOT, TARGET)} ` +
      `(${entries.length} top-level entries, ${human(bytes)})`,
  );
  console.log(
    "the wrapper serves these from http://127.0.0.1:8788/ inside the app; " +
      "build the APK with the \"Android panel APK\" workflow or `gradle -p android-panel assembleDebug`",
  );
}

main().catch((error: unknown) => {
  console.error(`android-panel-assets: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
});
