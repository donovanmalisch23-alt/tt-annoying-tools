package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.TeamTalkConfigException
import com.teamtalk.annoying.core.TeamTalkSession
import com.teamtalk.annoying.core.ToolCancelledException
import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.NetworkInterface
import java.net.Socket
import java.security.SecureRandom
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicLong

// ----- constants mirrored from tt_loic.py / tt_ramp.py ---------------------- //

private const val DEFAULT_TCP_PORT = 10333
private const val DEFAULT_UDP_PORT = 10333
private const val MAX_DURATION_SECONDS = 60.0
private const val MAX_THREADS = 64
private const val RAMP_MAX_THREADS = 1024
private const val TCP_CHUNK_BYTES = 1024
private const val UDP_DATAGRAM_BYTES = 512
private const val JUNK_BUFFER_BYTES = 1 shl 16
private const val PROBE_ECHO_TIMEOUT_SEC = 2.0
private const val BASELINE_PROBES = 3
private const val AFTER_PROBES = 2
private const val PROBE_CADENCE_SEC = 1.0
private const val DEGRADATION_LATENCY_RATIO = 2.0

/** Local-target gate: the flood tools refuse anything that is not this device. */
object LocalGate {

    fun localAddresses(): Set<String> {
        val addresses = mutableSetOf("127.0.0.1", "::1", "localhost")
        try {
            val interfaces = NetworkInterface.getNetworkInterfaces() ?: return addresses
            for (nif in interfaces) {
                for (address in nif.inetAddresses) {
                    val text = address.hostAddress?.substringBefore('%') ?: continue
                    addresses.add(text)
                }
            }
        } catch (t: Throwable) {
            // Fall back to loopback only.
        }
        return addresses
    }

    fun requireLocal(host: String) {
        val trimmed = host.trim()
        val resolved = try {
            InetAddress.getAllByName(trimmed)
        } catch (t: Throwable) {
            throw TeamTalkConfigException("Cannot resolve host '$trimmed': ${t.message}")
        }
        if (resolved.isEmpty()) {
            throw TeamTalkConfigException("Host '$trimmed' did not resolve.")
        }
        val local = localAddresses()
        for (address in resolved) {
            val text = address.hostAddress?.substringBefore('%') ?: continue
            if (address.isLoopbackAddress || text in local) return
        }
        throw TeamTalkConfigException(
            "'$trimmed' is not an address on this device. The flood test only targets this " +
                "device; use the ramp test for allowlisted remote servers.",
        )
    }
}

private class FloodStats {
    val connections = AtomicLong()
    val bytesSent = AtomicLong()
    val datagrams = AtomicLong()
    val errors = AtomicLong()
}

private fun tcpFlood(
    host: String,
    port: Int,
    stats: FloodStats,
    running: AtomicBoolean,
    timeoutMs: Int,
) {
    val junk = ByteArray(JUNK_BUFFER_BYTES).also { SecureRandom().nextBytes(it) }
    val address = InetSocketAddress(host, port)
    while (running.get()) {
        try {
            Socket().use { socket ->
                socket.connect(address, timeoutMs)
                socket.soTimeout = timeoutMs
                stats.connections.incrementAndGet()
                var offset = 0
                val out = socket.getOutputStream()
                while (running.get()) {
                    out.write(junk, offset, TCP_CHUNK_BYTES)
                    stats.bytesSent.addAndGet(TCP_CHUNK_BYTES.toLong())
                    offset = (offset + TCP_CHUNK_BYTES) % (JUNK_BUFFER_BYTES - TCP_CHUNK_BYTES)
                }
            }
        } catch (t: Throwable) {
            stats.errors.incrementAndGet()
            if (!running.get()) return
        }
    }
}

private fun udpFlood(
    host: String,
    port: Int,
    stats: FloodStats,
    running: AtomicBoolean,
    timeoutMs: Int,
) {
    val payload = ByteArray(UDP_DATAGRAM_BYTES).also { SecureRandom().nextBytes(it) }
    val target = try {
        InetAddress.getByName(host)
    } catch (t: Throwable) {
        return
    }
    while (running.get()) {
        try {
            DatagramSocket().use { socket ->
                socket.soTimeout = timeoutMs
                while (running.get()) {
                    socket.send(DatagramPacket(payload, payload.size, target, port))
                    stats.datagrams.incrementAndGet()
                }
            }
        } catch (t: Throwable) {
            stats.errors.incrementAndGet()
            if (!running.get()) return
        }
    }
}

