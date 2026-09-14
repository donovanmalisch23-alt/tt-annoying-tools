package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.TeamTalkConfigException

enum class FieldKind { TEXT, SECRET, INT, DECIMAL, TOGGLE, CHOICE, MULTILINE }

data class FieldSpec(
    val key: String,
    val label: String,
    val kind: FieldKind = FieldKind.TEXT,
    val help: String = "",
    val default: String = "",
    val choices: List<String> = emptyList(),
)

data class ToolSpec(
    val id: String,
    val title: String,
    val tagline: String,
    val description: String,
    val fields: List<FieldSpec> = emptyList(),
    /** Target host must appear in the allowlist before this tool will connect. */
    val requiresWhitelist: Boolean = false,
    /** The UI asks for an explicit confirmation before starting. */
    val requiresConfirm: Boolean = false,
    /** Part of the gentle "soft" surface rather than the heavy load tools. */
    val soft: Boolean = true,
)

// ----- typed access to the raw string map the UI collects ------------------- //

/** Accepts `1_000` and `10,999` like the Python `comma_int` argparse type. */
fun parseLooseInt(value: String): Int? =
    value.trim().replace("_", "").replace(",", "").toIntOrNull()

fun Map<String, String>.str(key: String, default: String = ""): String =
    this[key]?.takeIf { it.isNotEmpty() } ?: default

fun Map<String, String>.bool(key: String): Boolean =
    (this[key] ?: "false").equals("true", ignoreCase = true)

fun Map<String, String>.int(key: String, default: Int): Int {
    val raw = this[key]?.takeIf { it.isNotBlank() } ?: return default
    return parseLooseInt(raw) ?: throw TeamTalkConfigException("'$raw' is not a whole number.")
}

fun Map<String, String>.double(key: String, default: Double): Double {
    val raw = this[key]?.takeIf { it.isNotBlank() } ?: return default
    return raw.toDoubleOrNull() ?: throw TeamTalkConfigException("'$raw' is not a number.")
}

fun Map<String, String>.requireText(key: String, label: String): String {
    val value = str(key).trim()
    if (value.isEmpty()) throw TeamTalkConfigException("$label is required.")
    return value
}

/** Splits repeatable/comma-separated user lists the way the CLI does. */
fun parseUserList(raw: String): List<String> =
    raw.split(',')
        .map { it.trim() }
        .filter { it.isNotEmpty() }
        .distinct()

object ToolRegistry {

    const val MESSAGE_SPAMMER = "message-spammer"
    const val LOGIN_SPAMMER = "login-spammer"
    const val LEAVE_JOIN = "leave-join"
    const val IDLE_BOTS = "idle-bots"
    const val RESPONSE_BOT = "response-bot"
    const val SUITE = "suite"
    const val LOIC = "loic"
    const val RAMP = "ramp"

