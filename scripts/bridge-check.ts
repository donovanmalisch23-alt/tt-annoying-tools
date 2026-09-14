/**
 * Headless check for live mode's frontend half: the bridge client and the store
 * that drives it. `fetch` is stubbed, so this runs with no bridge and no
 * network — it verifies the panel's side of the API contract and the failure
 * paths the UI depends on.
 *
 *   bun scripts/bridge-check.ts
 */
import "./browser-shim";
import { EventEmitter } from "node:events";
import { resolveConfig } from "vite";
import type { ViteDevServer } from "vite";
import { logbus } from "../src/app/core/logbus";
import { webbyBridge } from "./vite-webby";
import type { SpawnOptions, SpawnedChild } from "./vite-webby";
import {
  BridgeClient,
  BridgeError,
  BridgeUnreachableError,
  bridgeCandidates,
  inferLevel,
} from "../src/app/live/api";
import { liveStore } from "../src/app/live/store";

let failures = 0;

function check(label: string, condition: boolean, detail = ""): void {
  if (condition) {
    console.log(`  PASS  ${label}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${label}${detail ? ` — ${detail}` : ""}`);
  }
}

const HEALTH = {
  ok: true,
  service: "webby",
  version: "0.1.0",
  mode: "live",
  time: 1,
  config: {
    repo_root: "/repo",
    host: "127.0.0.1",
    port: 8787,
    whitelist_path: "/repo/whitelist.txt",
    admin_path: "/repo/.webby/admin.json",
    state_dir: "/repo/.webby",
    python: "/usr/bin/python3",
    accept_sdk_license: false,
    require_admin_for_runs: false,
    max_run_seconds: 0,
    dist_dir: "/repo/dist",
    dist_built: true,
    sdk_python_present: true,
    sdk_library_present: true,
    sdk_marker_present: false,
    allow_origins: ["*"],
  },
  whitelist: { path: "/repo/whitelist.txt", entries: ["127.0.0.1"], count: 1, exists: true, mtime: 7 },
  admin: { configured: true, username: "admin", source: "file" },
  run: null,
  tools: ["message-sender", "login-cycles"],
};

const TOOLS = {
  tools: [
    {
      id: "login-cycles",
      label: "Login / logout cycles",
      script: "tt_spammer.py",
      requires_confirm: false,
      requires_whitelist: false,
      local_only: false,
      accepts_connection: true,
      ignored: [],
      note: "",
    },
  ],
};

/** A fetch that answers from a routing table, or throws to simulate a dead port. */
function stubFetch(
  routes: Record<string, unknown>,
  options: { fail?: boolean } = {},
): typeof fetch {
  return (async (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (options.fail) throw new TypeError("fetch failed");
    for (const [path, payload] of Object.entries(routes)) {
      if (url.includes(path)) {
        const status = (payload as { __status?: number }).__status ?? 200;
        return new Response(JSON.stringify(payload), {
          status,
          headers: { "Content-Type": "application/json" },
        });
      }
    }
    return new Response(JSON.stringify({ error: "no such API route" }), { status: 404 });
  }) as typeof fetch;
}

globalThis.fetch = stubFetch({});

console.log("bridge check");

// --- address candidates ----------------------------------------------------- //
const candidates = bridgeCandidates();
check("same origin is tried first", candidates[0] === "", JSON.stringify(candidates));
check(
  "the conventional port is tried",
  candidates.some((candidate) => candidate.endsWith(":8787")),
  JSON.stringify(candidates),
);
check("loopback is tried", candidates.some((candidate) => candidate.includes("127.0.0.1")));

// --- log level inference ---------------------------------------------------- //
check("a traceback is an error", inferLevel("Traceback (most recent call last):") === "error");
check("a refusal is a warning", inferLevel("refused by the server") === "warn");
check("a kick is a warning", inferLevel("kicked from channel") === "warn");
check("success reads as ok", inferLevel("logged in successfully") === "ok");
check("a banner is sys", inferLevel("=== suite started ===") === "sys");
check("plain output is info", inferLevel("sending message 3") === "info");