private fun runFlood(
    host: String,
    tcpPort: Int,
    udpPort: Int,
    mode: String,
    threads: Int,
    durationSec: Double,
    timeoutMs: Int,
    running: AtomicBoolean,
    stats: MutableMap<String, FloodStats>,
) {
    val workers = mutableListOf<Thread>()
    fun spawn(kind: String, body: (FloodStats) -> Unit) {
        val stat = FloodStats()
        stats[kind] = stat
        repeat(threads) {
            workers += Thread({ body(stat) }, "flood-$kind").apply { isDaemon = true }
        }
    }
    if (mode == "both" || mode == "tcp") {
        spawn("tcp") { tcpFlood(host, tcpPort, it, running, timeoutMs) }
    }
    if (mode == "both" || mode == "udp") {
        spawn("udp") { udpFlood(host, udpPort, it, running, timeoutMs) }
    }
    workers.forEach { it.start() }
    val deadline = System.nanoTime() + (durationSec * 1_000_000_000.0).toLong()
    while (System.nanoTime() < deadline && running.get()) {
        try {
            Thread.sleep(50)
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
            break
        }
    }
    running.set(false)
    workers.forEach { runCatching { it.join(5_000) } }
}

// ----- service probe ------------------------------------------------------- //

data class ProbeResult(
    val phase: String,
    val connectMs: Double?,
    val relayMs: Double?,
    val ok: Boolean,
    val note: String = "",
) {
    fun line(index: Int): String {
        val connect = connectMs?.let { String.format("%.1f ms", it) } ?: "failed"
        val relay = relayMs?.let { String.format("%.1f ms", it) } ?: "no reply"
        val status = if (ok) "ok" else "FAILED"
        val suffix = if (note.isNotEmpty()) " — $note" else ""
        return "[probe $index $phase] connect $connect, message $relay ($status)$suffix"
    }
}

/** Two real SDK clients that keep measuring the server's responsiveness. */
class ServiceProbe(
    private val host: String,
    private val tcpPort: Int,
    private val udpPort: Int,
    private val probeChannel: String,
    private val probeUsername: String,
    private val probePassword: String,
    private val log: (String) -> Unit,
) {
    private var sender: TeamTalkSession? = null
    private var receiver: TeamTalkSession? = null
    private var channelId: Int = 1
    private var fellBackToRoot = false
    private val seed = System.currentTimeMillis()

    private fun baseConfig(role: String) = ConnectionConfig(
        host = host,
        tcpPort = tcpPort,
        udpPort = udpPort,
        username = probeUsername,
        password = probePassword,
        nickname = "tt-probe-$role",
        commandTimeoutSec = 15.0,
        kickResistance = false,
    )

    private fun openOne(role: String): TeamTalkSession {
        val session = TeamTalkSession(baseConfig(role), log)
        session.open()
        joinProbeChannel(session)
        return session
    }

    private fun joinProbeChannel(session: TeamTalkSession) {
        try {
            channelId = session.joinChannelPath(probeChannel, "")
        } catch (t: Throwable) {
            if (!fellBackToRoot) {
                fellBackToRoot = true
                log("[probe] channel '$probeChannel' was not found; measuring from the root channel.")
            }
            val root = runCatching { session.client.getRootChannelID() }.getOrDefault(1)
            channelId = session.joinChannel(root, "")
        }
    }

    fun start() {
        close()
        sender = openOne("sender")
        // The server relays a channel message to the other users in the channel
        // and never back to its sender, so the probe needs a second login.
        receiver = openOne("listener")
    }

    fun probe(phase: String): ProbeResult {
        val connectStart = System.nanoTime()
        try {
            ensureOpen()
        } catch (t: Throwable) {
            return ProbeResult(phase, null, null, false, t.message ?: "no connection")
        }
        val connectMs = (System.nanoTime() - connectStart) / 1_000_000.0

        val marker = "ttprobe-${(System.currentTimeMillis() - seed) % 100000}"
        val senderSession = sender
        val receiverSession = receiver
        if (senderSession == null || receiverSession == null) {
            return ProbeResult(phase, connectMs, null, false, "probe session unavailable")
        }
        val sendStart = System.nanoTime()
        try {
            senderSession.sendChannelMessage(marker, channelId)
        } catch (t: Throwable) {
            return ProbeResult(phase, connectMs, null, false, "send failed: ${t.message}")
        }

        val deadline = System.nanoTime() + (PROBE_ECHO_TIMEOUT_SEC * 1_000_000_000.0).toLong()
        while (System.nanoTime() < deadline) {
            val message = receiverSession.poll(200) ?: break
            val event = TeamTalkSession.textEventFrom(message) ?: continue
            if (event.text != marker) continue
            val relayMs = (System.nanoTime() - sendStart) / 1_000_000.0
            return ProbeResult(phase, connectMs, relayMs, true)
        }
        return ProbeResult(phase, connectMs, null, false, "no relay within ${PROBE_ECHO_TIMEOUT_SEC}s")
    }

    private fun ensureOpen() {
        if (sender?.isOnline() != true) {
            sender?.close()
            sender = openOne("sender")
        }
        if (receiver?.isOnline() != true) {
            receiver?.close()
            receiver = openOne("listener")
        }
    }

    fun close() {
        runCatching { sender?.close() }
        runCatching { receiver?.close() }
        sender = null
        receiver = null
    }
}

