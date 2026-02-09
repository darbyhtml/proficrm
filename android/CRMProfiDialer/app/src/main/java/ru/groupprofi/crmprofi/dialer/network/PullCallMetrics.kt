package ru.groupprofi.crmprofi.dialer.network

import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicLong

/**
 * Метрики для диагностики pullCall (получение команд).
 * Отслеживает режимы работы, ошибки, задержки.
 */
object PullCallMetrics {
    // Режим работы pullCall
    enum class PullMode {
        LONG_POLL,  // Long-polling (сервер поддерживает ожидание)
        BURST,      // Burst window (активный период)
        SLOW        // Медленный режим (фон/idle)
    }
    
    // Причина деградации режима
    enum class DegradationReason {
        NONE,           // Нет деградации
        RATE_LIMIT,     // 429 rate limit
        NETWORK_ERROR,  // Сетевая ошибка / timeout
        SERVER_ERROR    // 5xx ошибка сервера
    }
    
    // Текущий режим
    @Volatile
    var currentMode: PullMode = PullMode.LONG_POLL
        private set
    
    // Причина деградации
    @Volatile
    var degradationReason: DegradationReason = DegradationReason.NONE
        private set
    
    // Время последней команды (timestamp)
    @Volatile
    var lastCommandReceivedAt: Long? = null
        private set
    
    // Счетчик 429 за последний час (сбрасывается каждый час)
    private val rateLimit429Count = AtomicInteger(0)
    private var last429ResetAt = System.currentTimeMillis()
    
    // Счетчик активных запросов (для single-flight)
    private val activeRequestCount = AtomicInteger(0)
    
    // Время последнего pullCall цикла
    @Volatile
    var lastPullCycleAt: Long = 0
        private set
    
    // Latency последнего pullCall (в миллисекундах)
    @Volatile
    var lastPullLatencyMs: Long = 0
        private set
    
    // Метрики доставки команды (time to delivery)
    private val deliveryLatencies = mutableListOf<Long>() // Последние 20 замеров
    private val maxDeliveryLatencies = 20
    
    // Время начала ожидания команды (для измерения cycle_wait_time)
    @Volatile
    var cycleWaitStartTime: Long = 0
        private set
    
    // Счетчик времени в backoff (для диагностики)
    private var backoffStartTime: Long? = null
    private var totalTimeSpentInBackoffMs = AtomicLong(0)
    
    // Максимальный достигнутый уровень backoff
    @Volatile
    var maxBackoffReached: Int = 0
        private set
    
    /**
     * Обновить режим работы.
     */
    fun setMode(mode: PullMode, reason: DegradationReason = DegradationReason.NONE) {
        currentMode = mode
        degradationReason = reason
    }
    
    /**
     * Зафиксировать получение команды.
     * @param createdAtTimestamp время создания команды в CRM (если доступно, null если нет)
     */
    fun recordCommandReceived(createdAtTimestamp: Long? = null) {
        val receivedAt = System.currentTimeMillis()
        lastCommandReceivedAt = receivedAt
        
        // Вычисляем latency доставки команды
        if (createdAtTimestamp != null && createdAtTimestamp > 0) {
            val deliveryLatency = receivedAt - createdAtTimestamp
            synchronized(deliveryLatencies) {
                deliveryLatencies.add(deliveryLatency)
                if (deliveryLatencies.size > maxDeliveryLatencies) {
                    deliveryLatencies.removeAt(0)
                }
            }
        } else if (cycleWaitStartTime > 0) {
            // Fallback: используем cycle_wait_time как приближение
            val cycleWaitTime = receivedAt - cycleWaitStartTime
            synchronized(deliveryLatencies) {
                deliveryLatencies.add(cycleWaitTime)
                if (deliveryLatencies.size > maxDeliveryLatencies) {
                    deliveryLatencies.removeAt(0)
                }
            }
        }
        
        cycleWaitStartTime = 0 // Сбрасываем после получения команды
        
        // При получении команды сбрасываем деградацию
        degradationReason = DegradationReason.NONE
    }
    