// --- client ---------------------------------------------------------------- //
async function clientChecks(): Promise<void> {
  globalThis.fetch = stubFetch({ "/api/health": HEALTH });
  const client = new BridgeClient("http://127.0.0.1:8787/");
  const health = await client.health;
  check("health parses", health.service === "webby" && health.whitelist.count === 1);
  check("a trailing slash is normalised", client.endpoint("/api/tools") === "http://127.0.0.1:8787/api/tools");

  globalThis.fetch = stubFetch({
    "/api/whitelist": { error: "editing the allowlist needs an admin session; sign in first", __status: 401 },
  });
  let status = 0;
  let message = "";
  try {
    await client.saveWhitelist("x\n");
  } catch (error) {
    status = error instanceof BridgeError ? error.status : 0;
    message = error instanceof Error ? error.message : "";
  }
  check("a 401 keeps its status", status === 401, String(status));
  check("the bridge's own message is surfaced", message.includes("admin session"), message);

  globalThis.fetch = stubFetch({}, { fail: true });
  let unreachable = false;
  try {
    await client.health;
  } catch (error) {
    unreachable = error instanceof BridgeUnreachableError;
  }
  check("a dead bridge is reported as unreachable, not as an HTTP error", unreachable);
}

// --- store ----------------------------------------------------------------- //
async function storeChecks(): Promise<void> {
  globalThis.fetch = stubFetch({ "/api/health": HEALTH, "/api/tools": TOOLS, "/api/whitelist": HEALTH.whitelist, "/api/runs/current": { run: null } });
  await liveStore.connect("http://127.0.0.1:8787");
  const online = liveStore.getSnapshot();
  check("connecting reaches the online state", online.status === "online", online.status);
  check("the health snapshot is kept", online.health?.version === "0.1.0");
  check("the tool catalog is kept", online.tools.length === 1 && online.tools[0].id === "login-cycles");
  check("the allowlist is kept", online.whitelist?.entries[0] === "127.0.0.1");
  check("runs are allowed once online", liveStore.canRun);

  globalThis.fetch = stubFetch(
    {
      "/api/runs": {
        error: "'evil.example' is not in /repo/whitelist.txt; add it in the admin panel first.",
        __status: 400,
      },
    },
    {},
  );
  let refused = "";
  try {
    await liveStore.startRun("login-cycles", { cycles: "1" }, {
      host: "evil.example",
      tcpPort: 10333,
      udpPort: 10333,
      username: "",
      password: "hunter2",
      nickname: "n",
      clientName: "c",
      encrypted: false,
      channelId: null,
      channelPath: null,
      channelPassword: "",
      commandTimeoutSec: 15,
      kickResistance: true,
      reconnectDelaySec: 3.5,
    }, false);
  } catch (error) {
    refused = error instanceof Error ? error.message : "";
  }
  check("a refused run surfaces the allowlist message", refused.includes("whitelist"), refused);
  check("the panel shows the refusal", (liveStore.getSnapshot().error ?? "").includes("whitelist"));

  const log = logbus.dump();
  check("the password never reaches the console", !log.includes("hunter2"), log.slice(-300));

  globalThis.fetch = stubFetch({}, { fail: true });
  await liveStore.connect("http://127.0.0.1:9999");
  const offline = liveStore.getSnapshot();
  check("a failed connect lands in the offline state", offline.status === "offline", offline.status);
  check("the failure carries a reason", Boolean(offline.error));
  check("runs are blocked while offline", !liveStore.canRun);
}

// --- the dev-server wiring (what makes the preview reachable) --------------- //

interface FakeServer {
  server: ViteDevServer;
  http: EventEmitter;
}

function fakeServer(): FakeServer {
  const http = new EventEmitter();
  return { server: { httpServer: http } as unknown as ViteDevServer, http };
}

