package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.TeamTalkConfigException
import com.teamtalk.annoying.core.TeamTalkSession
import com.teamtalk.annoying.core.ToolCancelledException

private const val MAX_CONSECUTIVE_FAILURES = 3
private const val MAX_WAIT_SECONDS = 300.0

private fun requirePositiveCycles(cycles: Int, label: String) {
    if (cycles < 1) throw TeamTalkConfigException("$label must be at least 1.")
}

private fun requireNonNegative(value: Double, label: String) {
    if (value < 0) throw TeamTalkConfigException("$label cannot be negative.")
}

private fun requireWait(wait: Double) {
    if (wait < 0 || wait > MAX_WAIT_SECONDS) {
        throw TeamTalkConfigException("Startup wait must be between 0 and $MAX_WAIT_SECONDS seconds.")
    }
}

/** Resolve a username (or nickname) to its current server-assigned user ID. */
private fun resolveUserId(session: TeamTalkSession, name: String): Int? {
    val key = name.lowercase()
    return session.listUsers().firstOrNull {
        it.username.lowercase() == key ||
            it.nickname.lowercase() == key ||
            it.displayName.lowercase() == key
    }?.id
}

/** Message sender: tt_message_spammer.py. */
object MessageSpammer {

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val target = values.str("target", "channel")
        val message = values.str("message", "Oh Yeah!")
        if (message.isEmpty()) throw TeamTalkConfigException("Message text cannot be empty.")
        if (message.toByteArray(Charsets.UTF_8).size > 4096) {
            throw TeamTalkConfigException("Message is limited to 4096 UTF-8 bytes.")
        }
        val count = values.int("count", 3)
        requirePositiveCycles(count, "Message count")
        val interval = values.double("interval_ms", 50.0) / 1000.0
        requireNonNegative(interval, "Interval")
        val wait = values.double("wait", 0.0)
        requireWait(wait)
        val names = parseUserList(values.str("users"))
        if (target == "private" && names.isEmpty()) {
            throw TeamTalkConfigException("Private messaging needs at least one recipient username.")
        }

        if (wait > 0) {
            ctx.log("Waiting ${formatSeconds(wait)}s before sending…")
            ctx.sleep(wait)
        }

        val sessionConfig = if (target == "channel") config else config.loginOnly()
        return openSession(sessionConfig, ctx) { session ->
            val code = if (target == "channel") {
                sendToChannel(session, message, count, interval, ctx)
            } else {
                sendToUsers(session, names, message, count, interval, ctx)
            }
            if (code == 0) ToolResult(0, "Finished sending.") else ToolResult(code, "Stopped early.")
        }
    }

    private fun sendToChannel(
        session: TeamTalkSession,
        message: String,
        count: Int,
        interval: Double,
        ctx: RunContext,
    ): Int {
        runCatching {
            session.rejoinChannelId = session.currentChannelId()
            session.rejoinChannelPassword = session.config.channelPassword
        }
        var misses = 0
        var sent = 0
        while (sent < count) {
            ctx.checkStopped()
            val channelId: Int
            try {
                channelId = session.currentChannelId()
                session.sendChannelMessage(message, channelId)
            } catch (t: Throwable) {
                if (t is ToolCancelledException) throw t
                ctx.log("[kick-resistance] send ${sent + 1} interrupted: ${t.message}")
                misses++
                if (misses >= MAX_CONSECUTIVE_FAILURES) {
                    ctx.log("Too many failed sends in a row; stopping message test.")
                    return 1
                }
                if (!session.checkAndReconnect()) {
                    ctx.log("Could not reconnect; stopping message test.")
                    return 1
                }
                continue
            }
            misses = 0
            sent++
            ctx.log("Sent $sent/$count to channel $channelId.")
            if (sent < count && interval > 0) ctx.sleep(interval)
        }
        return 0
    }

    private fun sendToUsers(
        session: TeamTalkSession,
        names: List<String>,
        message: String,
        count: Int,
        interval: Double,
        ctx: RunContext,
    ): Int {
        var misses = 0
        for (round in 1..count) {
            names.forEachIndexed { index, name ->
                ctx.checkStopped()
                var delivered = false
                while (!delivered) {
                    val userId = resolveUserId(session, name)
                    if (userId == null) {
                        ctx.log("$name is not online; skipping them this round.")
                        break
                    }
                    try {
                        session.sendPrivateMessage(message, userId)
                    } catch (t: Throwable) {
                        if (t is ToolCancelledException) throw t
                        ctx.log("[kick-resistance] send to $name interrupted: ${t.message}")
                        misses++
                        if (misses >= MAX_CONSECUTIVE_FAILURES) {
                            ctx.log("Too many failed sends in a row; stopping message test.")
                            return 1
                        }
                        if (!session.checkAndReconnect()) {
                            ctx.log("Could not reconnect; stopping message test.")
                            return 1
                        }
                        continue // retry the same send; counters do not move
                    }
                    misses = 0
                    delivered = true
                    ctx.log(
                        "Sent message $round/$count to $name (${index + 1}/${names.size} recipients).",
                    )
                }
                if (interval > 0) ctx.sleep(interval)
            }
        }
        return 0
    }
}

