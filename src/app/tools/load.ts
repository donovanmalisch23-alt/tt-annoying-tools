import { bool, int, num, str } from "../core/registry";
import { TeamTalkConfigError } from "../core/types";
import type { SimSession } from "../sim/session";
import type { RunContext } from "./context";
import { formatSeconds, isLocalTarget } from "./context";

export const FLOOD_THREAD_MAX = 64;
export const RAMP_THREAD_MAX = 1024;
export const FLOOD_DURATION_MAX = 60;

export type FloodMode = "both" | "tcp" | "udp";

interface ProbeResult {
  ok: boolean;
  connectMs: number;
  loginMs: number;
  roundTripMs: number;
  detail: string;
}

let floodSeq = 0;

/**
 * The sender+receiver service probe used by both load tools: a fresh TCP
 * "connect", a login, and a channel-message round trip. A TeamTalk server
 * relays a channel message to the *other* users in the channel and never back
 * to its sender, which is why the probe logs in twice.
 */
async function probe(ctx: RunContext, channelPath: string, label: string): Promise<ProbeResult> {
  const result: ProbeResult = {
    ok: false,
    connectMs: 0,
    loginMs: 0,
    roundTripMs: 0,
    detail: "not started",
  };
  let receiver: SimSession | undefined;
  let sender: SimSession | undefined;
  try {
    receiver = ctx.newSession(`probe-${label}-rx`, { quiet: true });
    sender = ctx.newSession(`probe-${label}-tx`, { quiet: true });
  } catch (error) {
    result.detail = error instanceof Error ? error.message : String(error);
    return result;
  }

  try {
    const connectStart = Date.now();
    await receiver.connect();
    await sender.connect();
    result.connectMs = Date.now() - connectStart;

    const loginStart = Date.now();
    await receiver.login();
    await sender.login();
    result.loginMs = Date.now() - loginStart;

    let channel = channelPath;
    try {
      await receiver.joinByPath(channel);
      await sender.joinByPath(channel);
    } catch {
      ctx.warn(`Probe channel '${channel}' is not usable; measuring from the root channel instead.`);
      channel = "/";
      await receiver.leaveChannel();
      await sender.leaveChannel();
      await receiver.joinByPath(channel);
      await sender.joinByPath(channel);
    }

    receiver.drainText();
    const roundStart = Date.now();
    await sender.sendChannelMessage(`probe ${Math.random().toString(36).slice(2, 8)}`);
    const event = await receiver.nextText(4000);
    result.roundTripMs = Date.now() - roundStart;
    result.ok = event !== null;
    result.detail = event
      ? `relayed in ${result.roundTripMs} ms`
      : "the message never arrived within 4 s";
  } catch (error) {
    result.detail = error instanceof Error ? error.message : String(error);
  } finally {
    if (receiver) await ctx.closeSession(receiver);
    if (sender) await ctx.closeSession(sender);
  }
  return result;
}

function logProbe(ctx: RunContext, label: string, result: ProbeResult): void {
  if (result.ok) {
    ctx.info(
      `Probe ${label}: connect ${result.connectMs} ms, login ${result.loginMs} ms, ` +
        `round trip ${result.roundTripMs} ms (${result.detail})`,
    );
  } else {
    ctx.warn(`Probe ${label}: failed — ${result.detail}`);
  }
}

const MODE_LABEL: Record<FloodMode, string> = {
  both: "TCP+UDP",
  tcp: "TCP",
  udp: "UDP",
};

function readMode(ctx: RunContext): FloodMode {
  const raw = str(ctx.values, "mode", "both").toLowerCase();
  if (raw === "tcp" || raw === "udp" || raw === "both") return raw;
  throw new TeamTalkConfigError(`Unknown flood mode '${raw}'.`);
}

/** A fresh load-session id. Registering the same id again replaces its threads. */
function newFloodId(): string {
  return `flood-${++floodSeq}`;
}

/** Registers simulated junk-load threads so the server's load curve responds. */
function startFlood(ctx: RunContext, threads: number): string {
  const id = newFloodId();
  ctx.server.registerFlood(id, threads);
  return id;
}

/**
 * Port of `tt_loic.py`: junk-load threads plus before/during/after service
 * probes and a plain-language verdict. Local-machine targets only.
 */