async function previewChecks(): Promise<void> {
  console.log("preview wiring (vite -> bridge)");

  const spawned: Array<{ command: string; args: string[]; options: SpawnOptions }> = [];
  const kills: string[] = [];
  const lines: string[] = [];

  const makeSpawn = (): ((c: string, a: string[], o: SpawnOptions) => SpawnedChild) =>
    (command, args, options) => {
      spawned.push({ command, args, options });
      return {
        pid: 4242,
        kill: (signal) => {
          kills.push(String(signal ?? "SIGTERM"));
          return true;
        },
      };
    };

  // 1. A fresh start: nothing is there, so the bridge is started and supervised.
  let probes = 0;
  const plugin = webbyBridge({
    port: 8787,
    root: "/repo",
    spawn: makeSpawn(),
    probe: async () => {
      probes += 1;
      return probes > 1; // not there for the pre-check, up on the first poll
    },
    log: (message) => lines.push(message),
  });

  check("the plugin only runs for the dev server", plugin.apply === "serve", String(plugin.apply));

  const patched = (plugin.config as () => { server?: { proxy?: Record<string, { target: string }> } })();
  const proxy = patched.server?.proxy?.["/api"];
  check("same-origin /api is proxied", Boolean(proxy), JSON.stringify(patched));
  check("the proxy points at the bridge", proxy?.target === "http://127.0.0.1:8787", String(proxy?.target));

  const { server, http } = fakeServer();
  (plugin.configureServer as (s: ViteDevServer) => void)(server);
  await new Promise((resolve) => setTimeout(resolve, 600));

  check("the bridge is started exactly once", spawned.length === 1, JSON.stringify(spawned.map((s) => s.command)));
  const argv = spawned[0]?.args.join(" ") ?? "";
  check("it runs the bridge CLI", argv.includes("-m webby serve"), argv);
  check("it points the bridge at the repository", argv.includes("--repo-root /repo"), argv);
  check("the bridge binds loopback only", argv.includes("--host 127.0.0.1"), argv);
  check("it provisions an admin login for the preview", argv.includes("--ensure-admin"), argv);
  check("its output is prefixed into the dev log", lines.some((line) => line.includes("bridge ready")), lines.join(" | "));

  // 2. Closing the dev server stops the bridge we started.
  http.emit("close");
  check("the bridge we started is stopped with SIGTERM", kills[0] === "SIGTERM", kills.join(","));

  // 3. A bridge that was already running is adopted, never killed.
  const adopted: string[] = [];
  const adoptionKills: string[] = [];
  const adopter = webbyBridge({
    port: 8787,
    root: "/repo",
    spawn: () => ({ pid: 1, kill: (signal) => adoptionKills.push(String(signal)) }),
    probe: async () => true,
    log: (message) => adopted.push(message),
  });
  const second = fakeServer();
  (adopter.configureServer as (s: ViteDevServer) => void)(second.server);
  await new Promise((resolve) => setTimeout(resolve, 200));
  check("an already-running bridge is adopted", adopted.some((line) => line.includes("already running")), adopted.join(" | "));
  second.http.emit("close");
  check("an adopted bridge is left alone", adoptionKills.length === 0, adoptionKills.join(","));

  // 4. Off means off: no proxy, no process.
  const offSpawns: string[] = [];
  const disabled = webbyBridge({
    enabled: false,
    spawn: (command) => {
      offSpawns.push(command);
      return { kill: () => true };
    },
    probe: async () => false,
    log: () => undefined,
  });
  const offPatch = (disabled.config as () => { server?: unknown })();
  check("a disabled plugin installs no proxy", !offPatch.server, JSON.stringify(offPatch));
  (disabled.configureServer as (s: ViteDevServer) => void)(fakeServer().server);
  await new Promise((resolve) => setTimeout(resolve, 100));
  check("a disabled plugin starts nothing", offSpawns.length === 0, offSpawns.join(","));

  // 5. The panel prefers the same origin, which is now the bridge.
  check("same origin is the first bridge address tried", bridgeCandidates()[0] === "");

  // 6. The real config resolves with the proxy in place and HMR still off.
  const resolved = await resolveConfig({}, "serve");
  const resolvedProxy = (resolved.server.proxy ?? {}) as Record<string, { target?: string }>;
  check(
    "the resolved dev config proxies /api to the bridge",
    String(resolvedProxy["/api"]?.target ?? "").includes(":8787"),
    JSON.stringify(resolvedProxy),
  );
  check("HMR stays disabled", resolved.server.hmr === false, String(resolved.server.hmr));
  check("the dev server still binds 0.0.0.0", resolved.server.host === true, String(resolved.server.host));
}

await clientChecks();
await storeChecks();
await previewChecks();

console.log();
if (failures > 0) {
  console.log(`  ${failures} failing bridge check(s)`);
  process.exit(1);
}
console.log("  the live-mode client and store behave");
process.exit(0);
