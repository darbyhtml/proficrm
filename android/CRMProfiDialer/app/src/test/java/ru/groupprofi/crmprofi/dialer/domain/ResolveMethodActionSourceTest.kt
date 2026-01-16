package ru.groupprofi.crmprofi.dialer.domain

import org.junit.Assert.*
import org.junit.Test

/**
 * Тесты для ResolveMethod и ActionSource enum (строковые значения контракта).
 * ЭТАП 6: проверка корректности apiValue.
 */
class ResolveMethodActionSourceTest {

    @Test
    fun `ResolveMethod apiValue - корректные строковые значения`() {
        assertEquals("observer", ResolveMethod.OBSERVER.apiValue)
        assertEquals("retry", ResolveMethod.RETRY.apiValue)
        assertEquals("unknown", ResolveMethod.UNKNOWN.apiValue)
    }

    @Test
    fun `ActionSource apiValue - корректные строковые значения`() {
        assertEquals("crm_ui", ActionSource.CRM_UI.apiValue)
        assertEquals("notification", ActionSource.NOTIFICATION.apiValue)
        assertEquals("history", ActionSource.HISTORY.apiValue)
        assertEquals("unknown", ActionSource.UNKNOWN.apiValue)
    }
}