private fun median(values: List<Double>): Double? {
    if (values.isEmpty()) return null
    val sorted = values.sorted()
    val middle = sorted.size / 2
    return if (sorted.size % 2 == 1) sorted[middle] else (sorted[middle - 1] + sorted[middle]) / 2
}

private fun phaseSummary(probes: List<ProbeResult>): String {
    if (probes.isEmpty()) return "no samples"
    val ok = probes.count { it.ok }
    val relays = probes.mapNotNull { it.relayMs }
    val medianRelay = median(relays)
    return buildString {
        append("$ok/${probes.size} ok")
        if (medianRelay != null) append(String.format(", median RTT %.1f ms", medianRelay))
    }
}

private fun verdict(
    baseline: List<ProbeResult>,
    during: List<ProbeResult>,
    after: List<ProbeResult>,
): String {
    if (during.isEmpty()) return "no during-flood samples were taken."
    val ok = during.count { it.ok }
    val baseMedian = median(baseline.mapNotNull { it.relayMs })
    val duringMedian = median(during.mapNotNull { it.relayMs })

    val head = when {
        ok == during.size ->
            "the server stayed fully in service during the flood ($ok/${during.size} probes succeeded)"
        ok == 0 -> {
            val recovered = after.isNotEmpty() && after.all { it.ok }
            return "the server was NOT reachable for any of the ${during.size} probes during the " +
                "flood and " + (if (recovered) "recovered after it stopped." else "did NOT fully recover after it stopped.")
        }
        else ->
            "the server degraded but stayed partly in service ($ok/${during.size} probes fully succeeded)"
    }
    val latency = if (baseMedian != null && duringMedian != null && baseMedian > 0) {
        String.format("; message latency went from %.1f ms to %.1f ms (%.1fx)", baseMedian, duringMedian, duringMedian / baseMedian)
    } else {
        ""
    }
    return "$head$latency."
}

/** LOIC-style local flood: tt_loic.py. */
object LoicFlood {

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val mode = values.str("mode", "both")
        val threads = values.int("threads", 8)
        if (threads !in 1..MAX_THREADS) {
            throw TeamTalkConfigException("Threads must be between 1 and $MAX_THREADS.")
        }
        val duration = values.double("duration", 10.0)
        if (duration <= 0 || duration > MAX_DURATION_SECONDS) {
            throw TeamTalkConfigException("Duration must be between 0 and ${MAX_DURATION_SECONDS.toInt()} seconds.")
        }
        val probeEnabled = values.bool("probe")
        val probeChannel = values.str("probe_channel", "/").ifBlank { "/" }

