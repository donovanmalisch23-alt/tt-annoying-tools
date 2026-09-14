import { int, num, parseUserList, str } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import type { RunContext } from "./context";
import { formatSeconds, retry } from "./context";
import { sendChannelSequence, sendPrivateSequence } from "./ops";

/**
 * Port of `tt_message_spammer.py`: a channel or private message sequence with
 * exact counts, kick resistance, and recipients addressed by username so a
 * mid-run relog retargets to the new user ID.
 */
export async function runMessageSender(ctx: RunContext): Promise<void> {
  const target = str(ctx.values, "target", "channel");
  const text = str(ctx.values, "message", "Oh Yeah!");
  const count = int(ctx.values, "count", 3);
  const intervalMs = Math.max(0, num(ctx.values, "interval_ms", 50));
  const waitSec = Math.max(0, num(ctx.values, "wait", 0));

  if (count < 1) throw new TeamTalkConfigError("Messages per recipient must be at least 1.");
  if (!text.trim()) throw new TeamTalkConfigError("The message text cannot be empty.");

  const users = parseUserList(str(ctx.values, "users"));
  if (target === "private" && users.length === 0) {
    throw new TeamTalkConfigError("Private messaging needs at least one recipient username.");
  }

  if (waitSec > 0) {
    ctx.sys(`Waiting ${waitSec} s before the first message so the channel can settle.`);
    await ctx.sleep(waitSec * 1000);
  }

  ctx.sys(
    `Sending ${count} message(s) every ${intervalMs} ms via ${target} target on ` +
      `${ctx.config.host}:${ctx.config.tcpPort}`,
  );

  const session = ctx.newSession("msg");
  await session.connect();
  await session.login();

  const started = Date.now();
  let delivered = 0;
  let recipients = 0;
  let channelPath = "";

  if (target === "private") {
    const roster = await retry(ctx, "recipient discovery", async () => session.users());
    const online = new Map(roster.map((user) => [user.username.toLowerCase(), user.username]));
    const missing = users.filter((name) => !online.has(name.toLowerCase()));
    if (missing.length > 0) {
      ctx.warn(
        `Not online right now, will be retried per send: ${missing.join(", ")}`,
      );
    } else {
      ctx.info(`Recipients: ${users.join(", ")}`);
    }
    for (const username of users) {
      ctx.checkStop();
      ctx.info(`Messaging '${username}' (${count} message(s))`);
      const sent = await sendPrivateSequence(ctx, session, username, text, count, intervalMs);
      delivered += sent;
      recipients += 1;
    }
  } else {
    const channelId = session.currentChannelId;
    if (channelId === null) {
      throw new TeamTalkConfigError("Set a channel in Server before sending a channel message.");
    }
    const discovery = await retry(ctx, "channel discovery", async () => session.channels());
    channelPath = discovery.find((channel) => channel.id === channelId)?.path ?? "channel";
    ctx.info(`Channel target: ${channelPath}`);
    delivered = await sendChannelSequence(ctx, session, channelId, text, count, intervalMs);
    recipients = 1;
  }

  const elapsed = Date.now() - started;
  ctx.result("Messages delivered", delivered);
  if (target === "private") ctx.result("Recipients", recipients);
  else ctx.result("Channel", channelPath);
  ctx.result("Elapsed", formatSeconds(elapsed));
  ctx.result("Reconnects", session.recoveries);
  ctx.ok(
    `Done: ${delivered} message(s) delivered through ${session.recoveries} reconnect(s) in ` +
      `${formatSeconds(elapsed)}.`,
  );

  await session.disconnect();
}
