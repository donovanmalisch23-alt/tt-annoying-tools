package com.teamtalk.annoying.core

import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow

/**
 * Single log sink for every tool plus the Compose UI. Bounded so a long soak
 * run cannot grow the process without limit, and observable so the log screen
 * updates live while a test runs.
 */
object LogBus {

    data class Line(val timestampMs: Long, val text: String)

    private const val MAX_LINES = 2_000
    private val lock = Any()
    private val buffer = ArrayDeque<Line>()

    private val _lines = MutableStateFlow<List<Line>>(emptyList())
    val lines: StateFlow<List<Line>> = _lines.asStateFlow()

    fun log(text: String) {
        synchronized(lock) {
            buffer.addLast(Line(System.currentTimeMillis(), text))
            while (buffer.size > MAX_LINES) buffer.removeFirst()
            _lines.value = buffer.toList()
        }
    }

    fun clear() {
        synchronized(lock) {
            buffer.clear()
            _lines.value = emptyList()
        }
    }

    fun dump(): String = synchronized(lock) {
        buffer.joinToString("\n") { it.text }
    }
}
