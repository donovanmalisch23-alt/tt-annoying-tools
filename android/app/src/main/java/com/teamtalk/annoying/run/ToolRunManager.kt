package com.teamtalk.annoying.run

import com.teamtalk.annoying.core.ConnectionConfig
import com.teamtalk.annoying.core.LogBus
import com.teamtalk.annoying.tools.RunContext
import com.teamtalk.annoying.tools.ToolExecutor
import com.teamtalk.annoying.tools.ToolRegistry
import com.teamtalk.annoying.tools.ToolResult
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Owns the single running tool. The UI observes [state]; [com.teamtalk.annoying.service.RunService]
 * only exists to keep the process alive and show progress, so it watches this too.
 */
object ToolRunManager {

    enum class Status { IDLE, RUNNING, DONE }

    data class RunState(
        val status: Status = Status.IDLE,
        val toolId: String = "",
        val toolTitle: String = "",
        val lastResult: ToolResult? = null,
    )

    private val _state = MutableStateFlow(RunState())
    val state: StateFlow<RunState> = _state.asStateFlow()

    @Volatile
    private var worker: Thread? = null

    @Volatile
    private var context: RunContext? = null

    val isRunning: Boolean get() = worker?.isAlive == true

    val currentToolTitle: String get() = _state.value.toolTitle

    @Synchronized
    fun start(
        toolId: String,
        config: ConnectionConfig,
        values: Map<String, String>,
        whitelist: List<String>,
    ) {
        if (isRunning) throw IllegalStateException("A run is already in progress.")
        val spec = ToolRegistry.spec(toolId)
        val ctx = RunContext(spec.title)
        context = ctx
        _state.value = RunState(Status.RUNNING, toolId, spec.title, null)
        LogBus.log("=== ${spec.title} ===")

        val thread = Thread({
            val result = ToolExecutor.run(toolId, config, values, whitelist, ctx)
            context = null
            _state.value = RunState(Status.DONE, toolId, spec.title, result)
        }, "tool-run")
        thread.isDaemon = false
        worker = thread
        thread.start()
    }

    fun requestStop() {
        context?.requestStop()
        LogBus.log("Stop requested.")
    }
}
