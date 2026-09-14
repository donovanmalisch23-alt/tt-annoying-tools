package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.TeamTalkConfigException
import com.teamtalk.annoying.core.TeamTalkSession
import com.teamtalk.annoying.core.TextEvent
import com.teamtalk.annoying.core.ToolCancelledException
import dk.bearware.ClientEvent

/**
 * Idle bots: tt_concurrent_bots.py.
 *
 * The desktop tool splits thousands of bots across worker processes because
 * of a select() FD ceiling. Android can only hold a modest number of native
 * clients in one process, so this port runs bots as threads under a hard,
 * conservative cap and says so rather than pretending to match desktop scale.
 */
object IdleBots {

    const val MAX_BOTS = 128

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val count = values.int("count", 1)
        if (count !in 1..MAX_BOTS) {
            throw TeamTalkConfigException("Idle bot count must be between 1 and $MAX_BOTS.")
        }
        val startDelay = (values.int("start_delay_ms", 150) / 1000.0).coerceAtLeast(0.0)
        val attempts = values.int("connect_attempts", 5).coerceAtLeast(1)

        ctx.log(
            "Launching $count idle bot(s); Android builds cap this at $MAX_BOTS per run. " +
                "Stop the run to release them.",
        )

        val threads = (0 until count).map { index ->
            Thread({ idleBot(config, index, attempts, ctx) }, "idle-bot-$index").apply {
                isDaemon = true
            }
        }

        try {
            threads.forEachIndexed { index, thread ->
                thread.start()
                if (index < threads.size - 1 && startDelay > 0) ctx.sleep(startDelay)
            }
        } catch (cancelled: ToolCancelledException) {
            ctx.log("Launch interrupted; stopping the bots that already started.")
            ctx.requestStop()
        }

        // Bots run until stopped, but if every bot gives up (or fails to
        // connect) the tool should finish instead of blocking forever.
        try {
            while (threads.any { it.isAlive } && !ctx.isStopped) ctx.sleep(0.5)
        } finally {
            if (ctx.isStopped) ctx.requestStop()
            threads.forEach { runCatching { it.join(5_000) } }
        }
        return ToolResult(0, "Idle bots stopped.")
    }

    private fun idleBot(
        base: ConnectionConfig,
        index: Int,
        attempts: Int,
        ctx: RunContext,
    ) {
        val log = ctx.child("[bot $index] ")
        val config = base.copy(nickname = "${base.nickname}-$index")

        for (attempt in 0 until attempts) {
            if (ctx.isStopped) return
            val session = TeamTalkSession(config, log::log)
            try {
                session.open()
                log.log("connected and idle.")
                while (!ctx.isStopped) {
                    if (!session.isOnline()) {
                        if (!session.checkAndReconnect()) return
                    }
                    ctx.sleep(1.0)
                }
                return
            } catch (cancelled: ToolCancelledException) {
                return
            } catch (configError: TeamTalkConfigException) {
                log.log("configuration error: ${configError.message}")
                return
            } catch (t: Throwable) {
                if (attempt + 1 >= attempts) {
                    log.log("gave up after ${attempt + 1} attempt(s): ${t.message}")
                    return
                }
                log.log("connect attempt ${attempt + 1} failed: ${t.message}; retrying.")
            } finally {
                session.close()
            }
            runCatching { ctx.sleep(2.0) }
        }
    }
}

/**
 * Response bot: ttbot_the_offender.py.
 *
 * The original Windows executable insulted people automatically. This port
 * keeps the safe behaviour: it answers only an explicit trigger, only for
 * allowlisted users, and only once per cooldown window.
 */
object ResponseBot {

    private const val MIN_COOLDOWN_SECONDS = 5.0

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val trigger = values.str("trigger", "!hello")
        if (trigger.isEmpty()) throw TeamTalkConfigException("Trigger text cannot be empty.")
        val template = values.str("response", "Hi {username}, thanks for your message!")
        if (template.toByteArray(Charsets.UTF_8).size > 4096) {
            throw TeamTalkConfigException("Response is limited to 4096 UTF-8 bytes.")
        }
        val allowUsers = parseUserList(values.str("allow_users"))
        val allowAll = values.bool("allow_all")
        if (allowUsers.isEmpty() && !allowAll) {
            throw TeamTalkConfigException(
                "Allowlist at least one username, or explicitly enable 'Allow any user'.",
            )
        }
        val cooldown = values.double("cooldown", 30.0)
        if (cooldown < MIN_COOLDOWN_SECONDS) {
            throw TeamTalkConfigException("Cooldown must be at least $MIN_COOLDOWN_SECONDS seconds.")
        }
        val maxResponses = values.int("max_responses", 100)
        if (maxResponses < 0) throw TeamTalkConfigException("Max responses must be zero or greater.")
        if (config.channelId == null && config.channelPath == null) {
            throw TeamTalkConfigException("A channel is required for the response bot.")
        }

