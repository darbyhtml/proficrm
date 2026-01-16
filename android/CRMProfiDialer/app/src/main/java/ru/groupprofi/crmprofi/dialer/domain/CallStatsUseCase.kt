package ru.groupprofi.crmprofi.dialer.domain

import java.util.*

/**
 * Use-case для расчёта статистики звонков.
 * Агрегирует данные из CallHistoryItem по периодам.
 */
class CallStatsUseCase {
    
    /**
     * Период для фильтрации статистики.
     */
    enum class Period {
        TODAY,          // Сегодня (с начала дня по локальному времени)
        LAST_7_DAYS,   // Последние 7 дней
        ALL             // Всё время
    }
    
    /**
     * Статистика звонков за период.
     */
    data class CallStats(
        val total: Int,           // Всего звонков
        val success: Int,          // Разговор состоялся
        val noAnswer: Int,         // Не ответили
        val dropped: Int,          // Сброс/Не удалось дозвониться
        val pendingCrm: Int        // Ожидает отправки в CRM
    ) {
        companion object {
            val EMPTY = CallStats(0, 0, 0, 0, 0)
        }
    }
    
    /**
     * Рассчитать статистику для списка звонков за указанный период.
     * @param nowMillis текущее время в миллисекундах (для тестирования)
     */
    fun calculate(items: List<CallHistoryItem>, period: Period, nowMillis: Long = System.currentTimeMillis()): CallStats {
        val filtered = filterByPeriod(items, period, nowMillis)
        
        var total = 0
        var success = 0
        var noAnswer = 0
        var dropped = 0
        var pendingCrm = 0
        
        filtered.forEach { call ->
            total++
            
            // Подсчитываем по статусам
            when (call.status) {
                CallHistoryItem.CallStatus.CONNECTED -> success++
                CallHistoryItem.CallStatus.NO_ANSWER -> noAnswer++
                CallHistoryItem.CallStatus.REJECTED -> dropped++
                CallHistoryItem.CallStatus.UNKNOWN -> dropped++ // Не удалось определить = сброс
            }
            
            // Подсчитываем ожидающие отправки в CRM
            if (!call.sentToCrm) {
                pendingCrm++
            }
        }
        
        return CallStats(total, success, noAnswer, dropped, pendingCrm)
    }
    
    /**
     * Отфильтровать звонки по периоду (публичный метод для использования в UI).
     * @param nowMillis текущее время в миллисекундах (для тестирования)
     */
    fun filterByPeriod(items: List<CallHistoryItem>, period: Period, nowMillis: Long = System.currentTimeMillis()): List<CallHistoryItem> {
        val calendar = Calendar.getInstance()
        calendar.timeInMillis = nowMillis
        
        return when (period) {
            Period.TODAY -> {
                // Начало сегодняшнего дня
                calendar.set(Calendar.HOUR_OF_DAY, 0)
                calendar.set(Calendar.MINUTE, 0)
                calendar.set(Calendar.SECOND, 0)
                calendar.set(Calendar.MILLISECOND, 0)
                val startOfDay = calendar.timeInMillis
                
                items.filter { it.startedAt >= startOfDay }
            }
            
            Period.LAST_7_DAYS -> {
                // Последние 7 дней (7 * 24 часа назад, включая текущий день)
                calendar.add(Calendar.DAY_OF_YEAR, -6) // 7 дней назад, включая текущий
                calendar.set(Calendar.HOUR_OF_DAY, 0)
                calendar.set(Calendar.MINUTE, 0)
                calendar.set(Calendar.SECOND, 0)
                calendar.set(Calendar.MILLISECOND, 0)
                val sevenDaysAgoStart = calendar.timeInMillis
                
                items.filter { it.startedAt >= sevenDaysAgoStart }
            }
            
            Period.ALL -> items
        }
    }
}
