package com.teamtalk.annoying.core

import dk.bearware.Channel
import dk.bearware.ClientEvent
import dk.bearware.ClientFlag
import dk.bearware.IntPtr
import dk.bearware.TTMessage
import dk.bearware.TeamTalk5
import dk.bearware.TeamTalkBase
import dk.bearware.TextMessage
import dk.bearware.TextMsgType
import dk.bearware.User
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Blocking wrapper around the TeamTalk Java SDK, ported from
 * `TeamTalkSession` in tt_teamtalk.py.
 *
 * A single background "event pump" thread is the only caller of
 * `getMessage()`; command calls (`doLogin*`, `doJoinChannelByID`,
 * `doTextMessage`, ...) come from the run thread and their answers are read
 * back out of the pump's buffer. This keeps the native queue single-consumer,
 * which is the constraint the Python implementation documents.
 */
class TeamTalkSession(
    val config: ConnectionConfig,
    private val log: (String) -> Unit = { LogBus.log(it) },
) : AutoCloseable {

    init {
        // The SDK license must be accepted before any SDK file is used. The
        // caller (RunContext / UI) checks this too; this is the backstop.
        Sdk.ensureLoaded()
    }

    @Volatile
    private var tt: TeamTalkBase = newClient()

    val client: TeamTalkBase get() = tt

    @Volatile
    var connected: Boolean = false
        private set

    @Volatile
    var loggedIn: Boolean = false
        private set

    @Volatile
    var channelId: Int? = null
        private set

    /** Working channel used to resume after a kick (set by bots that pick a channel). */
    @Volatile
    var rejoinChannelId: Int? = null

    @Volatile
    var rejoinChannelPassword: String = ""

    private val events = LinkedBlockingQueue<TTMessage>()
    private val closed = AtomicBoolean(false)

    @Volatile
    private var pumpStopped = false

    @Volatile
    private var pumpThread: Thread? = null

    private fun newClient(): TeamTalkBase = try {
        Sdk.applyLicense(config.licenseName, config.licenseKey)
        TeamTalk5()
    } catch (t: Throwable) {
        throw TeamTalkSdkException("Failed to initialize TeamTalk: ${t.message}", t)
    }

    // ----- lifecycle -------------------------------------------------------- //

    /** Connect and log in (and join the configured channel) if not already open. */
    fun open() {
        if (connected) {
            if (!loggedIn) login()
            return
        }
        connectAndLogin()
        rejoinWorkingChannel()
    }

    private fun connectAndLogin() {
        val accepted = try {
            client.connect(
                config.host,
                config.tcpPort,
                config.udpPort,
                0,
                0,
                config.encrypted,
            )
        } catch (t: Throwable) {
            throw TeamTalkException("Could not start connection: ${t.message}", t)
        }
        if (!accepted) throw TeamTalkException("TeamTalk rejected the connection request.")
        waitFor(setOf(ClientEvent.CLIENTEVENT_CON_SUCCESS), "connection", null)
        connected = true
        login()
    }

    fun login() {
        if (loggedIn) return
        if (!connected) throw TeamTalkConfigException("Connect before logging in.")
        val command = checkCommand(
            client.doLoginEx(
                config.nickname,
                config.username,
                config.password,
                config.clientName,
            ),
            "login",
        )
        waitFor(setOf(ClientEvent.CLIENTEVENT_CMD_MYSELF_LOGGEDIN), "login", command)
        loggedIn = true
    }

    fun logout() {
        if (!loggedIn) return
        val command = checkCommand(client.doLogout(), "logout")
        waitFor(setOf(ClientEvent.CLIENTEVENT_CMD_MYSELF_LOGGEDOUT), "logout", command)
        loggedIn = false
    }

    // ----- channels --------------------------------------------------------- //

    fun joinChannel(channelId: Int, password: String = ""): Int {
        if (channelId < 0) throw TeamTalkConfigException("Channel ID cannot be negative.")
        // Joining the channel we are already in is a no-op; TeamTalk rejects a
        // duplicate join with a command error, and kick recovery calls this.
        if (evaluate { client.getMyChannelID() } == channelId) {
            this.channelId = channelId
            return channelId
        }
        val command = checkCommand(client.doJoinChannelByID(channelId, password), "join channel")
        waitFor(setOf(ClientEvent.CLIENTEVENT_CMD_SUCCESS), "join channel", command)
        this.channelId = channelId
        return channelId
    }

    fun joinChannelPath(path: String, password: String = ""): Int {
        val deadlineNs = System.nanoTime() + (config.commandTimeoutSec * 1_000_000_000.0).toLong()
        while (System.nanoTime() < deadlineNs) {
            val id = evaluate { client.getChannelIDFromPath(path) }
            // TeamTalk returns zero for "not found"; valid channel IDs start at one.
            if (id > 0) return joinChannel(id, password)
            val remainingMs = ((deadlineNs - System.nanoTime()) / 1_000_000L).coerceIn(1L, 100L)
            nextEvent(remainingMs)
        }
        throw TeamTalkException("TeamTalk channel path was not found: $path")
    }

    fun leaveChannel() {
        val command = checkCommand(client.doLeaveChannel(), "leave channel")
        waitFor(setOf(ClientEvent.CLIENTEVENT_CMD_SUCCESS), "leave channel", command)
        channelId = null
    }

    fun currentChannelId(): Int {
        channelId?.let { return it }
        val id = evaluate { client.getMyChannelID() }
        // The SDK reports zero (not a negative number) when in no channel.
        if (id <= 0) {
            throw TeamTalkConfigException("The client is not in a channel; set a channel first.")
        }
        channelId = id
        return id
    }

    fun listChannels(): List<ChannelInfo> {
        if (!loggedIn) throw TeamTalkConfigException("Connect and log in before listing channels.")
        val channels = try {
            val none: Array<Channel>? = null
            val countPtr = IntPtr()
            if (!client.getServerChannels(none, countPtr)) return emptyList()
            val count = countPtr.value
            if (count <= 0) return emptyList()
            val array = Array(count) { Channel() }
            val filled = IntPtr(count)
            if (!client.getServerChannels(array, filled)) array
            else array.copyOf(filled.value.coerceIn(0, count))
        } catch (t: Throwable) {
            throw TeamTalkException("Could not retrieve TeamTalk channels: ${t.message}", t)
        }
        return channels.mapNotNull { channel ->
            val id = channel.nChannelID
            if (id < 0) return@mapNotNull null
            val name = channel.szName ?: ""
            val path = evaluateText { client.getChannelPath(id) } ?: ""
            ChannelInfo(
                id = id,
                parentId = channel.nParentID,
                name = name,
                path = path.ifEmpty { if (name.isEmpty()) "/" else name },
                passwordRequired = channel.bPassword,
                hidden = (channel.uChannelType and 0x0040) != 0,
            )
        }.sortedWith(compareBy({ it.path.lowercase() }, { it.id }))
    }

    fun listUsers(includeSelf: Boolean = false): List<UserInfo> {
        if (!loggedIn) throw TeamTalkConfigException("Connect and log in before listing users.")
        val users = try {
            val none: Array<User>? = null
            val countPtr = IntPtr()
            if (!client.getServerUsers(none, countPtr)) return emptyList()
            val count = countPtr.value
            if (count <= 0) return emptyList()
            val array = Array(count) { User() }
            val filled = IntPtr(count)
            if (!client.getServerUsers(array, filled)) array
            else array.copyOf(filled.value.coerceIn(0, count))
        } catch (t: Throwable) {
            throw TeamTalkException("Could not retrieve TeamTalk users: ${t.message}", t)
        }
        val ownId = evaluate { client.getMyUserID() }
        return users.mapNotNull { user ->
            val id = user.nUserID
            if (id < 0 || (!includeSelf && id == ownId)) return@mapNotNull null
            val channel = user.nChannelID
            UserInfo(
                id = id,
                nickname = user.szNickname ?: "",
                username = user.szUsername ?: "",
                channelId = channel,
                channelPath = evaluateText { client.getChannelPath(channel) } ?: "",
            )
        }.sortedWith(compareBy({ it.displayName.lowercase() }, { it.id }))
    }

    // ----- messaging -------------------------------------------------------- //

    /** Send exactly one channel or private message. */
    fun sendText(text: String, channelId: Int? = null, userId: Int? = null) {
        if (text.isEmpty()) throw TeamTalkConfigException("Message text cannot be empty.")
        if ((channelId == null) == (userId == null)) {
            throw TeamTalkConfigException("Choose exactly one message target.")
        }
        val message = TextMessage()
        val action: String
        if (channelId != null) {
            message.nMsgType = TextMsgType.MSGTYPE_CHANNEL
            message.nChannelID = channelId
            action = "send channel message"
        } else {
            message.nMsgType = TextMsgType.MSGTYPE_USER
            message.nToUserID = userId!!
            action = "send private message"
        }
        message.szMessage = text
        val command = checkCommand(client.doTextMessage(message), action)
        waitFor(setOf(ClientEvent.CLIENTEVENT_CMD_SUCCESS), action, command)
    }

    fun sendChannelMessage(text: String, channelId: Int? = null) {
        sendText(text, channelId = channelId ?: currentChannelId())
    }

    fun sendPrivateMessage(text: String, userId: Int) {
        if (userId < 0) throw TeamTalkConfigException("User ID cannot be negative.")
        sendText(text, userId = userId)
    }

    // ----- online / kick resistance ----------------------------------------- //

    /** Non-destructive online probe: connected, logged in and known to the server. */
    fun isOnline(): Boolean {
        if (closed.get() || !connected || !loggedIn) return false
        return evaluate { client.getMyUserID() } > 0
    }

    fun isAuthorized(): Boolean =
        evaluate { client.getFlags() and ClientFlag.CLIENT_AUTHORIZED } == ClientFlag.CLIENT_AUTHORIZED

    /**
     * True when [message] is a connection-lost/failed/crypt event. Resets the
     * session flags so an idle event-loop bot notices a kick the moment the
     * event is dequeued.
     */
    fun isConnectionFailure(message: TTMessage?): Boolean {
        val event = message?.nClientEvent ?: return false
        if (event != ClientEvent.CLIENTEVENT_CON_LOST &&
            event != ClientEvent.CLIENTEVENT_CON_FAILED &&
            event != ClientEvent.CLIENTEVENT_CON_CRYPT_ERROR
        ) {
            return false
        }
        connected = false
        loggedIn = false
        channelId = null
        return true
    }

    /** Rebuild the native client and re-establish connection + login. */
    fun reconnect(): Boolean {
        stopPump()
        val old = tt
        runCatching { old.disconnect() }
        // close() also zeroes the SDK's internal handle, so the finalizer
        // cannot double-release it.
        runCatching { old.close() }
        connected = false
        loggedIn = false
        channelId = null
        tt = newClient()
        startPump()
        return try {
            connectAndLogin()
            try {
                rejoinWorkingChannel()
            } catch (t: Throwable) {
                log("[kick-resistance] could not rejoin channel: ${t.message}")
            }
            isOnline()
        } catch (t: Throwable) {
            log("[kick-resistance] reconnect failed: ${t.message}")
            false
        }
    }

    /**
     * Wait the reconnect delay, then reconnect if still offline. A client that
     * is online but was moved out of its working channel is rejoined instead.
     */
    fun checkAndReconnect(): Boolean {
        if (config.reconnectDelaySec > 0) sleepSeconds(config.reconnectDelaySec)
        if (isOnline()) {
            if (!inWorkingChannel()) {
                log("[kick-resistance] not in the working channel; rejoining.")
                runCatching { rejoinWorkingChannel() }
                    .onFailure { log("[kick-resistance] could not rejoin channel: ${it.message}") }
            }
            return true
        }
        if (!config.kickResistance) return false
        log("[kick-resistance] reconnecting after disconnect…")
        return try {
            reconnect()
        } catch (t: Throwable) {
            log("[kick-resistance] reconnect failed: ${t.message}")
            false
        }
    }

    private fun rejoinWorkingChannel() {
        rejoinChannelId?.let { joinChannel(it, rejoinChannelPassword); return }
        config.channelId?.let { joinChannel(it, config.channelPassword); return }
        config.channelPath?.let { joinChannelPath(it, config.channelPassword) }
    }

    private fun inWorkingChannel(): Boolean {
        val target = rejoinChannelId
            ?: config.channelId
            ?: config.channelPath?.let { path ->
                evaluate { client.getChannelIDFromPath(path) }.takeIf { it > 0 }
            }
            ?: return true
        return evaluate { client.getMyChannelID() } == target
    }

    override fun close() {
        if (!closed.compareAndSet(false, true)) return
        stopPump()
        runCatching { if (connected) client.disconnect() }
        runCatching { client.close() }
        connected = false
        loggedIn = false
        // The native client tears down its internal threads asynchronously;
        // give it a moment so the next client in this process starts cleanly.
        runCatching { Thread.sleep(SHUTDOWN_SETTLE_MS) }
    }

    // ----- event pump ------------------------------------------------------- //

    private fun startPump() {
        if (pumpThread?.isAlive == true) return
        pumpStopped = false
        events.clear()
        val thread = Thread({
            while (!pumpStopped) {
                val active = tt
                try {
                    val message = TTMessage()
                    if (active.getMessage(message, PUMP_SCAN_MS) && message.nClientEvent != 0) {
                        events.offer(message)
                    }
                } catch (t: Throwable) {
                    if (pumpStopped) break
                    try {
                        Thread.sleep(50)
                    } catch (interrupted: InterruptedException) {
                        Thread.currentThread().interrupt()
                        break
                    }
                }
            }
        }, "tt-event-pump")
        thread.isDaemon = true
        pumpThread = thread
        thread.start()
    }

    private fun stopPump() {
        pumpStopped = true
        val thread = pumpThread ?: return
        if (thread.isAlive) {
            // The pump can be blocked inside getMessage for up to one scan
            // interval, so wait that long plus a margin before releasing the client.
            runCatching { thread.join(PUMP_SCAN_MS + 1_000L) }
        }
        pumpThread = null
    }

    /** Pop one buffered event, waiting up to [timeoutMs] for one. Null if none. */
    fun nextEvent(timeoutMs: Long): TTMessage? =
        try {
            events.poll(timeoutMs.coerceAtLeast(0L), TimeUnit.MILLISECONDS)
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
            null
        }

    /** Convenience for idle bots that just want to drain events. */
    fun poll(waitMs: Long = 1_000L): TTMessage? = nextEvent(waitMs)

    // ----- command/event plumbing ------------------------------------------- //

    private fun waitFor(expected: Set<Int>, action: String, commandId: Int?): TTMessage? {
        val deadlineNs = System.nanoTime() + (config.commandTimeoutSec * 1_000_000_000.0).toLong()
        var result: TTMessage? = null
        var commandComplete = commandId == null

        while (System.nanoTime() < deadlineNs) {
            val remainingMs = ((deadlineNs - System.nanoTime()) / 1_000_000L).coerceAtLeast(1L)
            val message = nextEvent(remainingMs) ?: continue
            val event = message.nClientEvent
            val source = message.nSource
            if (event == ClientEvent.CLIENTEVENT_NONE) continue

            if (event in expected && (commandId == null || source == commandId)) return message
            if (event == ClientEvent.CLIENTEVENT_CMD_SUCCESS && commandId == null) return message
            if (event == ClientEvent.CLIENTEVENT_CMD_SUCCESS && source == commandId) result = message

            if (!commandComplete &&
                event == ClientEvent.CLIENTEVENT_CMD_PROCESSING &&
                source == commandId &&
                !message.bActive
            ) {
                commandComplete = true
                result?.let { return it }
            }

            if (event == ClientEvent.CLIENTEVENT_CON_LOST ||
                event == ClientEvent.CLIENTEVENT_CON_FAILED ||
                event == ClientEvent.CLIENTEVENT_CON_CRYPT_ERROR
            ) {
                connected = false
                loggedIn = false
                channelId = null
                throw TeamTalkException(
                    "$action failed: ${errorText(message).ifBlank { "connection event" }}",
                )
            }

            if (event == ClientEvent.CLIENTEVENT_CMD_ERROR &&
                (commandId == null || source == commandId)
            ) {
                throw TeamTalkException("$action failed: ${errorText(message)}")
            }
        }
        result?.let { return it }
        throw TeamTalkException(
            "Timed out waiting for $action after ${config.commandTimeoutSec}s.",
        )
    }

    private fun checkCommand(commandId: Int, action: String): Int {
        if (commandId < 0) {
            // TT_Do* returns a negative id for a local (pre-send) failure such
            // as "not connected"; there is no server error message to look up.
            throw TeamTalkException(
                "$action was rejected by TeamTalk locally (not connected or invalid state).",
            )
        }
        return commandId
    }

    private fun errorText(message: TTMessage?): String {
        val error = message?.clienterrormsg ?: return ""
        val code = error.nErrorNo
        val description = error.szErrorMsg?.takeIf { it.isNotBlank() }
        if (description != null) return description
        return if (code >= 0) Sdk.errorText(code) else ""
    }

    private fun evaluate(block: () -> Int): Int =
        try {
            block()
        } catch (t: Throwable) {
            -1
        }

    private fun evaluateText(block: () -> String?): String? =
        try {
            block()?.takeIf { it.isNotBlank() }
        } catch (t: Throwable) {
            null
        }

    private fun sleepSeconds(seconds: Double) {
        try {
            Thread.sleep((seconds * 1000).toLong())
        } catch (interrupted: InterruptedException) {
            Thread.currentThread().interrupt()
        }
    }

    companion object {
        // Int because the SDK's getMessage expects an int millisecond wait.
        private const val PUMP_SCAN_MS = 500
        private const val SHUTDOWN_SETTLE_MS = 500L

        /** Stable fields from an incoming TextMessage event. */
        fun textEventFrom(message: TTMessage?): TextEvent? {
            val text = message?.textmessage ?: return null
            return TextEvent(
                type = text.nMsgType,
                fromUserId = text.nFromUserID,
                fromUsername = text.szFromUsername ?: "",
                toUserId = text.nToUserID,
                channelId = text.nChannelID,
                text = text.szMessage ?: "",
                more = text.bMore,
            )
        }
    }
}
