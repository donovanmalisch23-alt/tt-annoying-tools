import { TeamTalkError } from "../core/types";
import type { SimSession } from "../sim/session";
import type { RunContext } from "./context";
import { retry } from "./context";

/**
 * Shared operation sequences. The standalone tools and the combined suite both
 * call these, so counts, retry behaviour and logging stay identical — the same
 * way the CLI's tools share `tt_teamtalk.py`.
 */

/** Login/logout cycles on an already-connected session. Returns completed cycles. */
export async function loginLogoutCycles(
  ctx: RunContext,
  session: SimSession,
  cycles: number,
  intervalMs: number,
): Promise<number> {
  if (cycles <= 0) return 0;
  let completed = 0;
  ctx.sys(`Repeating ${cycles} login/logout cycle(s) with ${intervalMs} ms between operations.`);
  for (let index = 1; index <= cycles; index += 1) {
    ctx.checkStop();
    await retry(ctx, `cycle ${index}/${cycles} login`, async () => {
      await session.login();
    });
    await ctx.sleep(intervalMs);
    await retry(ctx, `cycle ${index}/${cycles} logout`, async () => {
      await session.logout();
    });
    completed += 1;
    ctx.ok(`Completed login/logout cycle ${index}/${cycles}`);
    await ctx.sleep(intervalMs);
  }
  return completed;
}

/** Leave/join cycles in the session's current channel. Returns completed cycles. */
export async function leaveJoinCycles(
  ctx: RunContext,
  session: SimSession,
  cycles: number,
  intervalMs: number,
): Promise<number> {
  if (cycles <= 0) return 0;
  let completed = 0;
  ctx.sys(`Repeating ${cycles} leave/join cycle(s) with ${intervalMs} ms between operations.`);
  for (let index = 1; index <= cycles; index += 1) {
    ctx.checkStop();
    const channelId = session.currentChannelId;
    if (channelId === null) {
      throw new TeamTalkError("leave/join test needs a channel to work in; set one in Server.");
    }
    await retry(ctx, `cycle ${index}/${cycles} leave`, async () => {
      await session.leaveChannel();
    });
    await ctx.sleep(intervalMs);
    await retry(ctx, `cycle ${index}/${cycles} rejoin`, async () => {
      await session.joinById(channelId);
    });
    completed += 1;
    ctx.ok(`Completed leave/join cycle ${index}/${cycles}`);
    await ctx.sleep(intervalMs);
  }
  return completed;
}

/** A channel message sequence with exact counts. Returns messages delivered. */
export async function sendChannelSequence(
  ctx: RunContext,
  session: SimSession,
  channelId: number,
  text: string,
  count: number,
  intervalMs: number,
): Promise<number> {
  let sent = 0;
  for (let index = 1; index <= count; index += 1) {
    ctx.checkStop();
    await retry(ctx, `channel message ${index}/${count}`, async () => {
      await session.joinById(channelId);
      await session.sendChannelMessage(text);
    });
    sent += 1;
    if (sent === 1 || sent % 10 === 0 || sent === count) {
      ctx.ok(`Channel message ${sent}/${count} sent (${text.length} bytes of text)`);
    }
    await ctx.sleep(intervalMs);
  }
  return sent;
}

/**
 * Private-message sequence addressed by username, so a target who reconnects
 * mid-run is retargeted to their brand-new user ID automatically.
 */
export async function sendPrivateSequence(
  ctx: RunContext,
  session: SimSession,
  username: string,
  text: string,
  count: number,
  intervalMs: number,
): Promise<number> {
  let sent = 0;
  for (let index = 1; index <= count; index += 1) {
    ctx.checkStop();
    await retry(ctx, `${count} message(s) to '${username}' (${index}/${count})`, async () => {
      await session.sendPrivateMessage(username, text);
    });
    sent += 1;
    await ctx.sleep(intervalMs);
  }
  if (sent > 0) ctx.ok(`Sent ${sent}/${count} private message(s) to '${username}'`);
  return sent;
}
