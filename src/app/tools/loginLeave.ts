import { int, num } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import type { RunContext } from "./context";
import { formatSeconds, retry } from "./context";
import { leaveJoinCycles, loginLogoutCycles } from "./ops";

/**
 * Port of `tt_spammer.py`: one connection is kept open while it repeats
 * authenticated login/logout cycles without joining a channel.
 */
export async function runLoginCycles(ctx: RunContext): Promise<void> {
  const cycles = int(ctx.values, "cycles", 5);
  const intervalMs = Math.max(0, num(ctx.values, "interval_ms", 200));
  const waitSec = Math.max(0, num(ctx.values, "wait", 0));
  if (cycles < 1) throw new TeamTalkConfigError("Cycles must be at least 1.");

  if (waitSec > 0) {
    ctx.sys(`Waiting ${waitSec} s before starting.`);
    await ctx.sleep(waitSec * 1000);
  }

  ctx.sys(`Login/logout cycles against ${ctx.config.host}:${ctx.config.tcpPort}`);
  const session = ctx.newSession("login", { joinOnLogin: false });
  await session.connect();

  const started = Date.now();
  const completed = await loginLogoutCycles(ctx, session, cycles, intervalMs);
  const elapsed = Date.now() - started;

  ctx.result("Completed cycles", `${completed}/${cycles}`);
  ctx.result("Elapsed", formatSeconds(elapsed));
  ctx.result("Reconnects", session.recoveries);
  ctx.ok(
    `Done: ${completed}/${cycles} login/logout cycle(s) completed through ` +
      `${session.recoveries} reconnect(s) in ${formatSeconds(elapsed)}.`,
  );

  await session.disconnect();
}

/**
 * Port of `tt_leave_join_spammer.py`: join the configured channel, then leave
 * and rejoin it for each requested cycle.
 */
export async function runLeaveJoin(ctx: RunContext): Promise<void> {
  const cycles = int(ctx.values, "cycles", 5);
  const intervalMs = Math.max(0, num(ctx.values, "interval_ms", 200));
  const waitSec = Math.max(0, num(ctx.values, "wait", 50));
  if (cycles < 1) throw new TeamTalkConfigError("Cycles must be at least 1.");

  if (waitSec > 0) {
    ctx.sys(`Waiting ${waitSec} s before joining so the channel settles.`);
    await ctx.sleep(waitSec * 1000);
  }

  const session = ctx.newSession("leavejoin");
  await session.connect();
  await session.login();

  const channelId = session.currentChannelId;
  if (channelId === null) {
    throw new TeamTalkConfigError("Set a channel in Server before running the leave/join test.");
  }
  const discovery = await retry(ctx, "channel discovery", async () => session.channels());
  const path = discovery.find((channel) => channel.id === channelId)?.path ?? "channel";
  ctx.sys(`Leave/rejoin cycles in ${path} on ${ctx.config.host}:${ctx.config.tcpPort}`);

  const started = Date.now();
  const completed = await leaveJoinCycles(ctx, session, cycles, intervalMs);
  const elapsed = Date.now() - started;

  ctx.result("Completed cycles", `${completed}/${cycles}`);
  ctx.result("Channel", path);
  ctx.result("Elapsed", formatSeconds(elapsed));
  ctx.result("Reconnects", session.recoveries);
  ctx.ok(
    `Done: ${completed}/${cycles} leave/join cycle(s) in ${path} through ` +
      `${session.recoveries} reconnect(s) in ${formatSeconds(elapsed)}.`,
  );

  await session.disconnect();
}
