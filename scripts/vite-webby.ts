/**
 * Vite plugin: make the Webby bridge reachable from the dev server.
 *
 * The preview only exposes one port — the app's. The bridge listens on its own
 * (8787), so the browser can never reach it directly. This plugin closes that
 * gap for local development and the preview:
 *
 * 1. it proxies same-origin `/api/*` (including the server-sent event stream)
 *    to the bridge, so the panel's own origin *is* the bridge address; and
 * 2. it starts the bridge as a child of the dev server, so opening the preview
 *    is enough — no second terminal — and stops it when the dev server exits.
 *
 * It is `apply: "serve"` only: a production build never spawns anything, and
 * `WEBBY_DISABLE=1` turns it off. If something is already serving the port (for
 * example `./run_webby.sh start`), that bridge is adopted instead of spawning a
 * second one, and it is left running when the dev server exits.
 */
import { spawn as nodeSpawn } from "node:child_process";
import type { ChildProcess } from "node:child_process";
import type { Plugin, ViteDevServer } from "vite";

export const DEFAULT_PORT = 8787;

/** The slice of `ChildProcess` the plugin needs (injectable for tests). */
export interface SpawnedChild {
  pid?: number;
  kill(signal?: NodeJS.Signals | number): unknown;
}

export interface SpawnOptions {
  cwd: string;
  env: NodeJS.ProcessEnv;
  stdio: Array<"ignore" | "pipe">;
}

export interface WebbyBridgeOptions {
  /** Bridge port. Defaults to `WEBBY_PORT` or 8787. */
  port?: number;
  /** Python interpreter. Defaults to `WEBBY_PYTHON` or python3. */
  python?: string;
  /** Repository root the bridge is told about. Defaults to the process cwd. */
  root?: string;
  /** Set false to disable entirely. Defaults to `WEBBY_DISABLE` being unset. */
  enabled?: boolean;
  /** Extra flags for `python3 -m webby serve`. */
  serveArgs?: string[];
  /** Injected for tests. */
  spawn?: (command: string, args: string[], options: SpawnOptions) => SpawnedChild;
  /** Injected for tests: is a bridge already answering at this URL? */
  probe?: (url: string) => Promise<boolean>;
  /** Injected for tests. */
  log?: (message: string) => void;
}

interface BridgeHandle {
  child: SpawnedChild;
  /** True when we started it, so we are the ones who stop it. */
  owned: boolean;
}

/** Strip anything that would break a bare `host:port` display. */
function display(port: number): string {
  return `http://127.0.0.1:${port}`;
}

/**
 * Ask the bridge whether it is already there. Never throws: a refused
 * connection is the normal "nothing there yet" answer.
 */
async function defaultProbe(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1500) });
    if (!response.ok) return false;
    const payload = (await response.json()) as { service?: string };
    return payload?.service === "webby";
  } catch {
    return false;
  }
}

function defaultSpawn(
  command: string,
  args: string[],
  options: SpawnOptions,
): ChildProcess {
  return nodeSpawn(command, args, {
    cwd: options.cwd,
    env: options.env,
    stdio: options.stdio as never,
    detached: false,
  });
}

