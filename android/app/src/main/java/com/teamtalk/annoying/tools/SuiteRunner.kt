package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ChannelInfo
import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.LogBus
import com.teamtalk.annoying.core.TeamTalkConfigException
import com.teamtalk.annoying.core.TeamTalkException
import com.teamtalk.annoying.core.TeamTalkSession
import com.teamtalk.annoying.core.ToolCancelledException
import com.teamtalk.annoying.core.UserInfo

/**
 * Combined suite: tt_suite.py.
 *
 * Discovery runs on a throwaway session that deliberately does not auto-join
 * the configured channel; channel targets are then joined explicitly. The
 * concurrent mode gives every bot its own SDK connection, but on Android the
 * bots are threads inside one process (the desktop tool forks workers instead).
 */
object SuiteRunner {

    private const val MAX_CONSECUTIVE_FAILURES = 3
    private const val MAX_CONCURRENT_BOTS = 64

    fun run(config: ConnectionConfig, values: Map<String, String>, ctx: RunContext): ToolResult {
        val allChannels = values.bool("all_channels")
        val allUsers = values.bool("all_users")
        val explicitUsers = parseUserList(values.str("users"))
        val channelMessage = values.str("channel_message").takeIf { it.isNotBlank() }
        val privateMessage = values.str("private_message").takeIf { it.isNotBlank() }
        channelMessage?.let { requireMessageFits(it, "Channel message") }
        privateMessage?.let { requireMessageFits(it, "Private message") }
        val messageCount = values.int("message_count", 1)
        if (messageCount < 1) throw TeamTalkConfigException("Message count must be at least 1.")
        val joinLeaveCycles = values.int("join_leave_cycles", 0)
        val loginCycles = values.int("login_cycles", 0)
        val interval = values.double("interval", 0.2)
        val concurrent = values.bool("concurrent")
        val churnBots = values.int("churn_bots", 0)
        val churnCycles = values.int("churn_cycles", 10)
        val perChannel = values.bool("bot_per_channel")
        val perUser = values.bool("bot_per_user")
        val sweepInterval = values.double("sweep_interval", 0.5)

        if ((perChannel || perUser) && !concurrent) {
            throw TeamTalkConfigException("One bot per channel/user requires concurrent mode.")
        }

        val channelAction = channelMessage != null || joinLeaveCycles > 0

        // Discovery session: never auto-joins a channel.
        val discoveryConfig = config.copy(channelId = null, channelPath = null, channelPassword = "")
        val (channels, users) = TeamTalkSession(discoveryConfig, ctx::log).let { session ->
            try {
                session.open()
                val discoveredChannels = session.listChannels()
                var discoveredUsers = session.listUsers()
                val deadline = System.currentTimeMillis() + 2_000
                while (discoveredUsers.isEmpty() && System.currentTimeMillis() < deadline) {
                    Thread.sleep(50)
                    discoveredUsers = session.listUsers()
                }
                Pair(discoveredChannels, discoveredUsers)
            } finally {
                session.close()
            }
        }

        printDiscovery(channels, users)

        val selectedChannels = selectChannels(channels, discoveryConfig, allChannels)
        val selectedUsers = when {
            explicitUsers.isNotEmpty() -> users.filter { user ->
                explicitUsers.any {
                    it.equals(user.username, ignoreCase = true) ||
                        it.equals(user.nickname, ignoreCase = true)
                }
            }
            allUsers -> users
            else -> emptyList()
        }

        ctx.log("Selected ${selectedChannels.size} channel(s) and ${selectedUsers.size} user(s).")

        if (concurrent) {
            return runConcurrent(
                config = discoveryConfig,
                ctx = ctx,
                selectedChannels = selectedChannels,
                selectedUsers = selectedUsers,
                allUsers = allUsers,
                allChannels = allChannels,
                channelAction = channelAction,
                channelMessage = channelMessage,
                privateMessage = privateMessage,
                messageCount = messageCount,
                joinLeaveCycles = joinLeaveCycles,
                interval = interval,
                churnBots = churnBots,
                churnCycles = churnCycles,
                perChannel = perChannel,
                perUser = perUser,
                sweepInterval = sweepInterval,
            )
        }

        if (privateMessage != null && selectedUsers.isEmpty()) {
            throw TeamTalkConfigException("No online users matched the private-message selection.")
        }

        val opsConfig = config.copy(channelId = null, channelPath = null)
        return openSession(opsConfig, ctx) { session ->
            if (loginCycles > 0) {
                // Run the cycles on this same session (as the desktop suite
                // does) instead of opening a second connection.
                var completed = 0
                while (completed < loginCycles) {
                    ctx.checkStopped()
                    try {
                        if (!session.loggedIn) session.login()
                        session.logout()
                    } catch (t: Throwable) {
                        if (t is ToolCancelledException) throw t
                        ctx.log("[kick-resistance] login cycle ${completed + 1} interrupted: ${t.message}")
                        if (!session.checkAndReconnect()) {
                            throw TeamTalkException("Could not reconnect; stopping the run.")
                        }
                        continue
                    }
                    completed++
                    ctx.log("Login cycle $completed/$loginCycles complete.")
                    if (completed < loginCycles && interval > 0) ctx.sleep(interval)
                }
                if (session.connected && !session.loggedIn) {
                    session.login()
                    ctx.log("Logged back in for the requested test operations.")
                }
            }

            if (selectedChannels.isNotEmpty()) {
                runChannelOperations(
                    session, selectedChannels, config.channelPassword,
                    joinLeaveCycles, channelMessage, messageCount, interval, ctx,
                )
            }
            if (privateMessage != null) {
                if (selectedUsers.isEmpty()) {
                    throw TeamTalkConfigException("No online users matched the private-message selection.")
                }
                runPrivateOperations(
                    session, selectedUsers, privateMessage, messageCount, interval, ctx,
                )
            }
            ToolResult(0, "Finished TeamTalk suite.")
        }
    }

