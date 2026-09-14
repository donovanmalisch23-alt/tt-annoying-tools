package com.teamtalk.annoying.tools

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.ToolCancelledException
import com.teamtalk.annoying.core.Whitelist

/**
 * Runs one tool by id. Applies the shared gates (config validation and the
 * exact-host allowlist) and turns every failure into a logged result so the UI
 * never has to handle a raw exception.
 */
object ToolExecutor {

    fun run(
        toolId: String,
        config: ConnectionConfig,
        values: Map<String, String>,
        whitelist: List<String>,
        ctx: RunContext,
    ): ToolResult = try {
        val spec = ToolRegistry.spec(toolId)
        val validated = config.validated()

        if (spec.requiresWhitelist) {
            Whitelist.requireAllowed(validated.host, whitelist)
        }
        // Re-check the local-only gate up front so the UI can report it before
        // any SDK work happens.
        if (toolId == ToolRegistry.LOIC) {
            LocalGate.requireLocal(validated.host)
        }

        ctx.log("Starting: ${spec.title}.")
        val result = when (toolId) {
            ToolRegistry.MESSAGE_SPAMMER -> MessageSpammer.run(validated, values, ctx)
            ToolRegistry.LOGIN_SPAMMER -> LoginSpammer.run(validated, values, ctx)
            ToolRegistry.LEAVE_JOIN -> LeaveJoinSpammer.run(validated, values, ctx)
            ToolRegistry.IDLE_BOTS -> IdleBots.run(validated, values, ctx)
            ToolRegistry.RESPONSE_BOT -> ResponseBot.run(validated, values, ctx)
            ToolRegistry.SUITE -> SuiteRunner.run(validated, values, ctx)
            ToolRegistry.LOIC -> LoicFlood.run(validated, values, ctx)
            ToolRegistry.RAMP -> RampTest.run(validated, values, ctx)
            else -> throw IllegalStateException("Tool '$toolId' has no implementation.")
        }
        ctx.log(result.message)
        result
    } catch (cancelled: ToolCancelledException) {
        ctx.log("Stopped.")
        ToolResult(130, "Stopped by the user.")
    } catch (t: Throwable) {
        ctx.log("Error: ${t.message ?: t::class.java.simpleName}")
        ToolResult(2, t.message ?: "Run failed.")
    }
}