    /**
     * Зафиксировать 429 ошибку.
     */
    fun record429() {
        // Сбрасываем счетчик каждый час
        val now = System.currentTimeMillis()
        if (now - last429ResetAt > 3600_000L) {
            rateLimit429Count.set(0)
            last429ResetAt = now
        }
        rateLimit429Count.incrementAndGet()
        
        // Начинаем отсчет времени в backoff, если еще не начат
        if (backoffStartTime == null) {
            backoffStartTime = now
        }
    }
    
    /**
     * Зафиксировать выход из backoff.
     */
    fun recordBackoffExit(backoffLevel: Int) {
        if (backoffStartTime != null) {
            val timeSpent = System.currentTimeMillis() - backoffStartTime!!
            totalTimeSpentInBackoffMs.addAndGet(timeSpent)
            backoffStartTime = null
        }
        if (backoffLevel > maxBackoffReached) {
            maxBackoffReached = backoffLevel
        }
    }
    
    /**
     * Получить количество 429 за последний час.
     */
    fun get429CountLastHour(): Int {
        val now = System.currentTimeMillis()
        if (now - last429ResetAt > 3600_000L) {
            rateLimit429Count.set(0)
            last429ResetAt = now
        }
        return rateLimit429Count.get()
    }
    
    /**
     * Зафиксировать начало pullCall цикла.
     */
    fun recordPullCycleStart() {
        activeRequestCount.incrementAndGet()
        lastPullCycleAt = System.currentTimeMillis()
        
        // Если cycle_wait_time еще не начат, начинаем отсчет
        if (cycleWaitStartTime == 0L) {
            cycleWaitStartTime = System.currentTimeMillis()
        }
    }
    
    /**
     * Зафиксировать завершение pullCall цикла.
     */
    fun recordPullCycleEnd(latencyMs: Long, httpCode: Int) {
        activeRequestCount.decrementAndGet()
        lastPullLatencyMs = latencyMs
        
        if (httpCode == 429) {
            record429()
        }
    }
    
    /**
     * Получить количество активных запросов (для single-flight проверки).
     */
    fun getActiveRequestCount(): Int = activeRequestCount.get()
    
    /**
     * Получить время с последней команды (в секундах).
     */
    fun getSecondsSinceLastCommand(): Long? {
        val lastCommand = lastCommandReceivedAt ?: return null
        return (System.currentTimeMillis() - lastCommand) / 1000
    }
    
    /**
     * Получить среднюю latency доставки команды (из последних замеров).
     */
    fun getAverageDeliveryLatencyMs(): Long? {
        synchronized(deliveryLatencies) {
            if (deliveryLatencies.isEmpty()) return null
            return deliveryLatencies.average().toLong()
        }
    }
    
    /**
     * Получить время, проведенное в backoff (в миллисекундах).
     */
    fun getTimeSpentInBackoffMs(): Long {
        val currentBackoffTime = if (backoffStartTime != null) {
            System.currentTimeMillis() - backoffStartTime!!
        } else {
            0L
        }
        return totalTimeSpentInBackoffMs.get() + currentBackoffTime
    }
    
    /**
     * Получить cycle_wait_time (время ожидания в текущем цикле).
     */
    fun getCycleWaitTimeMs(): Long {
        if (cycleWaitStartTime == 0L) return 0L
        return System.currentTimeMillis() - cycleWaitStartTime
    }
    
    /**
     * Сбросить все метрики (для тестирования).
     */
    fun reset() {
        rateLimit429Count.set(0)
        activeRequestCount.set(0)
        lastCommandReceivedAt = null
        last429ResetAt = System.currentTimeMillis()
        degradationReason = DegradationReason.NONE
        currentMode = PullMode.LONG_POLL
        synchronized(deliveryLatencies) {
            deliveryLatencies.clear()
        }
        cycleWaitStartTime = 0
        backoffStartTime = null
        totalTimeSpentInBackoffMs.set(0)
        maxBackoffReached = 0
    }
}
