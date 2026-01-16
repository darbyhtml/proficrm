package ru.groupprofi.crmprofi.dialer.domain

import org.junit.Assert.*
import org.junit.Test
import org.json.JSONObject

/**
 * Тесты для CallEventPayload (формирование legacy/extended JSON).
 * ЭТАП 6: проверка контракта CallEvent.
 */
class CallEventPayloadTest {

    @Test
    fun `toLegacyJson - содержит только 4 поля`() {
        val payload = CallEventPayload(
            callRequestId = "test-uuid",
            callStatus = "connected",
            callStartedAt = 1705327800000L, // 2024-01-15T14:30:00Z
            callDurationSeconds = 180,
            // Новые поля присутствуют, но не должны попасть в legacy JSON
            callEndedAt = 1705327980000L,
            direction = "outgoing",
            resolveMethod = "observer",
            attemptsCount = 1,
            actionSource = "crm_ui"
        )

        val json = payload.toLegacyJson()
        val obj = JSONObject(json)

        // Проверяем, что legacy JSON содержит только 4 поля
        assertEquals("test-uuid", obj.getString("call_request_id"))
        assertEquals("connected", obj.getString("call_status"))
        assertTrue(obj.has("call_started_at"))
        assertEquals(180, obj.getInt("call_duration_seconds"))

        // Проверяем, что новых полей НЕТ
        assertFalse(obj.has("call_ended_at"))
        assertFalse(obj.has("direction"))
        assertFalse(obj.has("resolve_method"))
        assertFalse(obj.has("attempts_count"))
        assertFalse(obj.has("action_source"))
    }

    @Test
    fun `toExtendedJson - включает новые поля при наличии`() {
        val payload = CallEventPayload(
            callRequestId = "test-uuid",
            callStatus = "connected",
            callStartedAt = 1705327800000L,
            callDurationSeconds = 180,
            callEndedAt = 1705327980000L,
            direction = "outgoing",
            resolveMethod = "observer",
            attemptsCount = 1,
            actionSource = "crm_ui"
        )

        val json = payload.toExtendedJson()
        val obj = JSONObject(json)

        // Проверяем, что extended JSON содержит все поля
        assertEquals("test-uuid", obj.getString("call_request_id"))
        assertEquals("connected", obj.getString("call_status"))
        assertTrue(obj.has("call_started_at"))
        assertEquals(180, obj.getInt("call_duration_seconds"))
        assertTrue(obj.has("call_ended_at"))
        assertEquals("outgoing", obj.getString("direction"))
        assertEquals("observer", obj.getString("resolve_method"))
        assertEquals(1, obj.getInt("attempts_count"))
        assertEquals("crm_ui", obj.getString("action_source"))
    }

    @Test
    fun `toExtendedJson - не включает null поля`() {
        val payload = CallEventPayload(
            callRequestId = "test-uuid",
            callStatus = "connected",
            callStartedAt = 1705327800000L,
            callDurationSeconds = 180,
            // Новые поля = null
            callEndedAt = null,
            direction = null,
            resolveMethod = null,
            attemptsCount = null,
            actionSource = null
        )

        val json = payload.toExtendedJson()
        val obj = JSONObject(json)

        // Проверяем, что null поля не включены
        assertEquals("test-uuid", obj.getString("call_request_id"))
        assertEquals("connected", obj.getString("call_status"))
        assertFalse(obj.has("call_ended_at"))
        assertFalse(obj.has("direction"))
        assertFalse(obj.has("resolve_method"))
        assertFalse(obj.has("attempts_count"))
        assertFalse(obj.has("action_source"))
    }

    @Test
    fun `toLegacyJson - минимальный payload (только call_request_id)`() {
        val payload = CallEventPayload(
            callRequestId = "test-uuid",
            callStatus = null,
            callStartedAt = null,
            callDurationSeconds = null
        )

        val json = payload.toLegacyJson()
        val obj = JSONObject(json)

        // Проверяем, что только call_request_id присутствует
        assertEquals("test-uuid", obj.getString("call_request_id"))
        assertFalse(obj.has("call_status"))
        assertFalse(obj.has("call_started_at"))
        assertFalse(obj.has("call_duration_seconds"))
    }
}