    val specs: List<ToolSpec> = listOf(
        ToolSpec(
            id = MESSAGE_SPAMMER,
            title = "Message sender",
            tagline = "Send a channel or private message sequence",
            description = "Send a configurable channel or private text message sequence " +
                "through one SDK connection, with kick resistance and exact counts.",
            fields = listOf(
                FieldSpec("target", "Target", FieldKind.CHOICE, "Channel or private messages", "channel", listOf("channel", "private")),
                FieldSpec("message", "Message text", FieldKind.MULTILINE, "Up to 4096 UTF-8 bytes", "Oh Yeah!"),
                FieldSpec("users", "Recipients (usernames)", FieldKind.TEXT, "Comma-separated; private target only", ""),
                FieldSpec("count", "Messages per recipient", FieldKind.INT, "", "3"),
                FieldSpec("interval_ms", "Delay between messages (ms)", FieldKind.DECIMAL, "", "50"),
                FieldSpec("wait", "Startup wait (s)", FieldKind.DECIMAL, "", "0"),
            ),
        ),
        ToolSpec(
            id = LOGIN_SPAMMER,
            title = "Login / logout cycles",
            tagline = "Repeat login and logout on one connection",
            description = "Keeps one SDK connection open while it repeats authenticated " +
                "login/logout cycles without joining a channel.",
            fields = listOf(
                FieldSpec("cycles", "Cycles", FieldKind.INT, "Any positive integer", "5"),
                FieldSpec("interval_ms", "Delay between cycles (ms)", FieldKind.DECIMAL, "Zero is allowed", "200"),
                FieldSpec("wait", "Startup wait (s)", FieldKind.DECIMAL, "Maximum 300", "0"),
            ),
        ),
        ToolSpec(
            id = LEAVE_JOIN,
            title = "Channel leave / join",
            tagline = "Repeat leave and rejoin cycles",
            description = "Joins the configured channel, then leaves and rejoins it for " +
                "each requested cycle.",
            fields = listOf(
                FieldSpec("cycles", "Cycles", FieldKind.INT, "Any positive integer", "5"),
                FieldSpec("interval_ms", "Delay between leave and join (ms)", FieldKind.DECIMAL, "Zero is allowed", "200"),
                FieldSpec("wait", "Startup wait (s)", FieldKind.DECIMAL, "Maximum 300", "50"),
            ),
        ),
        ToolSpec(
            id = IDLE_BOTS,
            title = "Idle bots",
            tagline = "Park logged-in clients on the server",
            description = "Launch idle bots that connect, log in, join the channel and sit " +
                "there occupying server slots. Android sustains far fewer connections " +
                "than the desktop tools, so the count is capped conservatively.",
            fields = listOf(
                FieldSpec("count", "Idle bots", FieldKind.INT, "1 to 128 on Android", "1"),
                FieldSpec("start_delay_ms", "Delay between bot launches (ms)", FieldKind.INT, "Spreads the connect burst", "150"),
                FieldSpec("connect_attempts", "Connect attempts per bot", FieldKind.INT, "", "5"),
            ),
            requiresWhitelist = true,
            requiresConfirm = true,
            soft = false,
        ),
        ToolSpec(
            id = RESPONSE_BOT,
            title = "Response bot",
            tagline = "Trigger-based benign replies",
            description = "Listens in the configured channel and replies only to an explicit " +
                "trigger, from allowlisted users, with a per-user cooldown. The default " +
                "reply is intentionally benign.",
            fields = listOf(
                FieldSpec("trigger", "Trigger prefix", FieldKind.TEXT, "", "!hello"),
                FieldSpec("response", "Response template", FieldKind.TEXT, "{username}, {user_id}, {message}", "Hi {username}, thanks for your message!"),
                FieldSpec("allow_users", "Allowlisted usernames", FieldKind.TEXT, "Comma-separated; blank plus Allow all means everyone", ""),
                FieldSpec("allow_all", "Allow any user", FieldKind.TOGGLE, "", "false"),
                FieldSpec("cooldown", "Per-user cooldown (s)", FieldKind.DECIMAL, "Minimum 5", "30"),
                FieldSpec("max_responses", "Max responses (0 = unlimited)", FieldKind.INT, "", "100"),
            ),
        ),
        ToolSpec(
            id = SUITE,
            title = "Combined suite",
            tagline = "Discover targets, then run every selected test",
            description = "Discovers channels and users, then runs login cycles, join/leave " +
                "cycles, channel messages and private messages. Concurrent mode splits the " +
                "work across dedicated bots on their own connections.",
            fields = listOf(
                FieldSpec("all_channels", "All discovered channels", FieldKind.TOGGLE, "", "true"),
                FieldSpec("all_users", "All discovered users", FieldKind.TOGGLE, "", "true"),
                FieldSpec("users", "Recipients (usernames)", FieldKind.TEXT, "Comma-separated; used when All users is off", ""),
                FieldSpec("channel_message", "Channel message", FieldKind.TEXT, "Blank skips channel messages", ""),
                FieldSpec("private_message", "Private message", FieldKind.TEXT, "Blank skips private messages", ""),
                FieldSpec("message_count", "Messages per target", FieldKind.INT, "", "1"),
                FieldSpec("join_leave_cycles", "Join/leave cycles", FieldKind.INT, "0 disables", "0"),
                FieldSpec("login_cycles", "Login/logout cycles", FieldKind.INT, "0 disables", "0"),
                FieldSpec("interval", "Delay between operations (s)", FieldKind.DECIMAL, "", "0.2"),
                FieldSpec("concurrent", "Concurrent bots", FieldKind.TOGGLE, "", "false"),
                FieldSpec("churn_bots", "Churn bots", FieldKind.INT, "Requires concurrent", "0"),
                FieldSpec("churn_cycles", "Cycles per churn bot", FieldKind.INT, "", "10"),
                FieldSpec("bot_per_channel", "One bot per channel", FieldKind.TOGGLE, "Requires concurrent", "false"),
                FieldSpec("bot_per_user", "One bot per user", FieldKind.TOGGLE, "Requires concurrent", "false"),
                FieldSpec("sweep_interval", "New-joiner sweep interval (s)", FieldKind.DECIMAL, "Continuous all-users mode", "0.5"),
            ),
            requiresWhitelist = true,
            requiresConfirm = true,
            soft = false,
        ),
        ToolSpec(
            id = LOIC,
            title = "Local flood test",
            tagline = "LOIC-style TCP/UDP probe, this device only",
            description = "Reproduces LOIC's TCP and UDP junk floods against a server this " +
                "device can reach locally, and measures the impact with a before/during/after " +
                "SDK probe pair. Off-device targets are refused.",
            fields = listOf(
                FieldSpec("mode", "Mode", FieldKind.CHOICE, "", "both", listOf("both", "tcp", "udp")),
                FieldSpec("threads", "Threads per mode", FieldKind.INT, "1 to 64", "8"),
                FieldSpec("duration", "Duration (s)", FieldKind.DECIMAL, "Maximum 60", "10"),
                FieldSpec("probe", "Run service probes", FieldKind.TOGGLE, "", "true"),
                FieldSpec("probe_channel", "Probe channel", FieldKind.TEXT, "", "/"),
            ),
            requiresConfirm = true,
            soft = false,
        ),
        ToolSpec(
            id = RAMP,
            title = "Ramp / breaking point",
            tagline = "Step the flood up until the server stops coping",
            description = "Ramps the flood in geometric stages, classifying each stage as " +
                "healthy, degraded or broken, and reports where the server stops coping. " +
                "Any host in the allowlist is a legal target.",
            fields = listOf(
                FieldSpec("start_threads", "Start threads", FieldKind.INT, "", "1"),
                FieldSpec("ramp_factor", "Ramp factor", FieldKind.INT, "", "2"),
                FieldSpec("max_threads", "Max threads", FieldKind.INT, "1 to 1024", "64"),
                FieldSpec("stage_duration", "Seconds per stage", FieldKind.DECIMAL, "1 to 60", "10"),
                FieldSpec("mode", "Mode", FieldKind.CHOICE, "", "both", listOf("both", "tcp", "udp")),
                FieldSpec("probe_channel", "Probe channel", FieldKind.TEXT, "", "/"),
            ),
            requiresWhitelist = true,
            requiresConfirm = true,
            soft = false,
        ),
    )

    fun spec(id: String): ToolSpec =
        specs.firstOrNull { it.id == id }
            ?: throw TeamTalkConfigException("Unknown tool '$id'.")

    fun defaultValues(id: String): Map<String, String> =
        spec(id).fields.associate { it.key to it.default }
}