        // The flood is local-only by construction; off-device targets are refused.
        LocalGate.requireLocal(config.host)

        val host = config.host
        val tcpPort = if (config.tcpPort in 1..65535) config.tcpPort else DEFAULT_TCP_PORT
        val udpPort = if (config.udpPort in 1..65535) config.udpPort else DEFAULT_UDP_PORT

        // A `val` (not `var`) so the compiler can smart-cast it inside the
        // lambdas below.
        val probe: ServiceProbe? = if (probeEnabled) {
            ServiceProbe(host, tcpPort, udpPort, probeChannel, config.username, config.password, ctx::log)
        } else {
            null
        }
        val baseline = mutableListOf<ProbeResult>()
        val during = mutableListOf<ProbeResult>()
        val after = mutableListOf<ProbeResult>()

        if (probe == null) {
            ctx.log("[probe] skipped.")
        } else {
            probe.start()
            repeat(BASELINE_PROBES) { index ->
                val result = probe.probe("before")
                baseline += result
                ctx.log(result.line(index + 1))
                ctx.sleep(0.2)
            }
            if (baseline.isNotEmpty() && baseline.all { it.connectMs == null }) {
                probe.close()
                throw TeamTalkConfigException(
                    "Nothing is listening on $host:$tcpPort; start the TeamTalk server first.",
                )
            }
            ctx.log("Baseline: ${phaseSummary(baseline)}.")
        }

        ctx.log(
            "Flooding $host ($mode, TCP $tcpPort / UDP $udpPort) with $threads thread(s) per mode " +
                "for ${formatSeconds(duration)}s.",
        )

        val running = AtomicBoolean(true)
        val stats = mutableMapOf<String, FloodStats>()
        val floodThread = Thread({
            runFlood(host, tcpPort, udpPort, mode, threads, duration, 5_000, running, stats)
        }, "loic-flood").apply { isDaemon = true }

        try {
            floodThread.start()
            var index = 0
            val activeProbe = probe
            if (activeProbe != null) {
                while (floodThread.isAlive && !ctx.isStopped) {
                    index++
                    val result = activeProbe.probe("during")
                    during += result
                    ctx.log(result.line(index))
                    cadence(ctx, floodThread)
                }
            } else {
                while (floodThread.isAlive && !ctx.isStopped) {
                    ctx.sleep(0.1)
                }
            }
            floodThread.join(5_000)
            running.set(false)
            if (activeProbe != null) {
                repeat(AFTER_PROBES) { i ->
                    val result = activeProbe.probe("after")
                    after += result
                    ctx.log(result.line(i + 1))
                    ctx.sleep(0.2)
                }
            }
        } catch (cancelled: ToolCancelledException) {
            running.set(false)
            ctx.log("Interrupted; stopping the flood.")
            throw cancelled
        } finally {
            running.set(false)
            runCatching { floodThread.join(5_000) }
            probe?.close()
        }

        ctx.log("Flood stopped. Totals:")
        stats["tcp"]?.let {
            ctx.log(
                "  TCP: ${it.connections.get()} connection(s), " +
                    "${megabytes(it.bytesSent.get())} of junk, ${it.errors.get()} error(s).",
            )
        }
        stats["udp"]?.let {
            ctx.log(
                "  UDP: ${it.datagrams.get()} datagram(s), " +
                    "${megabytes(it.datagrams.get() * UDP_DATAGRAM_BYTES)} of junk, ${it.errors.get()} error(s).",
            )
        }

        if (baseline.isNotEmpty() || during.isNotEmpty() || after.isNotEmpty()) {
            ctx.log("Service impact:")
            if (baseline.isNotEmpty()) ctx.log("  before: ${phaseSummary(baseline)}")
            if (during.isNotEmpty()) ctx.log("  during: ${phaseSummary(during)}")
            if (after.isNotEmpty()) ctx.log("  after:  ${phaseSummary(after)}")
            ctx.log("Verdict: ${verdict(baseline, during, after)}")
        }
        return ToolResult(0, "Flood test complete.")
    }

    private fun cadence(ctx: RunContext, floodThread: Thread) {
        var waited = 0.0
        while (waited < PROBE_CADENCE_SEC && floodThread.isAlive && !ctx.isStopped) {
            ctx.sleep(0.1)
            waited += 0.1
        }
    }
}

