package ru.groupprofi.crmprofi.dialer.domain

import android.provider.CallLog
import org.junit.Assert.*
import org.junit.Test

/**
 * Тесты для CallDirection enum (маппинг из CallLog.TYPE).
 * ЭТАП 6: проверка корректности маппинга.
 */
class CallDirectionTest {

    @Test
    fun `fromCallLogType - OUTGOING_TYPE маппится в OUTGOING`() {
        val direction = CallDirection.fromCallLogType(CallLog.Calls.OUTGOING_TYPE)
        assertEquals(CallDirection.OUTGOING, direction)
        assertEquals("outgoing", direction.apiValue)
    }

    @Test
    fun `fromCallLogType - INCOMING_TYPE маппится в INCOMING`() {
        val direction = CallDirection.fromCallLogType(CallLog.Calls.INCOMING_TYPE)
        assertEquals(CallDirection.INCOMING, direction)
        assertEquals("incoming", direction.apiValue)
    }

    @Test
    fun `fromCallLogType - MISSED_TYPE маппится в MISSED`() {
        val direction = CallDirection.fromCallLogType(CallLog.Calls.MISSED_TYPE)
        assertEquals(CallDirection.MISSED, direction)
        assertEquals("missed", direction.apiValue)
    }

    @Test
    fun `fromCallLogType - неизвестный тип маппится в UNKNOWN`() {
        val direction = CallDirection.fromCallLogType(999) // Неизвестный тип
        assertEquals(CallDirection.UNKNOWN, direction)
        assertEquals("unknown", direction.apiValue)
    }

    @Test
    fun `apiValue - корректные строковые значения`() {
        assertEquals("outgoing", CallDirection.OUTGOING.apiValue)
        assertEquals("incoming", CallDirection.INCOMING.apiValue)
        assertEquals("missed", CallDirection.MISSED.apiValue)
        assertEquals("unknown", CallDirection.UNKNOWN.apiValue)
    }
}