    // ----- sequential operations -------------------------------------------- //

    private fun runChannelOperations(
        session: TeamTalkSession,
        channels: List<ChannelInfo>,
        channelPassword: String,
        joinLeaveCycles: Int,
        channelMessage: String?,
        messageCount: Int,
        interval: Double,
        ctx: RunContext,
    ) {
        for (channel in channels) {
            ctx.checkStopped()
            guarded(ctx, "join ${channel.path}", { session.joinChannel(channel.id, channelPassword) }) {
                session.checkAndReconnect()
            }
            session.rejoinChannelId = channel.id
            session.rejoinChannelPassword = channelPassword

            for (cycle in 1..joinLeaveCycles) {
                guarded(ctx, "leave ${channel.path}", { session.leaveChannel() }) {
                    session.checkAndReconnect()
                }
                ctx.log("${channel.path}: left channel ($cycle/$joinLeaveCycles).")
                if (interval > 0) ctx.sleep(interval)
                guarded(ctx, "rejoin ${channel.path}", { session.joinChannel(channel.id, channelPassword) }) {
                    session.checkAndReconnect()
                }
                ctx.log("${channel.path}: joined channel ($cycle/$joinLeaveCycles).")
                if (interval > 0) ctx.sleep(interval)
            }

            if (channelMessage != null) {
                for (index in 1..messageCount) {
                    guarded(ctx, "message ${channel.path}", { session.sendChannelMessage(channelMessage, channel.id) }) {
                        session.checkAndReconnect()
                    }
                    ctx.log("${channel.path}: sent message $index/$messageCount.")
                    if (interval > 0) ctx.sleep(interval)
                }
            }
        }
    }

    private fun runPrivateOperations(
        session: TeamTalkSession,
        users: List<UserInfo>,
        message: String,
        messageCount: Int,
        interval: Double,
        ctx: RunContext,
    ) {
        for (round in 1..messageCount) {
            for (user in users) {
                ctx.checkStopped()
                guarded(ctx, "message ${user.displayName}", {
                    val target = session.listUsers().firstOrNull {
                        it.username.equals(user.username, ignoreCase = true) && it.username.isNotBlank()
                    } ?: session.listUsers().firstOrNull { it.id == user.id }
                    if (target == null) throw TeamTalkException("${user.displayName} is no longer online.")
                    session.sendPrivateMessage(message, target.id)
                }) { session.checkAndReconnect() }
                ctx.log("Sent message $round/$messageCount to ${user.displayName}.")
                if (interval > 0) ctx.sleep(interval)
            }
        }
    }

    /** Retries one operation after a reconnect, with a bounded failure budget. */
    private fun guarded(
        ctx: RunContext,
        label: String,
        action: () -> Unit,
        reconnect: () -> Boolean,
    ) {
        var failures = 0
        while (true) {
            ctx.checkStopped()
            try {
                action()
                return
            } catch (cancelled: ToolCancelledException) {
                throw cancelled
            } catch (t: Throwable) {
                failures++
                ctx.log("[kick-resistance] $label interrupted: ${t.message}")
                if (failures >= MAX_CONSECUTIVE_FAILURES) {
                    throw TeamTalkException("Too many failures on $label; stopping the run.")
                }
                if (!reconnect()) throw TeamTalkException("Could not reconnect; stopping the run.")
            }
        }
    }