        return openSession(config, ctx) { session ->
            var channelId = session.currentChannelId()
            session.rejoinChannelId = channelId
            session.rejoinChannelPassword = config.channelPassword
            var ownUserId = runCatching { session.client.getMyUserID() }.getOrDefault(-1)
            val allowed = allowUsers.map { it.lowercase() }.toSet()
            val lastReply = HashMap<String, Long>()
            var responses = 0
            var lastWatchdogMs = 0L
            val watchdogIntervalMs = (config.reconnectDelaySec * 1000).toLong().coerceAtLeast(1_000L)

            ctx.log("Listening in channel $channelId; trigger '$trigger'. Stop the run to end.")

            while (maxResponses == 0 || responses < maxResponses) {
                ctx.checkStopped()
                val message = session.poll(1_000)

                // Catch a kick the moment the CON_LOST/CON_FAILED event is dequeued.
                if (session.isConnectionFailure(message)) {
                    ctx.log("[kick-resistance] bot lost its connection.")
                    if (!session.checkAndReconnect()) {
                        ctx.log("Could not reconnect; stopping bot.")
                        return@openSession ToolResult(1, "Disconnected.")
                    }
                    channelId = runCatching { session.currentChannelId() }.getOrElse { channelId }
                    ownUserId = runCatching { session.client.getMyUserID() }.getOrDefault(ownUserId)
                    continue
                }

                val nowMs = System.currentTimeMillis()
                if (nowMs - lastWatchdogMs >= watchdogIntervalMs) {
                    lastWatchdogMs = nowMs
                    if (!session.isOnline()) {
                        ctx.log("[kick-resistance] bot was kicked from the server.")
                        if (!session.checkAndReconnect()) {
                            ctx.log("Could not reconnect; stopping bot.")
                            return@openSession ToolResult(1, "Disconnected.")
                        }
                        channelId = runCatching { session.currentChannelId() }.getOrElse { channelId }
                        ownUserId = runCatching { session.client.getMyUserID() }.getOrDefault(ownUserId)
                        continue
                    }
                    if (runCatching { session.currentChannelId() }.isFailure) {
                        ctx.log("[kick-resistance] kicked from channel; rejoining.")
                        runCatching { session.joinChannel(channelId, config.channelPassword) }
                            .onFailure { ctx.log("[kick-resistance] rejoin failed: ${it.message}") }
                    }
                }

                if ((message?.nClientEvent ?: 0) != ClientEvent.CLIENTEVENT_CMD_USER_TEXTMSG) continue
                val incoming = TeamTalkSession.textEventFrom(message) ?: continue
                if (incoming.fromUserId == ownUserId || incoming.channelId != channelId) continue
                if (incoming.more || !incoming.text.startsWith(trigger)) continue

                val senderKey = if (incoming.fromUsername.isNotBlank()) {
                    incoming.fromUsername.lowercase()
                } else {
                    "user-${incoming.fromUserId}"
                }
                if (!allowAll && senderKey !in allowed) continue

                val previous = lastReply[senderKey]
                if (previous != null && nowMs - previous < (cooldown * 1000).toLong()) continue

                val response = renderResponse(template, incoming)
                try {
                    session.sendChannelMessage(response, channelId)
                } catch (t: Throwable) {
                    if (t is ToolCancelledException) throw t
                    ctx.log("[kick-resistance] reply interrupted: ${t.message}")
                    if (!session.checkAndReconnect()) {
                        ctx.log("Could not reconnect; stopping bot.")
                        return@openSession ToolResult(1, "Disconnected.")
                    }
                    channelId = runCatching { session.currentChannelId() }.getOrElse { channelId }
                    continue
                }
                lastReply[senderKey] = nowMs
                responses++
                val sender = incoming.fromUsername.ifBlank { "user ${incoming.fromUserId}" }
                ctx.log("Replied to $sender ($responses/${if (maxResponses == 0) "∞" else maxResponses}).")
            }
            ToolResult(0, "Reached the response limit.")
        }
    }

    internal fun renderResponse(template: String, event: TextEvent): String {
        val rendered = template
            .replace("{username}", event.fromUsername)
            .replace("{user_id}", event.fromUserId.toString())
            .replace("{message}", event.text)
        if (Regex("\\{[A-Za-z_][A-Za-z0-9_]*}").containsMatchIn(rendered)) {
            throw TeamTalkConfigException(
                "Unsupported response placeholder; use {username}, {user_id} or {message}.",
            )
        }
        if (rendered.isBlank()) throw TeamTalkConfigException("Rendered response cannot be empty.")
        return rendered
    }
}
