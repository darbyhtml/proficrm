package ru.groupprofi.crmprofi.dialer.domain

import java.util.Date

/**
 * Элемент истории звонка для отображения пользователю.
 * Хранится локально для быстрого доступа к истории.
 * ЭТАП 2: Расширено новыми полями для аналитики (все nullable для обратной совместимости).
 */
data class CallHistoryItem(
    val id: String, // call_request_id из CRM
    val phone: String,
    val phoneDisplayName: String? = null, // Имя из контактов (если есть)
    val status: CallStatus,
    val statusText: String, // Человеческий текст статуса
    val durationSeconds: Int? = null,
    val startedAt: Long, // Timestamp
    val sentToCrm: Boolean = false, // Отправлено ли в CRM
    val sentToCrmAt: Long? = null, // Когда отправлено
    // Новые поля (ЭТАП 2: для расширенной аналитики)
    val direction: CallDirection? = null, // Направление звонка
    val resolveMethod: ResolveMethod? = null, // Метод определения результата
    val attemptsCount: Int? = null, // Количество попыток определения
    val actionSource: ActionSource? = null, // Источник действия пользователя
    val endedAt: Long? = null // Время окончания звонка (millis)
) {
    /**
     * Статус звонка (человеческий формат).
     */
    enum class CallStatus {
        CONNECTED,      // Разговор состоялся
        NO_ANSWER,      // Не ответили
        REJECTED,       // Сброс
        UNKNOWN         // Определяем результат...
    }
    
    /**
     * Получить человеческий текст статуса.
     */
    fun getStatusText(): String {
        return when (status) {
            CallStatus.CONNECTED -> "Разговор состоялся"
            CallStatus.NO_ANSWER -> "Не ответили"
            CallStatus.REJECTED -> "Сброс"
            CallStatus.UNKNOWN -> "Определяем результат..."
        }
    }
    
    /**
     * Получить бейдж статуса отправки в CRM.
     */
    fun getCrmBadgeText(): String {
        return if (sentToCrm) {
            "Отправлено в CRM"
        } else {
            "Ожидает отправки"
        }
    }
    
    /**
     * Форматировать длительность звонка.
     */
    fun getDurationText(): String {
        if (durationSeconds == null || durationSeconds == 0) return ""
        val minutes = durationSeconds / 60
        val seconds = durationSeconds % 60
        return if (minutes > 0) {
            "${minutes} мин ${seconds} сек"
        } else {
            "${seconds} сек"
        }
    }
    
    /**
     * Форматировать дату/время звонка.
     */
    fun getDateTimeText(): String {
        val date = Date(startedAt)
        val now = Date()
        val diff = now.time - startedAt
        
        return when {
            diff < 60000 -> "только что" // Меньше минуты
            diff < 3600000 -> "${diff / 60000} мин назад" // Меньше часа
            diff < 86400000 -> "${diff / 3600000} ч назад" // Меньше суток
            else -> {
                val sdf = java.text.SimpleDateFormat("dd.MM.yyyy HH:mm", java.util.Locale.getDefault())
                sdf.format(date)
            }
        }
    }
}