// ----- ramp ---------------------------------------------------------------- //

private data class RampStage(val index: Int, val threads: Int, val mode: String, val duration: Double) {
    fun label(): String = "stage $index: $threads thread(s), $mode, ${formatSeconds(duration)}s"
}

private class StageResult(val stage: RampStage) {
    var verdict: String = "not run"
    var detail: String = ""
    val during = mutableListOf<ProbeResult>()
    val after = mutableListOf<ProbeResult>()

    /** One-line stage report, e.g. `stage 2: 2 thread(s), both, 10s — degraded: ...`. */
    fun line(): String {
        val base = "${stage.label()} — $verdict"
        return if (detail.isBlank()) base else "$base: $detail"
    }
}

/** Ramped breaking-point test: tt_ramp.py. */
object RampTest {

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val startThreads = values.int("start_threads", 1).coerceAtLeast(1)
        val factor = values.int("ramp_factor", 2).coerceAtLeast(2)
        val maxThreads = values.int("max_threads", 64)
        if (maxThreads !in 1..RAMP_MAX_THREADS) {
            throw TeamTalkConfigException("Max threads must be between 1 and $RAMP_MAX_THREADS.")
        }
        val stageDuration = values.double("stage_duration", 10.0)
        if (stageDuration < 1.0 || stageDuration > MAX_DURATION_SECONDS) {
            throw TeamTalkConfigException("Stage duration must be between 1 and ${MAX_DURATION_SECONDS.toInt()} seconds.")
        }
        val mode = values.str("mode", "both")
        val probeChannel = values.str("probe_channel", "/").ifBlank { "/" }

        // The ramp is allowed on any allowlisted host (the suite's whitelist is
        // the authorization gate), so it does not use the local-only check.
        val host = config.host
        val tcpPort = if (config.tcpPort in 1..65535) config.tcpPort else DEFAULT_TCP_PORT
        val udpPort = if (config.udpPort in 1..65535) config.udpPort else DEFAULT_UDP_PORT

        val stages = buildStages(startThreads, factor, maxThreads, mode, stageDuration)
        ctx.log("Ramp plan: ${stages.size} stage(s), ${formatSeconds(stageDuration)}s each.")
        stages.forEach { ctx.log("  ${it.label()}") }

        val probe = ServiceProbe(host, tcpPort, udpPort, probeChannel, config.username, config.password, ctx::log)
        val results = mutableListOf<StageResult>()
        try {
            probe.start()
            val baseline = mutableListOf<ProbeResult>()
            repeat(BASELINE_PROBES) { index ->
                val result = probe.probe("before")
                baseline += result
                ctx.log(result.line(index + 1))
                ctx.sleep(0.2)
            }
            val baselineMedian = median(baseline.mapNotNull { it.relayMs })
            if (baseline.isNotEmpty() && baseline.all { it.connectMs == null }) {
                throw TeamTalkConfigException("Nothing is listening on $host:$tcpPort; start the server first.")
            }
            ctx.log("Baseline: ${phaseSummary(baseline)}.")

            for (stage in stages) {
                ctx.checkStopped()
                val result = runStage(stage, host, tcpPort, udpPort, probe, ctx)
                classify(result, baselineMedian)
                results += result
                ctx.log(result.line())
                if (result.verdict == "broken") {
                    ctx.log("Stopping at the first broken stage.")
                    break
                }
                ctx.sleep(0.5)
            }
        } finally {
            probe.close()
        }

