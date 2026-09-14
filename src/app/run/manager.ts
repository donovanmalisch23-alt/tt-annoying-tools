import { logbus } from "../core/logbus";
import type { Values } from "../core/registry";
import { toolSpec } from "../core/registry";
import type { ConnectionConfig } from "../core/types";
import { TeamTalkConfigError, ToolCancelledError, validateConfig, Whitelist } from "../core/types";
import { server } from "../sim/instance";
import type { SimServer } from "../sim/server";
import type { ResultChip, RunHandle } from "../tools/context";
import { runTool } from "../tools";

export type RunState = "idle" | "running" | "finished" | "failed" | "cancelled";

export interface RunSnapshot {
  state: RunState;
  toolId: string | null;
  toolTitle: string;
  startedAt: number | null;
  finishedAt: number | null;
  error: string | null;
  results: ResultChip[];
}

const IDLE: RunSnapshot = {
  state: "idle",
  toolId: null,
  toolTitle: "",
  startedAt: null,
  finishedAt: null,
  error: null,
  results: [],
};

export interface StartArgs {
  toolId: string;
  values: Values;
  config: ConnectionConfig;
  whitelist: string[];
  confirmed: boolean;
}

/**
 * Owns the one running tool, enforcing the same gates as the CLI before a run
 * is allowed to start: a valid connection config, the exact-host allowlist,
 * and an explicit confirmation for the heavy tools.
 */
class ToolRunManager {
  private snapshot: RunSnapshot = IDLE;
  private listeners = new Set<() => void>();
  private handle: RunHandle | null = null;

  constructor(private readonly server: SimServer) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = (): RunSnapshot => this.snapshot;

  get isRunning(): boolean {
    return this.snapshot.state === "running";
  }

  private update(patch: Partial<RunSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    this.listeners.forEach((listener) => listener());
  }

  /** Validates and starts a run. Throws if a gate refuses; returns immediately. */
  start(args: StartArgs): void {
    if (this.isRunning) throw new TeamTalkConfigError("A run is already in progress. Stop it first.");

    const spec = toolSpec(args.toolId);
    const config = validateConfig(args.config);

    if (spec.requiresWhitelist) Whitelist.requireAllowed(config.host, args.whitelist);
    if (spec.requiresConfirm && !args.confirmed) {
      throw new TeamTalkConfigError(
        `${spec.title} requires an explicit confirmation before it runs.`,
      );
    }

    const handle: RunHandle = { stopped: false, stop: () => (handle.stopped = true) };
    this.handle = handle;
    // Shared with the run context, so a stopped run still shows its partial numbers.
    const results: ResultChip[] = [];

    this.update({
      state: "running",
      toolId: spec.id,
      toolTitle: spec.title,
      startedAt: Date.now(),
      finishedAt: null,
      error: null,
      results: [],
    });

    logbus.sys(`=== ${spec.title} started against ${config.host}:${config.tcpPort} ===`);

    void (async () => {
      try {
        const finalResults = await runTool({
          spec,
          values: args.values,
          config,
          server: this.server,
          whitelist: args.whitelist,
          handle,
          results,
        });
        this.update({
          state: handle.stopped ? "cancelled" : "finished",
          finishedAt: Date.now(),
          results: [...finalResults],
        });
        logbus.sys(`=== ${spec.title} ${handle.stopped ? "stopped by the operator" : "finished"} ===`);
      } catch (error) {
        if (error instanceof ToolCancelledError) {
          this.update({ state: "cancelled", finishedAt: Date.now(), results: [...results] });
          logbus.warn(`=== ${spec.title} stopped by the operator ===`);
        } else {
          const text = error instanceof Error ? error.message : String(error);
          this.update({
            state: "failed",
            finishedAt: Date.now(),
            error: text,
            results: [...results],
          });
          logbus.error(`${spec.title} failed: ${text}`);
        }
      } finally {
        this.handle = null;
      }
    })();
  }

  stop(): void {
    if (!this.handle || !this.isRunning) return;
    this.handle.stop();
    logbus.warn("Stop requested — the run is finishing its current operation.");
  }

  clear(): void {
    if (this.isRunning) return;
    this.update(IDLE);
  }
}

/** The single run manager for this tab. */
export const runManager = new ToolRunManager(server);
export type { ToolRunManager };