export async function runLocalFlood(ctx: RunContext): Promise<void> {
  const host = ctx.config.host;
  const mode = readMode(ctx);
  const threads = int(ctx.values, "threads", 8);
  const durationSec = num(ctx.values, "duration", 10);
  const probesEnabled = bool(ctx.values, "probe");
  const probeChannel = str(ctx.values, "probe_channel", "/Lobby");

  if (!isLocalTarget(host)) {
    throw new TeamTalkConfigError(
      `This flood test is local-only: '${host}' is not an address on this machine. ` +
        "Point it at a server you run locally.",
    );
  }
  if (threads < 1 || threads > FLOOD_THREAD_MAX) {
    throw new TeamTalkConfigError(`Threads per mode must be 1 to ${FLOOD_THREAD_MAX}.`);
  }
  if (durationSec < 1 || durationSec > FLOOD_DURATION_MAX) {
    throw new TeamTalkConfigError(`Duration must be 1 to ${FLOOD_DURATION_MAX} seconds.`);
  }

  ctx.sys(
    `${threads} ${MODE_LABEL[mode]} junk thread(s) per mode for ${durationSec} s against ` +
      `${host}:${ctx.config.tcpPort} (UDP ${ctx.config.udpPort})`,
  );
  ctx.info("Local-only gate passed: the target is an address on this machine.");

  const baseline = probesEnabled ? await probe(ctx, probeChannel, "before") : null;
  if (baseline) logProbe(ctx, "before", baseline);

  let floodId: string | null = null;
  let worst: ProbeResult | null = null;
  const started = Date.now();
  try {
    floodId = startFlood(ctx, threads * (mode === "both" ? 2 : 1));
    ctx.ok("Flood started.");

    let elapsedSec = 0;
    while (elapsedSec < durationSec) {
      const slice = Math.min(2, durationSec - elapsedSec);
      await ctx.sleep(slice * 1000);
      elapsedSec += slice;
      ctx.info(`Flood running: ${elapsedSec.toFixed(1)}/${durationSec} s`);
      // Probe on every slice, including a short final one, so even a 1-second
      // flood produces a during-the-flood measurement.
      if (probesEnabled) {
        const during = await probe(ctx, probeChannel, "during");
        logProbe(ctx, "during", during);
        if (!worst || (!during.ok && worst.ok) || during.roundTripMs > worst.roundTripMs) {
          worst = during;
        }
      }
    }
  } finally {
    if (floodId) ctx.server.unregisterFlood(floodId);
  }
  ctx.ok("Flood stopped; load released.");

  await ctx.sleep(1000);
  const after = probesEnabled ? await probe(ctx, probeChannel, "after") : null;
  if (after) logProbe(ctx, "after", after);

  const elapsed = Date.now() - started;
  ctx.result("Threads", threads);
  ctx.result("Mode", MODE_LABEL[mode]);
  ctx.result("Flood duration", `${durationSec} s`);
  if (baseline) ctx.result("Baseline round trip", `${baseline.roundTripMs} ms`);
  if (worst) ctx.result("Worst round trip", worst.ok ? `${worst.roundTripMs} ms` : "failed");
  if (after) ctx.result("After round trip", after.ok ? `${after.roundTripMs} ms` : "failed");

  if (!probesEnabled) {
    ctx.info("Probes were disabled, so no verdict about service impact is available.");
  } else if (!baseline || !baseline.ok) {
    ctx.warn("The baseline probe failed before the flood, so the impact cannot be attributed.");
  } else if (worst && !worst.ok) {
    ctx.error("Verdict: the flood knocked the probe offline — the server did not cope.");
  } else if (worst && worst.roundTripMs >= baseline.roundTripMs * 2) {
    ctx.warn(
      `Verdict: the server degraded under load (${baseline.roundTripMs} ms → ` +
        `${worst.roundTripMs} ms, ${(worst.roundTripMs / Math.max(1, baseline.roundTripMs)).toFixed(1)}× slower).`,
    );
  } else if (worst) {
    ctx.ok(
      `Verdict: the server coped — ${baseline.roundTripMs} ms → ${worst.roundTripMs} ms round trip.`,
    );
  }
  ctx.info(`Test finished in ${formatSeconds(elapsed)}.`);
}

/**
 * Port of `tt_ramp.py`: geometric load stages, each classified healthy,
 * degraded or broken, stopping at the first broken stage to report the
 * breaking point. Keeps every `tt_loic` gate except local-only, because the
 * allowlist is the authorization gate for a remote host.
 */
