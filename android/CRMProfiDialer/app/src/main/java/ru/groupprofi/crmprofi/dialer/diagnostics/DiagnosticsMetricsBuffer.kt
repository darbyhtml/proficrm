package ru.groupprofi.crmprofi.dialer.diagnostics

import java.util.concurrent.ConcurrentLinkedDeque
import kotlin.math.min

/**
 * Ring-buffer диагностических событий (последние 50).
 * Используется для секции "ДИАГНОСТИЧЕСКИЕ СОБЫТИЯ" в отчёте.
 * Без чувствительных данных: номера маскируются, токены не логируются.
 */
object DiagnosticsMetricsBuffer {

    enum class EventType {
        APP_OPENED,
        SERVICE_STARTED,
        PULL_CALL_START,
        PULL_CALL_RESPONSE,
        PULL_CALL_MODE_CHANGED,
        BACKOFF_ENTER,
        BACKOFF_EXIT,
        COMMAND_RECEIVED,
        CALL_RESOLVE_START,
        CALL_RESOLVED,
        PERMISSION_CHANGED,
        NETWORK_CHANGED,
        DIAGNOSTICS_EXPORTED
    }

    data class DiagnosticEvent(
        val timestamp: Long,
        val type: EventType,
        val message: String,
        val metadata: Map<String, String> = emptyMap()
    )

    private const val MAX_EVENTS = 50
    private const val THROTTLE_PERMISSION_MS = 30_000L
    private const val THROTTLE_NETWORK_MS = 10_000L

    private val buffer = ConcurrentLinkedDeque<DiagnosticEvent>()
    private val lastThrottleTime = mutableMapOf<String, Long>()
    private val throttleLock = Any()

    /**
     * Маскировать номер: только последние 2–4 цифры (***1234).
     * Не логировать полный номер.
     */
    fun maskPhone(phone: String?): String {
        if (phone.isNullOrBlank()) return "***"
        val digits = phone.filter { it.isDigit() }
        return "***" + digits.takeLast(4).ifEmpty { "****" }
    }

    /**
     * Добавить событие.
     * @param throttleKey если задан, то одинаковые события с тем же ключом не чаще чем раз в throttleMs (для типа PERMISSION_CHANGED — 30s, NETWORK_CHANGED — 10s).
     */
    @JvmOverloads
    fun addEvent(
        type: EventType,
        message: String,
        metadata: Map<String, String> = emptyMap(),
        throttleKey: String? = null,
        throttleMs: Long = 0L
    ) {
        val key = throttleKey ?: (type.name + ":" + metadata.entries.sortedBy { it.key }.joinToString { "${it.key}=${it.value}" })
        val effectiveThrottle = when (type) {
            EventType.PERMISSION_CHANGED -> THROTTLE_PERMISSION_MS
            EventType.NETWORK_CHANGED -> THROTTLE_NETWORK_MS
            else -> throttleMs
        }
        if (effectiveThrottle > 0) {
            synchronized(throttleLock) {
                val last = lastThrottleTime[key] ?: 0L
                if (System.currentTimeMillis() - last < effectiveThrottle) return
                lastThrottleTime[key] = System.currentTimeMillis()
            }
        }

        val event = DiagnosticEvent(
            timestamp = System.currentTimeMillis(),
            type = type,
            message = message,
            metadata = metadata
        )
        buffer.addLast(event)
        while (buffer.size > MAX_EVENTS) {
            buffer.removeFirst()
        }
    }

    /**
     * Последние N событий (для отчёта).
     */
    fun getLastEvents(n: Int): List<DiagnosticEvent> {
        val list = buffer.toList()
        return list.takeLast(min(n, list.size))
    }
}
