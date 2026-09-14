import { bool, int, num, parseUserList, str } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import { simNow } from "../sim/clock";
import type { RunContext } from "./context";
import { formatSeconds, retry } from "./context";

/** One app process cannot fork worker processes, so bots are capped. */
export const IDLE_BOT_MAX = 128;

/**
 * Port of `tt_concurrent_bots.py`: park logged-in clients on the server so they
 * occupy user slots. The simulator enforces its max-user cap, so once the
 * server fills up the remaining launches are refused and reported.
 */
export async function runIdleBots(ctx: RunContext): Promise<void> {
  const count = int(ctx.values, "count", 4);
  const startDelayMs = Math.max(0, int(ctx.values, "start_delay_ms", 120));
  const attempts = Math.max(1, int(ctx.values, "connect_attempts", 3));

  if (count < 1) throw new TeamTalkConfigError("Idle bots must be at least 1.");
  if (count > IDLE_BOT_MAX) {
    throw new TeamTalkConfigError(
      `This web panel caps idle bots at ${IDLE_BOT_MAX} concurrent connections in one tab. ` +
        `You asked for ${count}.`,
    );
  }

  ctx.sys(
    `Launching ${count} idle bot(s) against ${ctx.config.host}:${ctx.config.tcpPort}, ` +
      `${startDelayMs} ms apart, up to ${attempts} connect attempt(s) each.`,
  );

  const bots: Array<{ label: string; session: ReturnType<RunContext["newSession"]> }> = [];
  let refused = 0;
  const started = Date.now();

  for (let index = 1; index <= count; index += 1) {
    ctx.checkStop();
    const label = `idle-${index}`;
    const session = ctx.newSession(label, { quiet: true });
    let launched = false;
    let lastError = "";

    for (let attempt = 1; attempt <= attempts && !launched; attempt += 1) {
      try {
        await session.connect();
        await session.login();
        launched = true;
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error);
        await ctx.sleep(120);
      }
    }

    if (launched) {
      bots.push({ label, session });
      ctx.ok(
        `Bot ${index}/${count} parked on the server ` +
          `(${ctx.server.users.size}/${ctx.server.settings.maxUsers} user slots used)`,
      );
    } else {
      refused += 1;
      ctx.warn(`Bot ${index}/${count} could not log in: ${lastError}`);
      await ctx.closeSession(session);
    }

    if (index < count) await ctx.sleep(startDelayMs);
  }

  ctx.result("Bots parked", `${bots.length}/${count}`);
  ctx.result("Refused", refused);
  ctx.result("Server slots used", `${ctx.server.users.size}/${ctx.server.settings.maxUsers}`);

  if (bots.length === 0) {
    ctx.error("No bots could be launched; nothing to keep alive.");
    return;
  }

  ctx.sys(`${bots.length} bot(s) parked. Watching for kicks — press Stop to end the run.`);

  const reported = new Set<string>();
  while (!ctx.stopped) {
    await ctx.sleep(1000);
    for (const bot of bots) {
      const alive = await bot.session.watchdog();
      if (!alive && !reported.has(bot.label)) {
        reported.add(bot.label);
        ctx.warn(`${bot.label} is offline and could not recover yet`);
      } else if (alive && reported.has(bot.label)) {
        reported.delete(bot.label);
        ctx.ok(`${bot.label} recovered and is parked again`);
      }
    }
  }

  const recoveries = bots.reduce((total, bot) => total + bot.session.recoveries, 0);
  ctx.result("Reconnect recoveries", recoveries);
  ctx.result("Elapsed", formatSeconds(Date.now() - started));
  ctx.ok(`Stopped: ${bots.length} idle bot(s) released after ${recoveries} recovery(ies).`);
}

/**
 * Port of `ttbot_the_offender.py`, minus the offensive behaviour: replies only
 * to an explicit trigger, only for allowlisted users, with a per-user cooldown
 * keyed on the username, and a response cap.
 */
