import { useSyncExternalStore } from "react";
import { logbus } from "../core/logbus";
import type { ConnectionConfig } from "../core/types";
import type {
  BridgeHealth,
  BridgeLogLine,
  BridgeRun,
  BridgeToolInfo,
  BridgeWhitelist,
} from "./api";
import { BridgeClient, BridgeError, BridgeUnreachableError, bridgeCandidates, inferLevel } from "./api";

export type LiveMode = "sim" | "live";
export type BridgeStatus = "idle" | "connecting" | "online" | "offline";

export interface LiveSnapshot {
  /** Simulated server, or the real tools through the bridge. */
  mode: LiveMode;
  status: BridgeStatus;
  /** The bridge URL in use; `""` means this page's own origin. */
  base: string;
  /** What the user typed, kept even when the connection failed. */
  baseInput: string;
  /** Every address the panel will try, for the diagnostics list. */
  candidates: string[];
  health: BridgeHealth | null;
  tools: BridgeToolInfo[];
  whitelist: BridgeWhitelist | null;
  run: BridgeRun | null;
  /** Whether an admin session is held in this tab. */
  signedIn: boolean;
  username: string | null;
  error: string | null;
  note: string | null;
  checkedAt: number | null;
}

const STORAGE_KEY = "tt-web.bridge.v1";
const HEALTH_POLL_MS = 5000;

const INITIAL: LiveSnapshot = {
  mode: "sim",
  status: "idle",
  base: "",
  baseInput: "",
  candidates: bridgeCandidates(),
  health: null,
  tools: [],
  whitelist: null,
  run: null,
  signedIn: false,
  username: null,
  error: null,
  note: null,
  checkedAt: null,
};

interface Persisted {
  mode?: LiveMode;
  base?: string;
  token?: string | null;
  username?: string | null;
}