    // ----- concurrent bots -------------------------------------------------- //

    private fun runConcurrent(
        config: ConnectionConfig,
        ctx: RunContext,
        selectedChannels: List<ChannelInfo>,
        selectedUsers: List<UserInfo>,
        allUsers: Boolean,
        allChannels: Boolean,
        channelAction: Boolean,
        channelMessage: String?,
        privateMessage: String?,
        messageCount: Int,
        joinLeaveCycles: Int,
        interval: Double,
        churnBots: Int,
        churnCycles: Int,
        perChannel: Boolean,
        perUser: Boolean,
        sweepInterval: Double,
    ): ToolResult {
        val jobs = mutableListOf<Pair<String, (RunContext) -> Unit>>()

        if (privateMessage != null) {
            if (perUser) {
                selectedUsers.forEach { user ->
                    jobs += "user-bot-${user.id}" to { botCtx: RunContext ->
                        userBot(config, botCtx, listOf(user), false, privateMessage, messageCount, interval, sweepInterval)
                    }
                }
            }
            if (!perUser) {
                jobs += "user-bot" to { botCtx: RunContext ->
                    userBot(config, botCtx, selectedUsers, allUsers, privateMessage, messageCount, interval, sweepInterval)
                }
            }
        }

        if (channelAction) {
            if (perChannel) {
                selectedChannels.forEach { channel ->
                    jobs += "channel-bot-${channel.id}" to { botCtx: RunContext ->
                        channelBot(config, botCtx, listOf(channel), channelMessage, messageCount, joinLeaveCycles, interval)
                    }
                }
            }
            if (!perChannel) {
                jobs += "channel-bot" to { botCtx: RunContext ->
                    channelBot(config, botCtx, selectedChannels, channelMessage, messageCount, joinLeaveCycles, interval)
                }
            }
        }

        for (index in 1..churnBots) {
            jobs += "churn-bot-$index" to { botCtx: RunContext ->
                churnBot(config, botCtx, index, churnBots, churnCycles, interval)
            }
        }

        if (jobs.isEmpty()) {
            throw TeamTalkConfigException(
                "Concurrent mode needs something to do: set a channel message, a private " +
                    "message, or churn bots.",
            )
        }
        if (jobs.size > MAX_CONCURRENT_BOTS) {
            throw TeamTalkConfigException(
                "This run would start ${jobs.size} bots; Android builds cap concurrent bots at " +
                    "$MAX_CONCURRENT_BOTS. Reduce the bots or targets.",
            )
        }

        ctx.log("Concurrent plan: ${jobs.size} bot(s).")
        val threads = jobs.map { (name, body) ->
            val botCtx = ctx.child("[$name] ")
            Thread({
                try {
                    body(botCtx)
                } catch (cancelled: ToolCancelledException) {
                    // asked to stop
                } catch (t: Throwable) {
                    botCtx.log("bot stopped: ${t.message}")
                }
            }, name).apply { isDaemon = true }
        }
        // Continuous all-users mode never finishes on its own, so wait for a
        // stop request; every other configuration ends when its bots do.
        val continuous = privateMessage != null && allUsers && !perUser
        threads.forEach { it.start() }
        try {
            if (continuous) {
                ctx.awaitStop()
            } else {
                while (threads.any { it.isAlive } && !ctx.isStopped) ctx.sleep(0.2)
            }
        } finally {
            if (ctx.isStopped) ctx.requestStop()
            threads.forEach { runCatching { it.join(5_000) } }
        }
        return ToolResult(0, "Finished concurrent TeamTalk suite.")
    }

    private fun userBot(
        config: ConnectionConfig,
        ctx: RunContext,
        targets: List<UserInfo>,
        continuous: Boolean,
        message: String,
        messageCount: Int,
        interval: Double,
        sweepInterval: Double,
    ) {
        val session = TeamTalkSession(config.loginOnly(), ctx::log)
        try {
            session.open()
            if (continuous) {
                val messaged = mutableSetOf<String>()
                ctx.log("Continuous mode: messaging every user and any new joiner.")
                while (!ctx.isStopped) {
                    for (user in session.listUsers()) {
                        val key = user.username.ifBlank { user.displayName }.lowercase()
                        if (key in messaged) continue
                        repeat(messageCount) {
                            session.sendPrivateMessage(message, user.id)
                            if (interval > 0) ctx.sleep(interval)
                        }
                        messaged += key
                        ctx.log("Messaged ${user.displayName} ($messageCount message(s)).")
                    }
                    ctx.sleep(sweepInterval)
                }
                return
            }
            for (user in targets) {
                for (index in 1..messageCount) {
                    ctx.checkStopped()
                    val target = session.listUsers().firstOrNull { it.id == user.id }
                    if (target == null) {
                        ctx.log("${user.displayName} is not online; skipping.")
                        break
                    }
                    session.sendPrivateMessage(message, target.id)
                    ctx.log("Sent $index/$messageCount to ${user.displayName}.")
                    if (interval > 0) ctx.sleep(interval)
                }
            }
        } finally {
            session.close()
        }
    }