export async function runResponseBot(ctx: RunContext): Promise<void> {
  const trigger = str(ctx.values, "trigger", "!hello").trim();
  const template = str(ctx.values, "response", "Hi {username}, thanks for your message!");
  const allowAll = bool(ctx.values, "allow_all");
  const allowList = parseUserList(str(ctx.values, "allow_users")).map((name) => name.toLowerCase());
  const cooldownSec = Math.max(5, num(ctx.values, "cooldown", 30));
  const maxResponses = Math.max(0, int(ctx.values, "max_responses", 100));

  if (!trigger) throw new TeamTalkConfigError("The trigger prefix cannot be empty.");
  if (!allowAll && allowList.length === 0) {
    throw new TeamTalkConfigError(
      "The response bot needs an explicit allowlist (usernames) or Allow any user enabled.",
    );
  }

  const session = ctx.newSession("response-bot");
  await session.connect();
  await session.login();

  const channelId = session.currentChannelId;
  const path =
    channelId === null
      ? "/ (not in a channel)"
      : (await session.channels()).find((channel) => channel.id === channelId)?.path ?? "channel";

  ctx.sys(`Listening in ${path} for messages starting with '${trigger}'.`);
  ctx.info(
    allowAll
      ? "Allowlist: any user"
      : `Allowlist: ${allowList.join(", ")} (matched on username, or nickname when there is none)`,
  );
  ctx.info(`Per-user cooldown: ${cooldownSec} s; response cap: ${maxResponses || "unlimited"}`);

  const lastReply = new Map<string, number>();
  const started = Date.now();
  let replies = 0;
  let ignoredNotAllowed = 0;
  let ignoredCooldown = 0;

  while (!ctx.stopped) {
    const event = await session.nextText(600);
    if (!event) continue;
    if (event.fromUserId === session.currentUserId) continue;

    // Identity is the account name, falling back to the nickname for an
    // anonymous login — the same rule the desktop tools use.
    const username = event.fromUsername || event.fromNickname || `user ${event.fromUserId}`;
    const lower = username.toLowerCase();
    const text = event.text.trim();

    if (!text.toLowerCase().startsWith(trigger.toLowerCase())) {
      continue;
    }
    if (!allowAll && !allowList.includes(lower)) {
      ignoredNotAllowed += 1;
      ctx.warn(`Ignoring trigger from '${username}': not on the allowlist`);
      continue;
    }
    const cooldownUntil = (lastReply.get(lower) ?? 0) + cooldownSec * 1000;
    if (simNow() < cooldownUntil) {
      ignoredCooldown += 1;
      ctx.info(
        `Ignoring trigger from '${username}': ${formatSeconds(cooldownUntil - simNow())} of cooldown left`,
      );
      continue;
    }
    if (maxResponses > 0 && replies >= maxResponses) {
      ctx.ok(`Response cap of ${maxResponses} reached; leaving the channel alone.`);
      break;
    }

    const reply = template
      .replace(/\{username\}/g, username)
      .replace(/\{user_id\}/g, String(event.fromUserId))
      .replace(/\{message\}/g, text);

    await retry(ctx, `reply to '${username}'`, async () => {
      await session.sendChannelMessage(reply);
    });
    lastReply.set(lower, simNow());
    replies += 1;
    ctx.ok(`Replied to '${username}' (${replies}/${maxResponses || "∞"}): ${reply}`);
  }

  ctx.result("Replies sent", replies);
  ctx.result("Ignored (not on allowlist)", ignoredNotAllowed);
  ctx.result("Ignored (cooldown)", ignoredCooldown);
  ctx.result("Elapsed", formatSeconds(Date.now() - started));
  ctx.ok(`Done: ${replies} replied, ${ignoredNotAllowed} refused, ${ignoredCooldown} cooled down.`);

  await session.disconnect();
}