        ctx.log("Summary: ${summary(results)}")
        return ToolResult(0, "Ramp complete.")
    }

    private fun buildStages(
        startThreads: Int,
        factor: Int,
        maxThreads: Int,
        mode: String,
        duration: Double,
    ): List<RampStage> {
        val stages = mutableListOf<RampStage>()
        var threads = startThreads
        var index = 0
        while (true) {
            index++
            val clamped = threads.coerceAtMost(maxThreads)
            stages += RampStage(index, clamped, mode, duration)
            if (clamped >= maxThreads) break
            threads = maxOf(threads + 1, (threads * factor))
        }
        return stages
    }

    private fun runStage(
        stage: RampStage,
        host: String,
        tcpPort: Int,
        udpPort: Int,
        probe: ServiceProbe,
        ctx: RunContext,
    ): StageResult {
        val result = StageResult(stage)
        ctx.log("")
        ctx.log(stage.label())
        val running = AtomicBoolean(true)
        val stats = mutableMapOf<String, FloodStats>()
        val floodThread = Thread({
            runFlood(host, tcpPort, udpPort, stage.mode, stage.threads, stage.duration, 5_000, running, stats)
        }, "ramp-stage-${stage.index}").apply { isDaemon = true }

        try {
            floodThread.start()
            var index = 0
            while (floodThread.isAlive && !ctx.isStopped) {
                index++
                result.during += probe.probe("during")
                var waited = 0.0
                while (waited < PROBE_CADENCE_SEC && floodThread.isAlive && !ctx.isStopped) {
                    ctx.sleep(0.1)
                    waited += 0.1
                }
            }
            floodThread.join(5_000)
            running.set(false)
            result.after += probe.probe("after")
        } catch (cancelled: ToolCancelledException) {
            running.set(false)
            throw cancelled
        } finally {
            running.set(false)
            runCatching { floodThread.join(5_000) }
        }
        return result
    }

    private fun classify(stage: StageResult, baselineMedian: Double?) {
        if (stage.during.isEmpty()) {
            stage.verdict = "broken"
            stage.detail = "no during-stage probes were taken"
            return
        }
        val ok = stage.during.count { it.ok }
        if (ok == 0) {
            stage.verdict = "broken"
            stage.detail = "server unreachable for every probe this stage"
            return
        }
        val duringMedian = median(stage.during.mapNotNull { it.relayMs })
        val ratio = if (duringMedian != null && baselineMedian != null && baselineMedian > 0) {
            duringMedian / baselineMedian
        } else {
            null
        }
        val details = mutableListOf<String>()
        if (ok < stage.during.size) details += "$ok/${stage.during.size} probes ok"
        if (ratio != null && ratio >= DEGRADATION_LATENCY_RATIO) details += String.format("RTT %.1fx baseline", ratio)
        if (details.isNotEmpty()) {
            stage.verdict = "degraded"
            stage.detail = details.joinToString(", ")
        } else {
            stage.verdict = "healthy"
            stage.detail = ratio?.let { String.format("RTT %.1fx baseline", it) } ?: "all probes ok"
        }
    }

    private fun summary(stages: List<StageResult>): String {
        if (stages.isEmpty()) return "no stages were run."
        var lastHealthy: RampStage? = null
        var firstDegraded: RampStage? = null
        var firstBroken: RampStage? = null
        for (result in stages) {
            when (result.verdict) {
                "healthy" -> if (firstDegraded == null) lastHealthy = result.stage
                "degraded" -> if (firstDegraded == null) firstDegraded = result.stage
                "broken" -> {
                    firstBroken = result.stage
                }
            }
            if (firstBroken != null) break
        }
        val head = when {
            firstBroken != null ->
                "BREAKS at ${firstBroken.threads} thread(s) (stage ${firstBroken.index}): the server was unreachable under load."
            firstDegraded != null ->
                "No breaking point reached within this tool's ceiling."
            else ->
                "HELD at every tested load up to ${stages.last().stage.threads} thread(s); no breaking point reached."
        }
        val degradedLine = firstDegraded?.let { " Degrades first at ${it.threads} thread(s) (stage ${it.index})." } ?: ""
        val holdLine = lastHealthy?.let { " Holds cleanly up to ${it.threads} thread(s)." } ?: " No stage was fully clean."
        return head + degradedLine + holdLine
    }
}

private fun megabytes(bytes: Long): String = String.format("%.1f MB", bytes / 1_048_576.0)
