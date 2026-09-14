package com.teamtalk.annoying.core

import android.content.Context

/**
 * Persists the connection settings, the exact-host allowlist and the SDK
 * license decision. This is the Android equivalent of `teamtalk.env` plus
 * `whitelist.txt` plus the `.tt-sdk-license-accepted` marker file.
 */
class ConfigStore(context: Context) {

    private val prefs =
        context.applicationContext.getSharedPreferences("tt_annoying", Context.MODE_PRIVATE)

    fun loadConfig(): ConnectionConfig = ConnectionConfig(
        host = prefs.getString(KEY_HOST, "") ?: "",
        tcpPort = prefs.getInt(KEY_TCP, 10333),
        udpPort = prefs.getInt(KEY_UDP, 10333),
        username = prefs.getString(KEY_USERNAME, "") ?: "",
        password = prefs.getString(KEY_PASSWORD, "") ?: "",
        nickname = prefs.getString(KEY_NICKNAME, "tt-android-client") ?: "tt-android-client",
        clientName = prefs.getString(KEY_CLIENT, "TT Annoying Tools Android")
            ?: "TT Annoying Tools Android",
        encrypted = prefs.getBoolean(KEY_ENCRYPTED, false),
        channelId = prefs.getInt(KEY_CHANNEL_ID, -1).takeIf { it >= 0 },
        channelPath = prefs.getString(KEY_CHANNEL_PATH, "")?.takeIf { it.isNotBlank() },
        channelPassword = prefs.getString(KEY_CHANNEL_PASSWORD, "") ?: "",
        commandTimeoutSec = prefs.getFloat(KEY_TIMEOUT, 15f).toDouble(),
        kickResistance = prefs.getBoolean(KEY_KICK, true),
        reconnectDelaySec = prefs.getFloat(KEY_RECONNECT, 3.5f).toDouble(),
        licenseName = prefs.getString(KEY_LICENSE_NAME, "")?.takeIf { it.isNotBlank() },
        licenseKey = prefs.getString(KEY_LICENSE_KEY, "") ?: "",
    )

    fun saveConfig(config: ConnectionConfig) {
        prefs.edit()
            .putString(KEY_HOST, config.host)
            .putInt(KEY_TCP, config.tcpPort)
            .putInt(KEY_UDP, config.udpPort)
            .putString(KEY_USERNAME, config.username)
            .putString(KEY_PASSWORD, config.password)
            .putString(KEY_NICKNAME, config.nickname)
            .putString(KEY_CLIENT, config.clientName)
            .putBoolean(KEY_ENCRYPTED, config.encrypted)
            .putInt(KEY_CHANNEL_ID, config.channelId ?: -1)
            .putString(KEY_CHANNEL_PATH, config.channelPath ?: "")
            .putString(KEY_CHANNEL_PASSWORD, config.channelPassword)
            .putFloat(KEY_TIMEOUT, config.commandTimeoutSec.toFloat())
            .putBoolean(KEY_KICK, config.kickResistance)
            .putFloat(KEY_RECONNECT, config.reconnectDelaySec.toFloat())
            .putString(KEY_LICENSE_NAME, config.licenseName ?: "")
            .putString(KEY_LICENSE_KEY, config.licenseKey)
            .apply()
    }

    fun loadWhitelistText(): String = prefs.getString(KEY_WHITELIST, DEFAULT_WHITELIST) ?: ""

    fun saveWhitelistText(text: String) {
        prefs.edit().putString(KEY_WHITELIST, text).apply()
    }

    fun loadWhitelist(): List<String> = Whitelist.parse(loadWhitelistText())

    fun isSdkLicenseAccepted(): Boolean = prefs.getBoolean(KEY_LICENSE_ACCEPTED, false)

    fun setSdkLicenseAccepted(accepted: Boolean) {
        prefs.edit().putBoolean(KEY_LICENSE_ACCEPTED, accepted).apply()
    }

    private companion object {
        const val DEFAULT_WHITELIST =
            "# Exact TeamTalk hostnames or IP addresses allowed to be tested.\n" +
                "# Add one server per line. Comments starting with # are ignored.\n"

        const val KEY_HOST = "host"
        const val KEY_TCP = "tcp"
        const val KEY_UDP = "udp"
        const val KEY_USERNAME = "username"
        const val KEY_PASSWORD = "password"
        const val KEY_NICKNAME = "nickname"
        const val KEY_CLIENT = "client"
        const val KEY_ENCRYPTED = "encrypted"
        const val KEY_CHANNEL_ID = "channel_id"
        const val KEY_CHANNEL_PATH = "channel_path"
        const val KEY_CHANNEL_PASSWORD = "channel_password"
        const val KEY_TIMEOUT = "timeout"
        const val KEY_KICK = "kick_resistance"
        const val KEY_RECONNECT = "reconnect"
        const val KEY_LICENSE_NAME = "license_name"
        const val KEY_LICENSE_KEY = "license_key"
        const val KEY_LICENSE_ACCEPTED = "license_accepted"
        const val KEY_WHITELIST = "whitelist"
    }
}
