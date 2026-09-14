import { bool, int, num, parseUserList, str } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import type { ChannelInfo, UserInfo } from "../core/types";
import type { SimSession } from "../sim/session";
import type { RunContext } from "./context";
import { formatSeconds, retry } from "./context";
import { leaveJoinCycles, loginLogoutCycles, sendChannelSequence, sendPrivateSequence } from "./ops";

/** Concurrent bots share one JS process in the browser, so they are capped. */
export const SUITE_BOT_MAX = 64;

/**
 * Port of `tt_suite.py`: discover the channels and users, then run login
 * cycles, join/leave cycles, channel messages and private messages —
 * sequentially, or split across dedicated bots in concurrent mode.
 */
export async function runSuite(ctx: RunContext): Promise<void> {
  const allChannels = bool(ctx.values, "all_channels");
  const allUsers = bool(ctx.values, "all_users");
  const requestedUsers = parseUserList(str(ctx.values, "users"));
  const channelMessage = str(ctx.values, "channel_message");
  const privateMessage = str(ctx.values, "private_message");
  const messageCount = Math.max(1, int(ctx.values, "message_count", 1));
  const joinLeave = Math.max(0, int(ctx.values, "join_leave_cycles", 0));
  const loginCycles = Math.max(0, int(ctx.values, "login_cycles", 0));
  const intervalSec = Math.max(0, num(ctx.values, "interval", 0.2));
  const concurrent = bool(ctx.values, "concurrent");
  const churnBots = Math.max(0, int(ctx.values, "churn_bots", 0));
  const churnCycles = Math.max(1, int(ctx.values, "churn_cycles", 10));
  const botPerChannel = bool(ctx.values, "bot_per_channel");
  const botPerUser = bool(ctx.values, "bot_per_user");
  const sweepSec = Math.max(0.1, num(ctx.values, "sweep_interval", 0.5));
  const dryRun = bool(ctx.values, "dry_run");
  const intervalMs = intervalSec * 1000;

  if (!channelMessage && !privateMessage && joinLeave === 0 && loginCycles === 0) {
    throw new TeamTalkConfigError(
      "Nothing to do: set a channel message, a private message, join/leave cycles or login cycles.",
    );
  }
  if ((botPerChannel || botPerUser) && !concurrent) {
    throw new TeamTalkConfigError("One bot per channel/user requires Concurrent bots.");
  }
  if (botPerUser && allUsers) {
    ctx.info("One user-bot per user snapshots the users online right now (no continuous mode).");
  }

  // ----- discovery -------------------------------------------------------- //

  ctx.sys(`Discovering channels and users on ${ctx.config.host}:${ctx.config.tcpPort}`);
  const discovery = ctx.newSession("discovery");
  await discovery.connect();
  await discovery.login();

  const channels = await retry(ctx, "channel discovery", async () => discovery.channels());
  const roster = await retry(ctx, "user discovery", async () => discovery.users());
  const ownUsername = ctx.config.username.trim().toLowerCase();

  const channelTargets: ChannelInfo[] = allChannels
    ? channels
    : channels.filter((channel) => channel.path === ctx.config.channelPath);

  const eligibleUsers: UserInfo[] = roster.filter(
    (user) => user.username && user.username.toLowerCase() !== ownUsername,
  );
  const userNames = allUsers
    ? Array.from(new Set(eligibleUsers.map((user) => user.username)))
    : requestedUsers;

  ctx.info(`Channels (${channels.length}):`);
  for (const channel of channels) {
    ctx.info(
      `  ${channel.path}  #${channel.id}` +
        (channel.passwordRequired ? "  [password required]" : "") +
        (channel.hidden ? "  [hidden]" : ""),
    );
  }
  ctx.info(`Users online (${eligibleUsers.length}):`);
  for (const user of eligibleUsers) {
    ctx.info(
      `  ${user.nickname}${user.nickname === user.username ? "" : ` (@${user.username})`} — ${user.channelPath}`,
    );
  }
  ctx.info(
    `Selected ${channelTargets.length} channel(s) for channel work` +
      (privateMessage ? ` and ${userNames.length} user(s) for private messages` : ""),
  );

  if (dryRun) {
    ctx.ok("Dry run: discovery only, nothing was sent and no channel was joined.");
    ctx.result("Channels discovered", channels.length);
    ctx.result("Users discovered", eligibleUsers.length);
    ctx.result("Channel targets", channelTargets.length);
    ctx.result("User targets", privateMessage ? userNames.length : 0);
    await discovery.disconnect();
    return;
  }

  // ----- bot plan --------------------------------------------------------- //

  const channelBots = concurrent && channelMessage ? (botPerChannel ? Math.max(1, channelTargets.length) : 1) : 0;
  const userBots = concurrent && privateMessage ? (botPerUser ? Math.max(1, userNames.length) : 1) : 0;
  const plannedBots = channelBots + userBots + churnBots;
  if (concurrent && plannedBots > SUITE_BOT_MAX) {
    throw new TeamTalkConfigError(
      `Concurrent mode planned ${plannedBots} bots, over this panel's cap of ${SUITE_BOT_MAX} ` +
        "connections in one tab. Reduce churn bots or target counts.",
    );
  }

  const started = Date.now();
  const totals = { channelMessages: 0, privateMessages: 0, joinLeaveCycles: 0, loginCycles: 0 };

  if (concurrent) {
    ctx.sys(
      `Concurrent mode: ${channelBots} channel-bot(s), ${userBots} user-bot(s), ` +
        `${churnBots} churn-bot(s) — each on its own connection.`,
    );
    if (loginCycles > 0) {
      ctx.info("Concurrent mode runs login churn through the churn bots; Login cycles is ignored.");
    }

    const tasks: Array<Promise<void>> = [];

    if (channelMessage && channelBots > 0) {
      const groups = botPerChannel
        ? channelTargets.map((channel) => [channel])
        : channelTargets.length > 0
          ? [channelTargets]
          : [];
      groups.forEach((group, index) => {
        tasks.push(
          runBot(ctx, `chan-bot-${index + 1}`, async (session) => {
            for (const channel of group) {
              try {
                await retry(ctx, `join ${channel.path}`, async () => {
                  await session.joinById(channel.id);
                });
              } catch (error) {
                ctx.warn(`Skipping ${channel.path}: ${message(error)}`);
                continue;
              }
              const sent = await sendChannelSequence(
                ctx,
                session,
                channel.id,
                channelMessage,
                messageCount,
                intervalMs,
              );
              totals.channelMessages += sent;
              ctx.result("Channel messages", totals.channelMessages);
            }
          }),
        );
      });
    }

    if (privateMessage && userBots > 0) {
      if (botPerUser) {
        userNames.forEach((username, index) => {
          tasks.push(
            runBot(ctx, `user-bot-${index + 1}`, async (session) => {
              // Note: read the total only after the await. `x += await f()` would
              // capture the old value first and lose concurrent updates.
              const sent = await sendPrivateSequence(
                ctx,
                session,
                username,
                privateMessage,
                messageCount,
                intervalMs,
              );
              totals.privateMessages += sent;
              ctx.result("Private messages", totals.privateMessages);
            }),
          );
        });
      } else {
        tasks.push(
          runBot(ctx, "user-bot", async (session) => {
            const messaged = new Set<string>();
            const continuous = allUsers && !botPerUser;
            do {
              const live = (
                await retry(ctx, "user-bot user sweep", async () => session.users())
              ).filter((user) => user.username && user.username.toLowerCase() !== ownUsername);
              for (const user of live) {
                if (messaged.has(user.username)) continue;
                messaged.add(user.username);
                const sent = await sendPrivateSequence(
                  ctx,
                  session,
                  user.username,
                  privateMessage,
                  messageCount,
                  intervalMs,
                );
                totals.privateMessages += sent;
              }
              ctx.result("Private messages", totals.privateMessages);
              ctx.result("Users messaged", messaged.size);
              if (!continuous) break;
              ctx.info(
                `${messaged.size} user(s) messaged so far; re-checking in ${sweepSec}s for new joiners.`,
              );
              await ctx.sleep(sweepSec * 1000);
            } while (!ctx.stopped);
            if (continuous) ctx.ok(`Continuous user-bot stopped after ${messaged.size} user(s).`);
          }),
        );
      }
    }

    for (let index = 1; index <= churnBots; index += 1) {
      tasks.push(
        runBot(ctx, `churn-${index}`, async (session) => {
          const cycles = await loginLogoutCycles(ctx, session, churnCycles, intervalMs);
          totals.loginCycles += cycles;
          ctx.result("Login cycles", totals.loginCycles);
        }),
      );
    }

    await Promise.all(tasks);
  } else {
    ctx.sys("Sequential mode: one session at a time, closing each before the next starts.");

    if (loginCycles > 0) {
      await runBot(ctx, "login-bot", async (session) => {
        const cycles = await loginLogoutCycles(ctx, session, loginCycles, intervalMs);
        totals.loginCycles += cycles;
      });
    }

    if (joinLeave > 0) {
      await runBot(ctx, "leavejoin-bot", async (session) => {
        const channelId = session.currentChannelId;
        if (channelId === null) {
          throw new TeamTalkConfigError("Set a channel in Server before join/leave cycles.");
        }
        const cycles = await leaveJoinCycles(ctx, session, joinLeave, intervalMs);
        totals.joinLeaveCycles += cycles;
      });
    }

    if (channelMessage) {
      for (const channel of channelTargets) {
        try {
          await runBot(ctx, `chan-${channel.id}`, async (session) => {
            await session.joinById(channel.id);
            const sent = await sendChannelSequence(
              ctx,
              session,
              channel.id,
              channelMessage,
              messageCount,
              intervalMs,
            );
            totals.channelMessages += sent;
            ctx.result("Channel messages", totals.channelMessages);
          });
        } catch (error) {
          ctx.warn(`Skipping ${channel.path}: ${message(error)}`);
        }
      }
    }

    if (privateMessage) {
      await runBot(ctx, "user-bot", async (session) => {
        for (const username of userNames) {
          ctx.checkStop();
          const sent = await sendPrivateSequence(
            ctx,
            session,
            username,
            privateMessage,
            messageCount,
            intervalMs,
          );
          totals.privateMessages += sent;
          ctx.result("Private messages", totals.privateMessages);
        }
      });
    }
  }

  const elapsed = Date.now() - started;
  ctx.result("Channel messages", totals.channelMessages);
  ctx.result("Private messages", totals.privateMessages);
  if (joinLeave > 0) ctx.result("Join/leave cycles", totals.joinLeaveCycles);
  if (loginCycles > 0 || churnBots > 0) ctx.result("Login cycles", totals.loginCycles);
  ctx.result("Elapsed", formatSeconds(elapsed));
  ctx.ok(
    `Suite complete: ${totals.channelMessages} channel message(s), ` +
      `${totals.privateMessages} private message(s) in ${formatSeconds(elapsed)}.`,
  );

  await discovery.disconnect();
}

/** Opens a session, logs in, runs the body, and always releases the session. */
async function runBot(
  ctx: RunContext,
  label: string,
  body: (session: SimSession) => Promise<void>,
): Promise<void> {
  const session = ctx.newSession(label, { quiet: true });
  try {
    await retry(ctx, `${label} connect/login`, async () => {
      await session.connect();
      await session.login();
    });
    await body(session);
  } finally {
    await ctx.closeSession(session);
  }
}

function message(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}