export function webbyBridge(options: WebbyBridgeOptions = {}): Plugin {
  const enabled = options.enabled ?? process.env.WEBBY_DISABLE !== "1";
  const port = options.port ?? (Number(process.env.WEBBY_PORT) || DEFAULT_PORT);
  const python = options.python ?? process.env.WEBBY_PYTHON ?? "python3";
  const root = options.root ?? process.cwd();
  const spawnImpl: (command: string, args: string[], options: SpawnOptions) => SpawnedChild =
    options.spawn ?? ((command, args, spawnOptions) => defaultSpawn(command, args, spawnOptions));
  const probe = options.probe ?? defaultProbe;
  const log = options.log ?? ((message: string) => console.log(message));
  const healthUrl = `${display(port)}/api/health`;

  let handle: BridgeHandle | null = null;

  const stop = (reason: string): void => {
    if (!handle) return;
    const { child, owned } = handle;
    handle = null;
    if (!owned) {
      log(`[webby] leaving the bridge on port ${port} alone (${reason})`);
      return;
    }
    if (child.pid) {
      log(`[webby] stopping the bridge (pid ${child.pid}, ${reason})`);
      child.kill("SIGTERM");
    }
  };

  const pipe = (stream: NodeJS.ReadableStream | null, level: string): void => {
    if (!stream) return;
    let buffer = "";
    stream.on("data", (chunk: Buffer | string) => {
      buffer += String(chunk);
      const lines = buffer.split("\n");
      buffer = lines.pop() ?? "";
      for (const line of lines) {
        if (line.trim()) log(`[webby ${level}] ${line}`);
      }
    });
  };

  return {
    name: "webby-bridge",
    apply: "serve",

    // Same-origin `/api` -> the bridge, so the panel needs no CORS and no
    // second address. The event stream is proxied too.
    config() {
      if (!enabled) return {};
      return {
        server: {
          proxy: {
            "/api": {
              target: display(port),
              changeOrigin: false,
              ws: false,
            },
          },
        },
      };
    },

    configureServer(server: ViteDevServer) {
      if (!enabled) {
        log("[webby] bridge supervision disabled (WEBBY_DISABLE=1)");
        return;
      }

      const httpServer = server.httpServer;
      const onClose = (): void => stop("dev server closed");
      httpServer?.once("close", onClose);
      process.once("exit", onClose);
      process.once("SIGINT", () => stop("interrupted"));
      process.once("SIGTERM", () => stop("terminated"));

      void (async () => {
        if (await probe(healthUrl)) {
          handle = { child: { kill: () => undefined }, owned: false };
          log(`[webby] using the bridge already running at ${display(port)}`);
          return;
        }

        const args = [
          "-u",
          "-m",
          "webby",
          "serve",
          "--repo-root",
          root,
          "--host",
          "127.0.0.1",
          "--port",
          String(port),
          "--ensure-admin",
          ...(options.serveArgs ?? []),
        ];
        if (options.spawn) {
          handle = { child: spawnImpl(python, args, spawnOptions(root)), owned: true };
        } else {
          const child = spawnImpl(python, args, spawnOptions(root)) as ChildProcess;
          pipe(child.stdout, "out");
          pipe(child.stderr, "err");
          child.on("error", (error: Error) => {
            log(
              `[webby] could not start '${python} -m webby' (${error.message}). Live mode stays ` +
                "offline until a bridge is reachable — start one with ./run_webby.sh start, or set " +
                "WEBBY_DISABLE=1 to silence this.",
            );
          });
          child.on("exit", (code: number | null) => {
            if (handle?.child.pid === child.pid) handle = null;
            if (code && code !== 0 && code !== null) {
              log(`[webby] the bridge exited with status ${code}; see its output above`);
            }
          });
          handle = { child, owned: true };
          log(`[webby] starting the bridge: ${python} ${args.join(" ")}`);
        }

        // Wait for it to answer, so a failure is visible in the dev log.
        const deadline = Date.now() + 20000;
        while (Date.now() < deadline) {
          if (await probe(healthUrl)) {
            log(`[webby] bridge ready — /api/* is proxied to ${display(port)}`);
            return;
          }
          if (!handle) {
            log("[webby] bridge process went away before it answered");
            return;
          }
          await new Promise((resolve) => setTimeout(resolve, 400));
        }
        log(
          `[webby] the bridge did not answer ${healthUrl} within 20s; ` +
            "live mode will report it as offline (check the [webby err] lines above)",
        );
      })();
    },
  };
}

function spawnOptions(root: string): SpawnOptions {
  return {
    cwd: root,
    env: {
      ...process.env,
      PYTHONUNBUFFERED: "1",
      WEBBY_QUIET: "1",
    },
    stdio: ["ignore", "pipe", "pipe"],
  };
}

export default webbyBridge;
