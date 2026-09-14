import type { ToolSpec, Values } from "../core/registry";
import { TOOL_IDS } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import type { ConnectionConfig } from "../core/types";
import type { SimServer } from "../sim/server";
import { IDLE_BOT_MAX, runIdleBots, runResponseBot } from "./bots";
import type { ResultChip, RunHandle } from "./context";
import { RunContext } from "./context";
import { runLocalFlood, runRamp } from "./load";
import { runLeaveJoin, runLoginCycles } from "./loginLeave";
import { runMessageSender } from "./messaging";
import { runSuite, SUITE_BOT_MAX } from "./suite";

/**
 * How many simultaneous connections each tool may hold. The desktop suite
 * forks worker processes to stay under the native `select()` FD ceiling; one
 * browser tab cannot do that, so these caps are explicit and enforced.
 */
export const SESSION_LIMITS: Record<string, number> = {
  [TOOL_IDS.message]: 2,
  [TOOL_IDS.login]: 2,
  [TOOL_IDS.leaveJoin]: 2,
  [TOOL_IDS.responseBot]: 2,
  [TOOL_IDS.idleBots]: IDLE_BOT_MAX,
  [TOOL_IDS.suite]: SUITE_BOT_MAX + 2,
  [TOOL_IDS.loic]: 4,
  [TOOL_IDS.ramp]: 4,
};

export interface RunToolArgs {
  spec: ToolSpec;
  values: Values;
  config: ConnectionConfig;
  server: SimServer;
  whitelist: string[];
  handle: RunHandle;
  /** Chips collected by the run manager; also filled in on a stopped run. */
  results: ResultChip[];
}

/** Runs one tool to completion and returns its result chips. */
export async function runTool(args: RunToolArgs): Promise<ResultChip[]> {
  const { spec } = args;
  const ctx = new RunContext({
    spec,
    values: args.values,
    config: args.config,
    server: args.server,
    whitelist: args.whitelist,
    handle: args.handle,
    sessionLimit: SESSION_LIMITS[spec.id] ?? 4,
    results: args.results,
  });

  try {
    switch (spec.id) {
      case TOOL_IDS.message:
        await runMessageSender(ctx);
        break;
      case TOOL_IDS.login:
        await runLoginCycles(ctx);
        break;
      case TOOL_IDS.leaveJoin:
        await runLeaveJoin(ctx);
        break;
      case TOOL_IDS.idleBots:
        await runIdleBots(ctx);
        break;
      case TOOL_IDS.responseBot:
        await runResponseBot(ctx);
        break;
      case TOOL_IDS.suite:
        await runSuite(ctx);
        break;
      case TOOL_IDS.loic:
        await runLocalFlood(ctx);
        break;
      case TOOL_IDS.ramp:
        await runRamp(ctx);
        break;
      default:
        throw new TeamTalkConfigError(`No runner is registered for tool '${spec.id}'.`);
    }
    return ctx.results;
  } finally {
    await ctx.cleanup();
  }
}
