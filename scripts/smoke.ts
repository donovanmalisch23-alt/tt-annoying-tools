/**
 * Headless smoke check for the web panel's tool logic.
 *
 * It runs every ported tool end to end against the in-tab simulator, with the
 * time scale compressed, and asserts the headline counters. This is what makes
 * "the tools work" a checked claim instead of an assumption:
 *
 *   bun scripts/smoke.ts
 *
 * Exit code 1 means at least one scenario failed.
 */
import "./browser-shim";
import { logbus } from "../src/app/core/logbus";
import { defaultValues, toolSpec } from "../src/app/core/registry";
import type { ConnectionConfig } from "../src/app/core/types";
import { DEFAULT_CONFIG } from "../src/app/core/types";
import { setTimeScale } from "../src/app/sim/clock";
import { server } from "../src/app/sim/instance";
import { SimSession } from "../src/app/sim/session";
import { runTool } from "../src/app/tools";

setTimeScale(30);

interface Outcome {
  ok: boolean;
  results: Array<{ label: string; value: string }>;
  error?: string;
}

let failures = 0;
let checks = 0;

function banner(text: string): void {
  console.log(`\n=== ${text} ===`);
}

function actual(outcome: Outcome, label: string): string | undefined {
  return outcome.results.find((result) => result.label === label)?.value;
}

function check(description: string, condition: boolean, detail = ""): void {
  checks += 1;
  if (condition) {
    console.log(`  PASS  ${description}${detail ? ` (${detail})` : ""}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${description}${detail ? ` (${detail})` : ""}`);
  }
}

function start(
  id: string,
  overrides: Record<string, string> = {},
  config: ConnectionConfig = DEFAULT_CONFIG,
  whitelist: string[] = ["127.0.0.1"],
): { handle: { stopped: boolean; stop(): void }; done: Promise<Outcome> } {
  const spec = toolSpec(id);
  const values = { ...defaultValues(id), ...overrides };
  const handle = {
    stopped: false,
    stop(): void {
      handle.stopped = true;
    },
  };
  const results: Array<{ label: string; value: string }> = [];
  const done = runTool({ spec, values, config, server, whitelist, handle, results }).then(
    (finalResults): Outcome => ({ ok: true, results: [...finalResults] }),
    (error: unknown): Outcome => ({
      ok: false,
      // The shared array still holds whatever the tool recorded before it stopped.
      results: [...results],
      error: error instanceof Error ? error.message : String(error),
    }),
  );
  return { handle, done };
}

async function scenario(title: string, body: () => Promise<void>): Promise<void> {
  banner(title);
  server.reset();
  // Capture the last line id, not the length: the bus is capped, so a length
  // would stop being a valid offset once the buffer fills.
  const logMark = logbus.getLines().at(-1)?.id ?? 0;
  logbus.sys(`--- smoke: ${title} ---`);
  const failuresBefore = failures;
  try {
    await body();
  } catch (error) {
    failures += 1;
    console.log(`  FAIL  scenario threw: ${error instanceof Error ? error.message : error}`);
  }
  if (failures > failuresBefore) {
    console.log("  --- run log for this scenario ---");
    for (const line of logbus.getLines().filter((candidate) => candidate.id > logMark)) {
      console.log(`  [${line.level}] ${line.text}`);
    }
  }
}

const sleep = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms));