export async function runRamp(ctx: RunContext): Promise<void> {
  const mode = readMode(ctx);
  const startThreads = int(ctx.values, "start_threads", 1);
  const factor = int(ctx.values, "ramp_factor", 2);
  const maxThreads = int(ctx.values, "max_threads", 64);
  const stageSec = num(ctx.values, "stage_duration", 6);
  const probeChannel = str(ctx.values, "probe_channel", "/Lobby");
  const dryRun = bool(ctx.values, "dry_run");

  if (startThreads < 1 || startThreads > RAMP_THREAD_MAX) {
    throw new TeamTalkConfigError(`Start threads must be 1 to ${RAMP_THREAD_MAX}.`);
  }
  if (factor < 2) throw new TeamTalkConfigError("Ramp factor must be at least 2.");
  if (maxThreads < startThreads || maxThreads > RAMP_THREAD_MAX) {
    throw new TeamTalkConfigError(
      `Max threads must be at least the start and at most ${RAMP_THREAD_MAX}.`,
    );
  }
  if (stageSec < 1 || stageSec > FLOOD_DURATION_MAX) {
    throw new TeamTalkConfigError(`Seconds per stage must be 1 to ${FLOOD_DURATION_MAX}.`);
  }

  const stages: number[] = [];
  let threads = startThreads;
  while (threads <= maxThreads && stages.length < 20) {
    stages.push(threads);
    threads *= factor;
  }

  ctx.sys(
    `Ramp plan: ${stages.length} stage(s) — ${stages.join(" → ")} ${MODE_LABEL[mode]} thread(s), ` +
      `${stageSec} s each, probing ${probeChannel}.`,
  );

  if (dryRun) {
    ctx.ok("Dry run: the stage plan above was not executed.");
    ctx.result("Stages", stages.length);
    ctx.result("Peak threads", stages[stages.length - 1] ?? 0);
    return;
  }

  const baseline = await probe(ctx, probeChannel, "baseline");
  logProbe(ctx, "baseline", baseline);
  if (!baseline.ok) {
    ctx.warn("The baseline probe failed; classification will be relative to the first stage instead.");
  }

  interface StageResult {
    threads: number;
    verdict: "healthy" | "degraded" | "broken";
    roundTripMs: number;
    detail: string;
  }
  const results: StageResult[] = [];
  // One load session for the whole ramp: each stage replaces its thread count,
  // so the server sees exactly that stage's load and nothing accumulated.
  const floodId = newFloodId();
  ctx.server.registerFlood(floodId, 0);
  let breakingPoint: number | null = null;
  const started = Date.now();

  try {
    for (const stage of stages) {
      ctx.checkStop();
      ctx.server.registerFlood(floodId, stage * (mode === "both" ? 2 : 1));
      ctx.sys(`Stage ${results.length + 1}/${stages.length}: ${stage} thread(s) flooding`);

      let elapsedSec = 0;
      let worst: ProbeResult | null = null;
      while (elapsedSec < stageSec) {
        const slice = Math.min(2, stageSec - elapsedSec);
        await ctx.sleep(slice * 1000);
        elapsedSec += slice;
        const during = await probe(ctx, probeChannel, `stage-${stage}`);
        logProbe(ctx, `stage ${stage} @ ${elapsedSec.toFixed(0)}s`, during);
        if (!worst || (!during.ok && worst.ok) || during.roundTripMs > worst.roundTripMs) {
          worst = during;
        }
      }

      const reference = baseline.ok ? baseline.roundTripMs : Math.max(1, worst?.roundTripMs ?? 1);
      let verdict: StageResult["verdict"] = "healthy";
      if (!worst || !worst.ok) verdict = "broken";
      else if (worst.roundTripMs >= reference * 2) verdict = "degraded";
      results.push({
        threads: stage,
        verdict,
        roundTripMs: worst?.roundTripMs ?? 0,
        detail: worst?.detail ?? "no probe result",
      });

      const stagesLeft = results.length < stages.length;
      if (verdict === "broken") {
        breakingPoint = stage;
        ctx.error(`Stage ${stage} threads: BROKEN — ${worst?.detail ?? "probe failed"}`);
        if (stagesLeft) ctx.warn("Stopping at the first broken stage.");
        break;
      }
      if (verdict === "degraded") {
        ctx.warn(
          `Stage ${stage} threads: DEGRADED — ${worst?.roundTripMs} ms vs ${reference} ms baseline`,
        );
      } else {
        ctx.ok(`Stage ${stage} threads: healthy — ${worst?.roundTripMs} ms round trip`);
      }
    }
  } finally {
    ctx.server.unregisterFlood(floodId);
  }

  ctx.ok("All flood threads released.");
  await ctx.sleep(1000);
  const after = await probe(ctx, probeChannel, "after");
  logProbe(ctx, "after", after);

  const degraded = results.filter((stage) => stage.verdict !== "healthy");
  const highestClean = results.filter((stage) => stage.verdict === "healthy").pop();

  ctx.result("Stages run", results.length);
  ctx.result("Peak threads", results[results.length - 1]?.threads ?? 0);
  ctx.result("Breaking point", breakingPoint === null ? "not reached" : `${breakingPoint} threads`);
  ctx.result("Highest clean load", highestClean ? `${highestClean.threads} threads` : "none");
  ctx.result("Elapsed", formatSeconds(Date.now() - started));

  ctx.sys("Stage summary:");
  for (const stage of results) {
    ctx.info(
      `  ${stage.threads.toString().padStart(4)} threads  ${stage.verdict.toUpperCase().padEnd(9)} ` +
        `${stage.roundTripMs} ms — ${stage.detail}`,
    );
  }
  if (breakingPoint !== null) {
    ctx.error(`BREAKS at ${breakingPoint} threads.`);
  } else {
    ctx.ok(`No breaking point up to ${results[results.length - 1]?.threads ?? 0} threads.`);
  }
  if (degraded.length > 0) {
    ctx.warn(`Degradation first appeared at ${degraded[0].threads} threads.`);
  }
}
