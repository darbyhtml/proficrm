package ru.groupprofi.crmprofi.dialer.network

import kotlin.random.Random

/**
 * Умная стратегия backoff ТОЛЬКО для pullCall (получение команд).
 * Отделена от общего RateLimitBackoff для более мягкого поведения.
 * 
 * Цель: даже при 429/сетевых ошибках приложение продолжает пытаться каждые ≤15 секунд,
 * вместо "адских задержек" 30-160 секунд.
 */
class PullCallBackoff {
    private var backoffLevel: Int = 0
    private val random = Random.Default
    
    companion object {
        // Мягкие базовые значения для pullCall backoff (в миллисекундах)
        private const val BASE_DELAY_MS = 1_000L // 1 секунда (вместо 10)
        private const val MAX_DELAY_MS = 15_000L // 15 секунд cap (вместо 5 минут)
        private const val MAX_BACKOFF_LEVEL = 4 // Максимальный уровень backoff
        
        // Для сетевых ошибок - быстрый backoff
        private const val NETWORK_ERROR_BASE_MS = 2_000L // 2 секунды
        private const val NETWORK_ERROR_MAX_MS = 15_000L // 15 секунд cap
        
        // Для 5xx ошибок - умеренный backoff
        private const val SERVER_ERROR_BASE_MS = 2_000L // 2 секунды
        private const val SERVER_ERROR_MAX_MS = 20_000L // 20 секунд cap
    }
    
    /**
     * Получить задержку для rate limit (429) с учетом Retry-After заголовка.
     * Мягкая стратегия: 1s → 2s → 4s → 8s → 15s (cap 15s).
     */
    fun getRateLimitDelay(retryAfterSeconds: Int?): Long {
        // Мягкий exponential backoff: 1s, 2s, 4s, 8s, 15s (cap)
        val exponentialDelay = BASE_DELAY_MS * (1L shl backoffLevel.coerceAtMost(MAX_BACKOFF_LEVEL))
        val backoffDelay = exponentialDelay.coerceAtMost(MAX_DELAY_MS)
        
        // Если есть Retry-After, используем max(backoff, retryAfterSeconds), но не больше 15s
        val retryAfterMs = retryAfterSeconds?.let { 
            (it * 1000L).coerceAtMost(MAX_DELAY_MS) 
        } ?: 0L
        val baseDelay = maxOf(backoffDelay, retryAfterMs).coerceAtLeast(BASE_DELAY_MS)
        
        // Добавляем jitter ±20% для избежания thundering herd
        val jitterRange = (baseDelay * 0.2).toLong()
        val jitter = random.nextLong(jitterRange * 2 + 1) - jitterRange
        
        return (baseDelay + jitter).coerceAtMost(MAX_DELAY_MS)
    }
    
    /**
     * Получить задержку для сетевой ошибки (timeout, no internet).
     * Быстрый backoff: 2s → 4s → 8s → 15s (cap 15s).
     */
    fun getNetworkErrorDelay(): Long {
        val exponentialDelay = NETWORK_ERROR_BASE_MS * (1L shl backoffLevel.coerceAtMost(MAX_BACKOFF_LEVEL))
        val baseDelay = exponentialDelay.coerceAtMost(NETWORK_ERROR_MAX_MS)
        
        // Jitter ±20%
        val jitterRange = (baseDelay * 0.2).toLong()
        val jitter = random.nextLong(jitterRange * 2 + 1) - jitterRange
        
        return (baseDelay + jitter).coerceAtMost(NETWORK_ERROR_MAX_MS)
    }
    
    /**
     * Получить задержку для ошибки сервера (5xx).
     * Умеренный backoff: 2s → 4s → 8s → 20s (cap 20s).
     */
    fun getServerErrorDelay(): Long {
        val exponentialDelay = SERVER_ERROR_BASE_MS * (1L shl backoffLevel.coerceAtMost(MAX_BACKOFF_LEVEL))
        val baseDelay = exponentialDelay.coerceAtMost(SERVER_ERROR_MAX_MS)
        
        // Jitter ±20%
        val jitterRange = (baseDelay * 0.2).toLong()
        val jitter = random.nextLong(jitterRange * 2 + 1) - jitterRange
        
        return (baseDelay + jitter).coerceAtMost(SERVER_ERROR_MAX_MS)
    }
    
    /**
     * Увеличить уровень backoff (вызывается при получении ошибки).
     */
    fun incrementBackoff() {
        backoffLevel = (backoffLevel + 1).coerceAtMost(MAX_BACKOFF_LEVEL)
    }
    
    /**
     * Сбросить backoff до нуля (полный сброс).
     * Вызывается при восстановлении сети или успешном ответе.
     */
    fun resetBackoff() {
        backoffLevel = 0
    }
    
    /**
     * Мягко снизить backoff (вызывается при успешном ответе после ошибки).
     */
    fun decrementBackoff() {
        if (backoffLevel > 0) {
            backoffLevel--
        }
    }
    
    /**
     * Получить текущий уровень backoff.
     */
    fun getBackoffLevel(): Int = backoffLevel
}