function readPersisted(): Persisted {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Persisted;
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * One bridge connection per tab. It streams the active run's output into the
 * shared log bus, so the Console tab shows live output exactly like the
 * simulator does, and keeps the admin session for the allowlist editor.
 */
class LiveStore {
  private snapshot: LiveSnapshot = { ...INITIAL };
  private listeners = new Set<() => void>();
  private client = new BridgeClient();
  private events: EventSource | null = null;
  private pollTimer: number | null = null;
  private liveLineCount = 0;
  /** True once the operator has actually chosen a mode in this browser. */
  private modeChosen = false;

  constructor() {
    const persisted = readPersisted();
    this.modeChosen = persisted.mode !== undefined;
    this.client = new BridgeClient(persisted.base ?? "", persisted.token ?? null);
    this.snapshot = {
      ...INITIAL,
      mode: persisted.mode === "live" ? "live" : "sim",
      base: persisted.base ?? "",
      baseInput: persisted.base ?? "",
      signedIn: Boolean(persisted.token),
      username: persisted.username ?? null,
    };
  }

  // ----- plumbing --------------------------------------------------------- //

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): LiveSnapshot => this.snapshot;

  private update(patch: Partial<LiveSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  private persist(): void {
    try {
      window.localStorage.setItem(
        STORAGE_KEY,
        JSON.stringify({
          mode: this.snapshot.mode,
          base: this.snapshot.base,
          token: this.client.getToken(),
          username: this.snapshot.username,
        }),
      );
    } catch {
      /* storage disabled: the tab keeps working for this session */
    }
  }

  /** Begin talking to the bridge if live mode needs it (called at start-up). */
  init(): void {
    if (this.snapshot.mode === "live") {
      void this.connect();
      return;
    }
    // No mode chosen yet: if the bridge itself is serving this page (the
    // `./run_webby.sh start` setup), live is plainly what the operator wants.
    if (!this.modeChosen) void this.adoptServingBridge();
  }

  private async adoptServingBridge(): Promise<void> {
    try {
      const health = await new BridgeClient("").health;
      if (health && health.service === "webby") {
        logbus.sys("This page is served by the Webby bridge; switching to live mode.");
        this.setMode("live");
      }
    } catch {
      /* not served by a bridge: stay on the simulator */
    }
  }

  setMode(mode: LiveMode): void {
    if (this.snapshot.mode === mode) return;
    logbus.sys(
      mode === "live"
        ? "Live mode: runs will be sent to the Webby bridge."
        : "Simulated mode: runs act on the in-tab server model.",
    );
    this.modeChosen = true;
    this.update({ mode, error: null, note: null });
    this.persist();
    if (mode === "live") void this.connect();
    else this.disconnect("left live mode");
  }

  /** Try the configured address, then the conventional candidates. */
  async connect(explicitBase?: string): Promise<void> {
    this.update({ status: "connecting", error: null, note: null });
    const requested = explicitBase ?? this.snapshot.baseInput;
    const wanted = requested.trim().replace(/\/+$/, "");
    // An address the operator typed is used as-is; otherwise try every
    // plausible place the bridge could be, cheapest first.
    const list = wanted ? [wanted] : [this.snapshot.base, ...this.snapshot.candidates];

    let lastError: string | null = null;
    for (const candidate of Array.from(new Set(list))) {
      const attempt = new BridgeClient(candidate, this.client.getToken());
      try {
        const health = await attempt.health;
        const tools = await attempt.tools();
        const whitelist = await attempt.whitelist();
        let run: BridgeRun | null = null;
        try {
          run = await attempt.currentRun();
        } catch {
          run = null;
        }
        this.client = attempt;
        this.update({
          status: "online",
          base: candidate,
          baseInput: candidate,
          health,
          tools,
          whitelist,
          run,
          error: null,
          checkedAt: Date.now(),
        });
        this.persist();
        this.watchEvents();
        this.watchHealth();
        logbus.ok(
          `Webby bridge ${health.version} reachable at ${candidate || "this origin"} ` +
            `(${health.whitelist.count} allowlisted host(s), ${tools.length} tools).`,
        );
        if (this.client.getToken()) await this.verifySession();
        return;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
      }
    }

    this.disconnect("offline");
    this.update({
      status: "offline",
      base: wanted,
      baseInput: wanted,
      error: lastError ?? "the bridge did not answer",
      checkedAt: Date.now(),
    });
    this.persist();
    logbus.warn(`Live mode: no bridge answered. ${lastError ?? ""}`.trim());
  }

  private disconnect(reason: string): void {
    const wasConnected = this.events !== null || this.pollTimer !== null;
    if (this.events) {
      this.events.close();
      this.events = null;
    }
    if (this.pollTimer !== null) {
      window.clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    if (reason && wasConnected) logbus.sys(`Bridge stream closed (${reason}).`);
  }

  /** Refresh just the health snapshot, cheaply. */
  async refresh(): Promise<void> {
    if (this.snapshot.status !== "online") return;
    try {
      const [health, whitelist] = await Promise.all([this.client.health, this.client.whitelist()]);
      this.update({
        health,
        whitelist,
        run: health.run ?? this.snapshot.run,
        status: "online",
        error: null,
        checkedAt: Date.now(),
      });
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.update({ status: "offline", error: message, checkedAt: Date.now() });
      this.disconnect("lost the bridge");
    }
  }

  private watchHealth(): void {
    if (this.pollTimer !== null) window.clearInterval(this.pollTimer);
    this.pollTimer = window.setInterval(() => {
      if (this.snapshot.mode === "live") void this.refresh();
    }, HEALTH_POLL_MS);
  }

  private watchEvents(): void {
    if (this.events) this.events.close();
    const stream = this.client.openEvents();
    if (!stream) return;
    this.events = stream;

    stream.addEventListener("log", (event) => {
      const payload = JSON.parse((event as MessageEvent<string>).data) as BridgeLogLine & {
        run: string;
      };
      this.liveLineCount += 1;
      const text = payload.text ?? "";
      logbus[inferLevel(text)](text);
    });

    stream.addEventListener("state", (event) => {
      const raw = (event as MessageEvent<string>).data;
      const run = raw === "null" ? null : (JSON.parse(raw) as BridgeRun);
      const previous = this.snapshot.run;
      this.update({ run, checkedAt: Date.now() });
      if (run && previous && run.id === previous.id && previous.state === "running" && run.state !== "running") {
        const summary = `bridge run ${run.id} ${run.state}` +
          (run.exit_code === null ? "" : ` (exit ${run.exit_code})`);
        if (run.state === "finished") logbus.ok(summary);
        else if (run.state === "failed") logbus.error(`${summary}: ${run.error ?? "no detail"}`);
        else logbus.warn(summary);
        void this.refresh();
      }
    });

    stream.addEventListener("error", () => {
      // EventSource retries by itself; only surface it once we have gone quiet.
      if (this.snapshot.status === "online") {
        this.update({ note: "the event stream dropped; reconnecting" });
      }
    });
  }

  // ----- admin session ---------------------------------------------------- //

  private async verifySession(): Promise<void> {
    try {
      const session = await this.client.session();
      if (session.authenticated) {
        this.update({ signedIn: true, username: session.username ?? null });
        this.persist();
      } else {
        this.client.setToken(null);
        this.update({ signedIn: false, username: null });
        this.persist();
      }
    } catch {
      /* keep the token; the next privileged call will say if it is stale */
    }
  }

  async login(username: string, password: string): Promise<void> {
    this.update({ error: null, note: null });
    try {
      const session = await this.client.login(username, password);
      this.client.setToken(session.token);
      this.update({ signedIn: true, username: session.username, note: `signed in as ${session.username}` });
      this.persist();
      logbus.ok(`Admin session opened for '${session.username}'.`);
      await this.reloadWhitelist();
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.update({ error: message, signedIn: false, username: null });
      logbus.error(`Admin sign-in failed: ${message}`);
      throw error;
    }
  }

  async logout(): Promise<void> {
    try {
      await this.client.logout();
    } catch {
      /* dropping the token locally is what matters */
    }
    this.client.setToken(null);
    this.update({ signedIn: false, username: null, note: "signed out" });
    this.persist();
    logbus.sys("Admin session closed.");
    await this.reloadWhitelist();
  }

  async reloadWhitelist(): Promise<BridgeWhitelist | null> {
    if (this.snapshot.status !== "online") return null;
    try {
      const whitelist = await this.client.whitelist();
      this.update({ whitelist, error: null });
      return whitelist;
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error) });
      return null;
    }
  }

  /** Replace the allowlist file. Requires an admin session. */
  async saveWhitelist(raw: string, force = false): Promise<void> {
    this.update({ error: null, note: null });
    try {
      const current = this.snapshot.whitelist;
      const updated = await this.client.saveWhitelist(raw, {
        mtime: current?.mtime,
        force,
      });
      this.update({
        whitelist: updated,
        note: `allowlist saved (${updated.count} entr(ies)) to ${updated.path}`,
      });
      logbus.ok(`Allowlist updated: ${updated.count} entr(ies) in ${updated.path}`);
      await this.refresh();
    } catch (error) {
      if (error instanceof BridgeError && error.status === 409) {
        await this.reloadWhitelist();
        this.update({
          error: `${error.message} — the file was reloaded, re-apply your change.`,
        });
      } else {
        this.update({ error: error instanceof Error ? error.message : String(error) });
      }
      throw error;
    }
  }

  // ----- runs ------------------------------------------------------------- //

  /** True when the bridge is reachable, so the tools can be run for real. */
  get canRun(): boolean {
    return this.snapshot.status === "online";
  }

  async startRun(
    tool: string,
    params: Record<string, string>,
    config: ConnectionConfig,
    confirm: boolean,
  ): Promise<void> {
    this.update({ error: null, note: null });
    this.liveLineCount = 0;
    const connection = {
      host: config.host,
      tcp_port: config.tcpPort,
      udp_port: config.udpPort,
      username: config.username,
      password: config.password,
      nickname: config.nickname,
      client_name: config.clientName,
      channel_path: config.channelPath ?? "",
      channel_id: config.channelId,
      channel_password: config.channelPassword,
      encrypted: config.encrypted,
      timeout: config.commandTimeoutSec,
      reconnect_delay: config.reconnectDelaySec,
      kick_resistance: config.kickResistance,
    };
    logbus.sys(`Sending ${tool} to the bridge for ${config.host}:${config.tcpPort}…`);
    try {
      const run = await this.client.startRun({ tool, params, connection, confirm });
      this.update({
        run,
        note: run.note ? `${run.script}: ${run.note}` : `${run.script} started (${run.id})`,
      });
      logbus.sys(`Bridge argv: ${run.argv.join(" ")}`);
      if (run.note) logbus.warn(run.note);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.update({ error: message });
      logbus.error(`The bridge refused the run: ${message}`);
      throw error;
    }
  }

  async stopRun(): Promise<void> {
    try {
      const run = await this.client.stopRun();
      this.update({ run, note: "stop requested" });
    } catch (error) {
      this.update({ error: error instanceof Error ? error.message : String(error) });
    }
  }

  async clearRun(): Promise<void> {
    this.update({ run: null });
    await this.refresh();
  }

  /** Persist a new bridge address without connecting yet. */
  setBaseInput(value: string): void {
    this.update({ baseInput: value });
  }
}

export const liveStore = new LiveStore();

export function useLive(): LiveSnapshot {
  return useSyncExternalStore(liveStore.subscribe, liveStore.getSnapshot, liveStore.getSnapshot);
}

export function useLiveMode(): LiveMode {
  return useLive().mode;
}

export { BridgeUnreachableError };