    private fun channelBot(
        config: ConnectionConfig,
        ctx: RunContext,
        channels: List<ChannelInfo>,
        message: String?,
        messageCount: Int,
        joinLeaveCycles: Int,
        interval: Double,
    ) {
        val session = TeamTalkSession(config, ctx::log)
        try {
            session.open()
            for (channel in channels) {
                ctx.checkStopped()
                session.joinChannel(channel.id, config.channelPassword)
                session.rejoinChannelId = channel.id
                session.rejoinChannelPassword = config.channelPassword
                for (cycle in 1..joinLeaveCycles) {
                    session.leaveChannel()
                    ctx.log("${channel.path}: left ($cycle/$joinLeaveCycles).")
                    if (interval > 0) ctx.sleep(interval)
                    session.joinChannel(channel.id, config.channelPassword)
                    ctx.log("${channel.path}: joined ($cycle/$joinLeaveCycles).")
                    if (interval > 0) ctx.sleep(interval)
                }
                if (message != null) {
                    for (index in 1..messageCount) {
                        session.sendChannelMessage(message, channel.id)
                        ctx.log("${channel.path}: sent message $index/$messageCount.")
                        if (interval > 0) ctx.sleep(interval)
                    }
                }
            }
        } finally {
            session.close()
        }
    }

    private fun churnBot(
        config: ConnectionConfig,
        ctx: RunContext,
        index: Int,
        total: Int,
        cycles: Int,
        interval: Double,
    ) {
        val botConfig = config.loginOnly().copy(nickname = "${config.nickname}-churn$index")
        val session = TeamTalkSession(botConfig, ctx::log)
        try {
            session.open()
            var completed = 0
            while (completed < cycles && !ctx.isStopped) {
                try {
                    if (!session.loggedIn) session.login()
                    ctx.log("Cycle ${completed + 1}/$cycles: logged in.")
                    session.logout()
                    ctx.log("Cycle ${completed + 1}/$cycles: logged out.")
                } catch (t: Throwable) {
                    if (t is ToolCancelledException) throw t
                    ctx.log("[kick-resistance] cycle ${completed + 1} interrupted: ${t.message}")
                    if (!session.checkAndReconnect()) {
                        ctx.log("Could not reconnect; stopping churn bot $index/$total.")
                        return
                    }
                    continue
                }
                completed++
                if (completed < cycles && interval > 0) ctx.sleep(interval)
            }
        } finally {
            session.close()
        }
    }

    // ----- helpers ---------------------------------------------------------- //

    private fun selectChannels(
        discovered: List<ChannelInfo>,
        config: ConnectionConfig,
        allChannels: Boolean,
    ): List<ChannelInfo> = when {
        allChannels -> discovered
        config.channelId != null -> discovered.filter { it.id == config.channelId }
        config.channelPath != null ->
            discovered.filter { it.path.equals(config.channelPath, ignoreCase = true) }
        else -> emptyList()
    }

    private fun requireMessageFits(message: String, label: String) {
        if (message.toByteArray(Charsets.UTF_8).size > 4096) {
            throw TeamTalkConfigException("$label is limited to 4096 UTF-8 bytes.")
        }
    }

    private fun printDiscovery(channels: List<ChannelInfo>, users: List<UserInfo>) {
        LogBus.log("Discovered ${channels.size} channel(s):")
        channels.forEach { LogBus.log("  ${it.id} — ${it.path}${if (it.passwordRequired) " (password)" else ""}") }
        LogBus.log("Discovered ${users.size} user(s):")
        users.forEach { user ->
            val nickname = if (user.nickname.isNotBlank() && user.nickname != user.username) {
                " (@${user.nickname})"
            } else {
                ""
            }
            LogBus.log("  ${user.username.ifBlank { user.displayName }}$nickname — ${user.channelPath}")
        }
    }
}
