/**
 * Client for the Webby bridge — the local process that runs the real tools.
 *
 * A page cannot open a raw TCP connection to a TeamTalk server, so "live" mode
 * talks to `python3 -m webby` (started with `./run_webby.sh start`) instead.
 * Every request here maps to one route in `webby/httpapi.py`.
 */

/** The default port `run_webby.sh` binds to. */
export const DEFAULT_BRIDGE_PORT = 8787;

export interface BridgeConfigInfo {
  repo_root: string;
  host: string;
  port: number;
  whitelist_path: string;
  admin_path: string;
  state_dir: string;
  python: string;
  accept_sdk_license: boolean;
  require_admin_for_runs: boolean;
  max_run_seconds: number;
  dist_dir: string;
  dist_built: boolean;
  sdk_python_present: boolean;
  sdk_library_present: boolean;
  sdk_marker_present: boolean;
  allow_origins: string[];
}

export interface BridgeWhitelist {
  path: string;
  entries: string[];
  count: number;
  exists: boolean;
  mtime: number;
  /** Only returned to an authenticated admin. */
  raw?: string;
  warning?: string;
  by?: string;
}

export interface BridgeAdminInfo {
  configured: boolean;
  username: string | null;
  source: "env" | "file" | "none";
}

export type BridgeRunState = "running" | "finished" | "failed" | "stopped";

export interface BridgeRun {
  id: string;
  tool: string;
  label: string;
  script: string;
  state: BridgeRunState;
  pid: number | null;
  host: string;
  /** Credential flags are already replaced with `***` by the bridge. */
  argv: string[];
  started_at: number;
  finished_at: number | null;
  exit_code: number | null;
  error: string | null;
  note: string;
  stop_reason: string | null;
  line_count: number;
}

export interface BridgeLogLine {
  n: number;
  time: number;
  text: string;
}

export interface BridgeHealth {
  ok: boolean;
  service: string;
  version: string;
  mode: string;
  time: number;
  config: BridgeConfigInfo;
  whitelist: BridgeWhitelist;
  admin: BridgeAdminInfo;
  run: BridgeRun | null;
  tools: string[];
}

export interface BridgeToolInfo {
  id: string;
  label: string;
  script: string;
  requires_confirm: boolean;
  requires_whitelist: boolean;
  local_only: boolean;
  accepts_connection: boolean;
  ignored: string[];
  note: string;
}

export interface BridgeSession {
  token: string;
  username: string;
  expires_at: number;
  ttl: number;
}

export interface StartRunRequest {
  tool: string;
  params: Record<string, string>;
  connection: Record<string, string | number | boolean | null>;
  confirm: boolean;
}

/** Any non-2xx answer, or a body the bridge rejected. */
export class BridgeError extends Error {
  readonly status: number;

  constructor(message: string, status = 0) {
    super(message);
    this.name = "BridgeError";
    this.status = status;
  }
}

/** The bridge could not be reached at all (not running, wrong address). */
export class BridgeUnreachableError extends BridgeError {
  constructor(message: string) {
    super(message, 0);
    this.name = "BridgeUnreachableError";
  }
}

function normalizeBase(base: string): string {
  const trimmed = base.trim().replace(/\/+$/, "");
  return trimmed;
}

function absolute(base: string, path: string): string {
  return `${normalizeBase(base)}${path}`;
}

/**
 * Where a bridge might be, in the order we should try it:
 * same origin first (the panel served by the bridge itself, or a dev server
 * that proxies /api), then the conventional port on this host and loopback.
 */
export function bridgeCandidates(): string[] {
  const candidates: string[] = [""];
  try {
    const { protocol, hostname, port } = window.location;
    const scheme = protocol === "https:" ? "https:" : "http:";
    if (hostname && port !== String(DEFAULT_BRIDGE_PORT)) {
      candidates.push(`${scheme}//${hostname}:${DEFAULT_BRIDGE_PORT}`);
    }
    if (hostname !== "127.0.0.1") candidates.push(`http://127.0.0.1:${DEFAULT_BRIDGE_PORT}`);
    if (hostname !== "localhost") candidates.push(`http://localhost:${DEFAULT_BRIDGE_PORT}`);
  } catch {
    /* no window: keep the same-origin candidate only */
  }
  return Array.from(new Set(candidates));
}

/** A short label for a base URL, for the diagnostics list. */
export function describeBase(base: string): string {
  if (!base) return "this page's own origin (served by the bridge)";
  return base;
}

const REQUEST_TIMEOUT_MS = 6000;

export class BridgeClient {
  private base: string;
  private token: string | null;

