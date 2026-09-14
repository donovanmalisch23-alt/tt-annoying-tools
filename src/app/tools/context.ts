import type { LogLevel } from "../core/logbus";
import { logbus } from "../core/logbus";
import type { ToolSpec, Values } from "../core/registry";
import type { ConnectionConfig } from "../core/types";
import { TeamTalkError, ToolCancelledError } from "../core/types";
import { advanceReal, scaled, sleepRaw } from "../sim/clock";
import type { SimServer } from "../sim/server";
import { SimSession } from "../sim/session";

export interface RunHandle {
  stopped: boolean;
  stop(): void;
}

/** One headline number shown as a chip when a run finishes. */
export interface ResultChip {
  label: string;
  value: string;
}

export interface RunContextArgs {
  spec: ToolSpec;
  values: Values;
  config: ConnectionConfig;
  server: SimServer;
  whitelist: string[];
  handle: RunHandle;
  /** Hard cap on concurrent sessions for this tool. */
  sessionLimit: number;
  /** Shared with the run manager, so partial results survive a stop. */
  results: ResultChip[];
}

/** Short human-readable duration for logs and summaries. */
export function formatSeconds(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

/**
 * Everything one tool run needs: its parameters, the simulated server, a
 * session factory, a stop-aware sleep, and the run's counters. This is the web
 * counterpart of the CLI's run context plus its logging helpers.
 */
export class RunContext {
  readonly spec: ToolSpec;
  readonly values: Values;
  readonly config: ConnectionConfig;
  readonly server: SimServer;
  readonly whitelist: string[];
  readonly handle: RunHandle;

  /** Headline numbers shown as chips when the run finishes. */
  readonly results: ResultChip[];

  private readonly sessionLimit: number;
  private sessions: SimSession[] = [];
  private closed = false;

  constructor(args: RunContextArgs) {
    this.spec = args.spec;
    this.values = args.values;
    this.config = args.config;
    this.server = args.server;
    this.whitelist = args.whitelist;
    this.handle = args.handle;
    this.sessionLimit = args.sessionLimit;
    this.results = args.results;
  }

  // ----- logging ---------------------------------------------------------- //

  log(level: LogLevel, text: string): void {
    logbus[level](text);
  }

  info = (text: string): void => this.log("info", text);
  ok = (text: string): void => this.log("ok", text);
  warn = (text: string): void => this.log("warn", text);
  error = (text: string): void => this.log("error", text);
  sys = (text: string): void => this.log("sys", text);

  /** Records a headline number, replacing any earlier value with the same label. */
  result(label: string, value: string | number): void {
    const text = String(value);
    const existing = this.results.find((chip) => chip.label === label);
    if (existing) existing.value = text;
    else this.results.push({ label, value: text });
  }

  // ----- sessions --------------------------------------------------------- //

  newSession(label: string, options: { quiet?: boolean; joinOnLogin?: boolean } = {}): SimSession {
    if (this.sessions.length >= this.sessionLimit) {
      throw new TeamTalkError(
        `refusing to open session '${label}': this tool is capped at ${this.sessionLimit} ` +
          "concurrent connections in the web panel (the CLI forks worker processes instead).",
      );
    }
    const session = new SimSession(this.server, this.config, {
      label,
      quiet: options.quiet ?? false,
      joinOnLogin: options.joinOnLogin ?? true,
    });
    this.sessions.push(session);
    return session;
  }

  get sessionCount(): number {
    return this.sessions.length;
  }

  async closeSession(session: SimSession): Promise<void> {
    try {
      await session.disconnect();
    } catch {
      /* already gone */
    }
    this.sessions = this.sessions.filter((candidate) => candidate !== session);
  }

  /** Drops every session this run opened. Safe to call twice. */
  async cleanup(): Promise<void> {
    if (this.closed) return;
    this.closed = true;
    const sessions = this.sessions;
    this.sessions = [];
    await Promise.all(
      sessions.map(async (session) => {
        try {
          await session.disconnect();
        } catch {
          /* best effort */
        }
      }),
    );
  }

  // ----- control ---------------------------------------------------------- //

  get stopped(): boolean {
    return this.handle.stopped;
  }

  checkStop(): void {
    if (this.handle.stopped) throw new ToolCancelledError();
  }

  /**
   * Stop-aware sleep: the wait is compressed by the simulator's time scale,
   * and a Stop request breaks out within ~60 ms.
   */
  async sleep(ms: number): Promise<void> {
    const total = scaled(ms);
    let waited = 0;
    while (waited < total) {
      this.checkStop();
      const slice = Math.min(60, total - waited);
      await sleepRaw(slice);
      waited += slice;
    }
    // The wait counts as `ms` on the simulated clock, whatever the wall time was.
    advanceReal(waited);
    this.checkStop();
  }

  /** A short settle whose interruption still respects the stop flag. */
  async settle(ms: number): Promise<void> {
    await this.sleep(ms);
  }
}

/**
 * Bounded retry, matching the CLI rule: an operation interrupted by a kick or
 * a lost command is retried after the session reconnects, and only completed
 * operations count. Three consecutive failures against the same target give up
 * cleanly so an unreachable target cannot become a retry storm.
 */
export async function retry<T>(
  ctx: RunContext,
  what: string,
  operation: () => Promise<T>,
): Promise<T> {
  let failures = 0;
  for (;;) {
    ctx.checkStop();
    try {
      return await operation();
    } catch (error) {
      if (error instanceof ToolCancelledError) throw error;
      failures += 1;
      const message = error instanceof Error ? error.message : String(error);
      if (failures >= 3) {
        throw new TeamTalkError(`${what} gave up after 3 consecutive failures: ${message}`);
      }
      ctx.warn(`${what} failed (attempt ${failures}/3): ${message} — retrying after recovery`);
      await ctx.sleep(150);
    }
  }
}

const LOOPBACK = new Set(["localhost", "127.0.0.1", "::1", "0.0.0.0"]);

/**
 * The flood tools' local-only gate, mirroring the CLI: the target must be this
 * machine — loopback or any address in a private range.
 */
export function isLocalTarget(host: string): boolean {
  const normalized = host.trim().toLowerCase();
  if (LOOPBACK.has(normalized)) return true;
  if (normalized.startsWith("127.")) return true;
  if (normalized.startsWith("10.")) return true;
  if (normalized.startsWith("192.168.")) return true;
  if (normalized.startsWith("169.254.")) return true;
  const match = /^172\.(\d{1,3})\./.exec(normalized);
  if (match) {
    const second = Number(match[1]);
    if (second >= 16 && second <= 31) return true;
  }
  return false;
}