/** Login/logout cycles: tt_spammer.py. */
object LoginSpammer {

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val cycles = values.int("cycles", 5)
        requirePositiveCycles(cycles, "Cycle count")
        val interval = values.double("interval_ms", 200.0) / 1000.0
        requireNonNegative(interval, "Interval")
        val wait = values.double("wait", 0.0)
        requireWait(wait)

        if (wait > 0) {
            ctx.log("Waiting ${formatSeconds(wait)}s before the login/logout test…")
            ctx.sleep(wait)
        }

        return openSession(config.loginOnly(), ctx) { session ->
            var completed = 0
            while (completed < cycles) {
                ctx.checkStopped()
                try {
                    if (!session.loggedIn) session.login()
                    ctx.log("Cycle ${completed + 1}/$cycles: logged in.")
                    session.logout()
                    ctx.log("Cycle ${completed + 1}/$cycles: logged out.")
                } catch (t: Throwable) {
                    if (t is ToolCancelledException) throw t
                    ctx.log("[kick-resistance] cycle ${completed + 1} interrupted: ${t.message}")
                    if (!session.checkAndReconnect()) {
                        ctx.log("Could not reconnect; stopping login/logout test.")
                        return@openSession ToolResult(1, "Interrupted.")
                    }
                    // Only completed cycles count: retry the interrupted one.
                    continue
                }
                completed++
                if (completed < cycles && interval > 0) ctx.sleep(interval)
            }
            ToolResult(0, "Finished login/logout test.")
        }
    }
}

/** Channel leave/join cycles: tt_leave_join_spammer.py. */
object LeaveJoinSpammer {

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val cycles = values.int("cycles", 5)
        requirePositiveCycles(cycles, "Cycle count")
        val interval = values.double("interval_ms", 200.0) / 1000.0
        requireNonNegative(interval, "Interval")
        val wait = values.double("wait", 0.0)
        requireWait(wait)

        val channelId = config.channelId
        val channelPath = config.channelPath
        if (channelId == null && channelPath == null) {
            throw TeamTalkConfigException(
                "A channel is required for a leave/join test; set one on the Connection tab.",
            )
        }

        if (wait > 0) {
            ctx.log("Waiting ${formatSeconds(wait)}s before the leave/join test…")
            ctx.sleep(wait)
        }

        return openSession(config, ctx) { session ->
            if (channelId != null) {
                session.rejoinChannelId = channelId
                session.rejoinChannelPassword = config.channelPassword
            }
            var completed = 0
            while (completed < cycles) {
                ctx.checkStopped()
                try {
                    val current = session.currentChannelId()
                    session.leaveChannel()
                    ctx.log("Cycle ${completed + 1}/$cycles: left channel $current.")
                    if (interval > 0) ctx.sleep(interval)
                    val joined = if (channelId != null) {
                        session.joinChannel(channelId, config.channelPassword)
                    } else {
                        session.joinChannelPath(channelPath!!, config.channelPassword)
                    }
                    ctx.log("Cycle ${completed + 1}/$cycles: joined channel $joined.")
                } catch (t: Throwable) {
                    if (t is ToolCancelledException) throw t
                    ctx.log("[kick-resistance] cycle ${completed + 1} interrupted: ${t.message}")
                    if (!session.checkAndReconnect()) {
                        ctx.log("Could not reconnect; stopping leave/join test.")
                        return@openSession ToolResult(1, "Interrupted.")
                    }
                    continue
                }
                completed++
                if (completed < cycles && interval > 0) ctx.sleep(interval)
            }
            ToolResult(0, "Finished leave/join test.")
        }
    }
}

internal fun formatSeconds(value: Double): String =
    if (value == value.toLong().toDouble()) value.toLong().toString() else value.toString()
