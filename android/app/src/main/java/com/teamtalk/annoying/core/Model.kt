package com.teamtalk.annoying.core

/** Base error for anything the tools raise, mirroring tt_teamtalk.TeamTalkError. */
open class TeamTalkException(message: String, cause: Throwable? = null) :
    RuntimeException(message, cause)

/** A missing or invalid setting, mirroring TeamTalkConfigurationError. */
class TeamTalkConfigException(message: String) : TeamTalkException(message)

/** The native SDK could not be loaded or reported a failure. */
class TeamTalkSdkException(message: String, cause: Throwable? = null) :
    TeamTalkException(message, cause)

/** Raised when the SDK license terms have not been accepted yet. */
class SdkLicenseRequiredException :
    TeamTalkException("The TeamTalk 5 SDK license must be accepted before the SDK can be used.")

/** Raised when a run is stopped by the user. */
class ToolCancelledException : RuntimeException("Run cancelled")

/**
 * Everything needed to open a TeamTalk session. Mirrors the Python
 * ConnectionConfig, including the "blank username/password means anonymous"
 * convention.
 */
data class ConnectionConfig(
    val host: String = "",
    val tcpPort: Int = 10333,
    val udpPort: Int = 10333,
    val username: String = "",
    val password: String = "",
    val nickname: String = "tt-android-client",
    val clientName: String = "TT Annoying Tools Android",
    val encrypted: Boolean = false,
    val channelId: Int? = null,
    val channelPath: String? = null,
    val channelPassword: String = "",
    val commandTimeoutSec: Double = 15.0,
    val kickResistance: Boolean = true,
    val reconnectDelaySec: Double = 3.5,
    val licenseName: String? = null,
    val licenseKey: String = "",
) {
    /** Throws TeamTalkConfigException for anything the tools cannot work with. */
    fun validated(): ConnectionConfig {
        val trimmedHost = host.trim()
        if (trimmedHost.isEmpty()) {
            throw TeamTalkConfigException("Server host is required; refusing to guess a server.")
        }
        if (tcpPort !in 1..65535 || udpPort !in 1..65535) {
            throw TeamTalkConfigException("TCP and UDP ports must be between 1 and 65535.")
        }
        if (commandTimeoutSec <= 0) {
            throw TeamTalkConfigException("Command timeout must be greater than zero.")
        }
        if (reconnectDelaySec < 0) {
            throw TeamTalkConfigException("Reconnect delay cannot be negative.")
        }
        channelId?.let {
            if (it < 0) throw TeamTalkConfigException("Channel ID cannot be negative.")
        }
        val path = channelPath?.trim()?.takeIf { it.isNotEmpty() }?.let {
            if (it.startsWith("/")) it else "/$it"
        }
        return copy(host = trimmedHost, channelPath = path)
    }

    /** A login-only copy: used by tools that must not auto-join a channel. */
    fun loginOnly(): ConnectionConfig =
        copy(channelId = null, channelPath = null, channelPassword = "")
}

data class ChannelInfo(
    val id: Int,
    val parentId: Int,
    val name: String,
    val path: String,
    val passwordRequired: Boolean,
    val hidden: Boolean,
)

data class UserInfo(
    val id: Int,
    val nickname: String,
    val username: String,
    val channelId: Int,
    val channelPath: String,
) {
    val displayName: String
        get() = nickname.ifBlank { username.ifBlank { "user $id" } }
}

data class TextEvent(
    val type: Int,
    val fromUserId: Int,
    val fromUsername: String,
    val toUserId: Int,
    val channelId: Int,
    val text: String,
    val more: Boolean,
)

/** Exact-host allowlist, identical in behaviour to the Python suite's gate. */
object Whitelist {

    fun normalize(host: String): String {
        var normalized = host.trim().lowercase().trimEnd('.')
        if (normalized.startsWith("[") && normalized.endsWith("]")) {
            normalized = normalized.substring(1, normalized.length - 1)
        }
        return normalized
    }

    fun parse(text: String): List<String> =
        text.lineSequence()
            .map { it.substringBefore('#').trim() }
            .filter { it.isNotEmpty() }
            .map { normalize(it) }
            .distinct()
            .toList()

    fun isAllowed(host: String, entries: List<String>): Boolean =
        normalize(host) in entries.map { normalize(it) }

    fun requireAllowed(host: String, entries: List<String>) {
        if (entries.isEmpty()) {
            throw TeamTalkConfigException(
                "The server allowlist is empty; add one hostname or IP per line first.",
            )
        }
        if (!isAllowed(host, entries)) {
            throw TeamTalkConfigException(
                "'$host' is not in the server allowlist. Add it before running this test.",
            )
        }
    }
}
