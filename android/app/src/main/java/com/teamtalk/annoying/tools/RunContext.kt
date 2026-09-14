package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.LogBus
import com.teamtalk.annoying.core.TeamTalkSession
import com.teamtalk.annoying.core.ToolCancelledException
import java.util.concurrent.atomic.AtomicBoolean

/** Exit status of one tool run. */
data class ToolResult(val code: Int, val message: String)

/**
 * Everything a running tool needs: a log sink, a cancellation flag and
 * interruptible sleeps. This replaces the "Ctrl+C" that stops a CLI tool.
 *
 * Stop state lives in a shared [AtomicBoolean] so concurrent bots started by
 * the suite all observe one stop request.
 */
class RunContext(
    val toolTitle: String,
    private val logSink: (String) -> Unit = { LogBus.log(it) },
    private val stopFlag: AtomicBoolean = AtomicBoolean(false),
) {
    fun log(message: String) = logSink(message)

    fun requestStop() {
        stopFlag.set(true)
    }

    val isStopped: Boolean get() = stopFlag.get()

    fun checkStopped() {
        if (stopFlag.get()) throw ToolCancelledException()
    }

    /** Interruptible sleep that aborts as soon as the run is stopped. */
    fun sleep(seconds: Double) {
        checkStopped()
        if (seconds <= 0) return
        val deadlineNs = System.nanoTime() + (seconds * 1_000_000_000.0).toLong()
        while (System.nanoTime() < deadlineNs) {
            checkStopped()
            val remainingMs = ((deadlineNs - System.nanoTime()) / 1_000_000L).coerceAtLeast(1L)
            try {
                Thread.sleep(remainingMs.coerceAtMost(100L))
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                throw ToolCancelledException()
            }
        }
        checkStopped()
    }

    /** Block until the run is stopped. Used by idle bots; never throws. */
    fun awaitStop(pollSeconds: Double = 1.0) {
        while (!stopFlag.get()) {
            try {
                Thread.sleep((pollSeconds * 1000).toLong().coerceAtLeast(50L))
            } catch (interrupted: InterruptedException) {
                Thread.currentThread().interrupt()
                return
            }
        }
    }

    /** A context that shares this run's stop flag and prefixes its log lines. */
    fun child(prefix: String): RunContext =
        RunContext(toolTitle, { log("$prefix$it") }, stopFlag)
}

/** Opens a session, runs [block], and always releases the native client. */
internal fun <T> openSession(
    config: ConnectionConfig,
    ctx: RunContext,
    block: (TeamTalkSession) -> T,
): T {
    val session = TeamTalkSession(config, ctx::log)
    try {
        session.open()
        return block(session)
    } finally {
        session.close()
    }
}
