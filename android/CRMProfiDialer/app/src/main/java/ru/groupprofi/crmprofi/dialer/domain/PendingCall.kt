package ru.groupprofi.crmprofi.dialer.domain

/**
 * Ожидаемый звонок - звонок, результат которого ещё определяется.
 * ЭТАП 2: Добавлен actionSource для отслеживания источника действия пользователя.
 */
data class PendingCall(
    val callRequestId: String,        // ID запроса из CRM
    val phoneNumber: String,          // Номер телефона (нормализованный)
    val startedAtMillis: Long,        // Время начала ожидания (когда открыли звонилку)
    val state: PendingState,          // Текущее состояние
    val attempts: Int = 0,            // Количество попыток проверки
    val actionSource: ActionSource? = null // Источник действия (ЭТАП 2: CRM_UI, NOTIFICATION, HISTORY, UNKNOWN)
) {
    /**
     * Состояние ожидаемого звонка.
     */
    enum class PendingState {
        PENDING,      // Ожидаем результат
        RESOLVING,    // Определяем результат (проверяем CallLog)
        RESOLVED,     // Результат определён
        FAILED        // Не удалось определить результат
    }
    
    /**
     * Нормализовать номер телефона (убрать пробелы, скобки, дефисы).
     * @deprecated Используйте PhoneNumberNormalizer.normalize()
     */
    companion object {
        @Deprecated("Используйте PhoneNumberNormalizer.normalize()", ReplaceWith("PhoneNumberNormalizer.normalize(phone)"))
        fun normalizePhone(phone: String): String {
            return PhoneNumberNormalizer.normalize(phone)
        }
    }
}