  constructor(base = "", token: string | null = null) {
    this.base = normalizeBase(base);
    this.token = token;
  }

  get url(): string {
    return this.base;
  }

  setBase(base: string): void {
    this.base = normalizeBase(base);
  }

  setToken(token: string | null): void {
    this.token = token;
  }

  getToken(): string | null {
    return this.token;
  }

  /** The absolute URL of a route, for display and for EventSource. */
  endpoint(path: string): string {
    return absolute(this.base, path);
  }

  private async request<T>(
    method: string,
    path: string,
    body?: unknown,
  ): Promise<T> {
    const controller = new AbortController();
    const timer = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    const headers: Record<string, string> = {};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (this.token) headers.Authorization = `Bearer ${this.token}`;
    try {
      const response = await fetch(absolute(this.base, path), {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
        signal: controller.signal,
      });
      const text = await response.text();
      let payload: unknown = null;
      if (text) {
        try {
          payload = JSON.parse(text);
        } catch {
          payload = null;
        }
      }
      if (!response.ok) {
        const message =
          payload && typeof payload === "object" && "error" in payload
            ? String((payload as { error: unknown }).error)
            : `${response.status} ${response.statusText}`;
        throw new BridgeError(message, response.status);
      }
      return payload as T;
    } catch (error) {
      if (error instanceof BridgeError) throw error;
      const reason = error instanceof Error ? error.message : String(error);
      throw new BridgeUnreachableError(
        `no answer from ${describeBase(this.base)} (${reason}). Is the bridge running? ` +
          "Start it with ./run_webby.sh start",
      );
    } finally {
      window.clearTimeout(timer);
    }
  }

  get health(): Promise<BridgeHealth> {
    return this.request<BridgeHealth>("GET", "/api/health");
  }

  async tools(): Promise<BridgeToolInfo[]> {
    const payload = await this.request<{ tools: BridgeToolInfo[] }>("GET", "/api/tools");
    return payload.tools ?? [];
  }

  whitelist(): Promise<BridgeWhitelist> {
    return this.request<BridgeWhitelist>("GET", "/api/whitelist");
  }

  saveWhitelist(
    raw: string,
    options: { mtime?: number; force?: boolean } = {},
  ): Promise<BridgeWhitelist> {
    const body: Record<string, unknown> = { raw };
    if (options.mtime !== undefined) body.mtime = options.mtime;
    if (options.force) body.force = true;
    return this.request<BridgeWhitelist>("PUT", "/api/whitelist", body);
  }

  login(username: string, password: string): Promise<BridgeSession> {
    return this.request<BridgeSession>("POST", "/api/admin/login", { username, password });
  }

  logout(): Promise<{ ok: boolean }> {
    return this.request<{ ok: boolean }>("POST", "/api/admin/logout", {});
  }

  session(): Promise<{ authenticated: boolean; username?: string; expires_at?: number }> {
    return this.request("GET", "/api/admin/session");
  }

  async currentRun(): Promise<BridgeRun | null> {
    const payload = await this.request<{ run: BridgeRun | null }>("GET", "/api/runs/current");
    return payload.run ?? null;
  }

  async startRun(request: StartRunRequest): Promise<BridgeRun> {
    const payload = await this.request<{ run: BridgeRun }>("POST", "/api/runs", request);
    return payload.run;
  }

  async stopRun(): Promise<BridgeRun> {
    const payload = await this.request<{ run: BridgeRun }>("POST", "/api/runs/stop", {});
    return payload.run;
  }

  /** The event stream. Never carries credentials, so no auth header is needed. */
  openEvents(): EventSource | null {
    if (typeof EventSource === "undefined") return null;
    return new EventSource(absolute(this.base, "/api/events"));
  }
}

/**
 * Map a raw tool output line onto a console level.
 *
 * The tools print plain prose, so this is a heuristic — it decides colour in
 * the console, nothing else. Note the patterns deliberately anchor only at the
 * start of a word, so "refused" and "successfully" are recognised.
 */
export function inferLevel(text: string): "info" | "ok" | "warn" | "error" | "sys" {
  const line = text.toLowerCase();
  if (line.startsWith("---") || line.startsWith("===")) return "sys";
  if (/\b(traceback|exception|error|fatal|panic)/.test(line)) return "error";
  if (/(failed|failure|cannot|refus|denied|unreach|timeout|timed out|warn|retry|retrying|kicked|degraded|dropped|broken)/.test(line)) {
    return "warn";
  }
  if (/(\bok|done|success|succeed|connected|passed|healthy|recovered|finished)/.test(line)) {
    return "ok";
  }
  return "info";
}