async function main(): Promise<void> {
  await scenario("message sender — channel sequence", async () => {
    const { done } = start("message-sender", { count: "3", interval_ms: "5", message: "smoke" });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("delivered exactly 3 channel messages", actual(outcome, "Messages delivered") === "3", String(actual(outcome, "Messages delivered")));
  });

  await scenario("message sender — private sequence", async () => {
    const { done } = start("message-sender", {
      target: "private",
      users: "amy,carol",
      count: "2",
      interval_ms: "5",
      message: "smoke",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("delivered 4 private messages", actual(outcome, "Messages delivered") === "4", String(actual(outcome, "Messages delivered")));
    check("2 recipients", actual(outcome, "Recipients") === "2");
  });

  await scenario("login/logout cycles", async () => {
    const { done } = start("login-cycles", { cycles: "3", interval_ms: "5" });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("3 of 3 cycles", actual(outcome, "Completed cycles") === "3/3", String(actual(outcome, "Completed cycles")));
  });

  await scenario("channel leave/join cycles", async () => {
    const { done } = start("leave-join", { cycles: "3", interval_ms: "5", wait: "0" });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("3 of 3 cycles", actual(outcome, "Completed cycles") === "3/3", String(actual(outcome, "Completed cycles")));
    check("worked in /Lobby", actual(outcome, "Channel") === "/Lobby", String(actual(outcome, "Channel")));
  });

  await scenario("response bot — one allowlisted trigger", async () => {
    const bot = start("response-bot", {
      trigger: "!hello",
      response: "Hi {username}!",
      allow_users: "dave",
      allow_all: "false",
      cooldown: "5",
      max_responses: "1",
    });
    const timer = setTimeout(() => bot.handle.stop(), 6000);
    await sleep(200);

    const peerConfig = { ...DEFAULT_CONFIG, username: "dave", nickname: "Dave" };
    const peer = new SimSession(server, peerConfig, { label: "smoke-peer", quiet: true });
    await peer.connect();
    await peer.login();
    for (let attempt = 0; attempt < 20; attempt += 1) {
      await peer.sendChannelMessage("!hello there");
      await sleep(100);
      if (logbus.getLines().some((line) => line.text.includes("Replied to"))) break;
    }
    const outcome = await bot.done;
    clearTimeout(timer);
    await peer.disconnect();
    check("run completed", outcome.ok, outcome.error);
    check("replied once", actual(outcome, "Replies sent") === "1", String(actual(outcome, "Replies sent")));
    check(
      "identified the sender by username",
      logbus.getLines().some((line) => line.text.includes("Replied to 'dave'")),
    );
  });

  await scenario("idle bots — park and release", async () => {
    const idle = start("idle-bots", { count: "3", start_delay_ms: "5" });
    // The bots park quickly; poll instead of guessing a fixed delay.
    const deadline = Date.now() + 5000;
    let parked = server.users.size;
    while (Date.now() < deadline && parked < 6) {
      await sleep(50);
      parked = server.users.size;
    }
    idle.handle.stop();
    const outcome = await idle.done;
    check("stopping the run is a clean cancel", !outcome.ok && outcome.error === "Run cancelled", outcome.error);
    check("3 bots parked", actual(outcome, "Bots parked") === "3/3", String(actual(outcome, "Bots parked")));
    check("server roster grew", parked >= 6, `${parked} users online`);
  });

  await scenario("combined suite — dry run", async () => {
    const { done } = start("suite", {
      dry_run: "true",
      all_channels: "true",
      all_users: "true",
      private_message: "hi",
      channel_message: "hi",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("discovered channels", Number(actual(outcome, "Channels discovered") ?? 0) >= 5, String(actual(outcome, "Channels discovered")));
    check("discovered users", Number(actual(outcome, "Users discovered") ?? 0) >= 3, String(actual(outcome, "Users discovered")));
  });

  await scenario("combined suite — sequential", async () => {
    const { done } = start("suite", {
      all_channels: "true",
      all_users: "true",
      channel_message: "smoke",
      private_message: "smoke",
      message_count: "1",
      join_leave_cycles: "1",
      login_cycles: "1",
      interval: "0",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("channel messages sent", Number(actual(outcome, "Channel messages") ?? 0) >= 4, String(actual(outcome, "Channel messages")));
    check("private messages sent", Number(actual(outcome, "Private messages") ?? 0) >= 3, String(actual(outcome, "Private messages")));
    check("login cycle completed", actual(outcome, "Login cycles") === "1", String(actual(outcome, "Login cycles")));
    check(
      "password-protected channel was skipped, not fatal",
      logbus.getLines().some((line) => line.text.includes("/Ops") && line.text.includes("Skipping")),
    );
  });

  await scenario("combined suite — concurrent bots", async () => {
    const { done } = start("suite", {
      concurrent: "true",
      all_channels: "true",
      all_users: "true",
      bot_per_user: "true",
      channel_message: "smoke",
      private_message: "smoke",
      message_count: "1",
      churn_bots: "2",
      churn_cycles: "2",
      interval: "0",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("channel messages sent", Number(actual(outcome, "Channel messages") ?? 0) >= 4, String(actual(outcome, "Channel messages")));
    check("private messages sent", Number(actual(outcome, "Private messages") ?? 0) >= 3, String(actual(outcome, "Private messages")));
    check("churn bots completed cycles", Number(actual(outcome, "Login cycles") ?? 0) >= 4, String(actual(outcome, "Login cycles")));
  });

  await scenario("combined suite — continuous new-joiner sweep", async () => {
    const suite = start("suite", {
      concurrent: "true",
      all_users: "true",
      private_message: "welcome",
      message_count: "1",
      interval: "0",
      sweep_interval: "0.2",
      churn_bots: "0",
    });
    await sleep(400);
    const late = server.spawnUser();
    await sleep(800);
    suite.handle.stop();
    const outcome = await suite.done;
    check("stopping the run is a clean cancel", !outcome.ok && outcome.error === "Run cancelled", outcome.error);
    const sent = Number(actual(outcome, "Private messages") ?? 0);
    check("messaged the seeded users", sent >= 3, `${sent} private messages`);
    check("late joiner was picked up", sent >= 4, `${sent} private messages (late joiner: ${late?.username ?? "none"})`);
  });

  await scenario("local flood test — with probes", async () => {
    const { done } = start("loic", { mode: "both", threads: "8", duration: "2", probe: "true" });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("flood duration reported", actual(outcome, "Flood duration") === "2 s", String(actual(outcome, "Flood duration")));
    check(
      "a verdict was reached",
      logbus.getLines().some((line) => line.text.startsWith("Verdict:")),
    );
  });

  await scenario("flood test — remote target is refused", async () => {
    const { done } = start("loic", { threads: "1", duration: "1" }, { ...DEFAULT_CONFIG, host: "example.org" });
    const outcome = await done;
    check("refused, as the CLI does", !outcome.ok, outcome.error);
    check("refusal mentions local-only", (outcome.error ?? "").includes("local-only"), outcome.error);
  });

  await scenario("ramp — healthy ceiling", async () => {
    const { done } = start("ramp", {
      start_threads: "1",
      ramp_factor: "2",
      max_threads: "4",
      stage_duration: "1",
      mode: "both",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    check("no breaking point below 4 threads", actual(outcome, "Breaking point") === "not reached", String(actual(outcome, "Breaking point")));
  });

  await scenario("ramp — finds the breaking point", async () => {
    const { done } = start("ramp", {
      start_threads: "1",
      ramp_factor: "2",
      max_threads: "64",
      stage_duration: "1",
      mode: "both",
    });
    const outcome = await done;
    check("run completed", outcome.ok, outcome.error);
    const breaking = actual(outcome, "Breaking point") ?? "";
    check("a breaking point was found", breaking.endsWith("threads"), breaking);
    check(
      "the simulator was left unloaded",
      server.floodThreads === 0,
      `${server.floodThreads} threads`,
    );
  });

  await scenario("kick resistance — exact counts through kicks", async () => {
    server.settings.protectionBurst = 2;
    server.settings.protectionWindowMs = 60000;
    const mark = logbus.getLines().length;
    const { done } = start("message-sender", { count: "4", interval_ms: "5", message: "smoke" });
    const outcome = await done;
    const recoveries = logbus
      .getLines()
      .slice(mark)
      .filter((line) => line.text.includes("recovering (attempt")).length;
    check("run completed despite being kicked", outcome.ok, outcome.error);
    check("exactly 4 messages delivered (no duplicates)", actual(outcome, "Messages delivered") === "4", String(actual(outcome, "Messages delivered")));
    check("kick resistance actually recovered", recoveries > 0, `${recoveries} recovery(ies)`);
  });

  banner("summary");
  console.log(`  ${checks - failures}/${checks} checks passed`);
  if (failures > 0) {
    console.log(`  ${failures} failing check(s)`);
    process.exit(1);
  }
  console.log("  all scenarios passed");
}

await main();
