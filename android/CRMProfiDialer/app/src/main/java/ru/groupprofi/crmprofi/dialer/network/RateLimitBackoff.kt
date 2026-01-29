package ru.groupprofi.crmprofi.dialer.network

import kotlin.random.Random

/**
 * Управление exponential backoff для rate limiting (HTTP 429).
 * Поддерживает Retry-After заголовок и экспоненциальную задержку с jitter.
 */
class RateLimitBackoff {
    private var backoffLevel: Int = 0
    private val random = Random.Default
    
    companion object {
        // Базовые значения для exponential backoff (в миллисекундах)
        private const val BASE_DELAY_MS = 10_000L // 10 секунд
        private const val MAX_DELAY_MS = 300_000L // 5 минут (cap)
        private const val MAX_BACKOFF_LEVEL = 5 // Максимальный уровень backoff
        
        // Для пустых ответов (204) - ступенчатое увеличение интервала
        private const val EMPTY_POLL_BASE_DELAY_MS = 2_000L // 2 секунды
        private const val EMPTY_POLL_MAX_DELAY_MS = 10_000L // 10 секунд
    }
    
    /**
     * Получить задержку для rate limit (429) с учетом Retry-After заголовка.
     * Использует max(backoff, retryAfterSeconds) для учета обоих значений.
     * @param retryAfterSeconds значение из заголовка Retry-After (если есть), null если нет
     * @return задержка в миллисекундах
     */
    fun getRateLimitDelay(retryAfterSeconds: Int?): Long {
        // Exponential backoff: 10s, 20s, 40s, 80s, 160s, capped at 5min
        val exponentialDelay = BASE_DELAY_MS * (1L shl backoffLevel.coerceAtMost(MAX_BACKOFF_LEVEL))
        val backoffDelay = exponentialDelay.coerceAtMost(MAX_DELAY_MS)
        
        // Если есть Retry-After, используем max(backoff, retryAfterSeconds)
        val retryAfterMs = retryAfterSeconds?.let { (it * 1000L).coerceAtMost(MAX_DELAY_MS) } ?: 0L
        val baseDelay = maxOf(backoffDelay, retryAfterMs).coerceAtLeast(BASE_DELAY_MS)
        
        // Добавляем jitter (±20% для избежания thundering herd)
        val jitterRange = (baseDelay * 0.2).toLong()
        val jitter = random.nextLong(jitterRange * 2 + 1) - jitterRange // -jitterRange..+jitterRange
        
        return (baseDelay + jitter).coerceAtLeast(BASE_DELAY_MS)
    }
    
    /**
     * Увеличить уровень backoff (вызывается при получении 429).
     */
    fun incrementBackoff() {
        backoffLevel = (backoffLevel + 1).coerceAtMost(MAX_BACKOFF_LEVEL)
    }
    
    /**
     * Сбросить backoff до нуля (полный сброс).
     * ВНИМАНИЕ: Для обычных случаев восстановления после 429 рекомендуется использовать
     * [decrementBackoff()] для плавного снижения уровня и избежания "пилы".
     * Этот метод может быть полезен для явного сброса в особых случаях (например, при переподключении).
     */
    fun resetBackoff() {
        backoffLevel = 0
    }
    
    /**
     * Мягко снизить backoff (вызывается при успешном ответе после 429).
     * Снижает уровень на 1 вместо полного обнуления для более плавного восстановления.
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
    
    /**
     * Получить задержку для пустых ответов (204) на основе количества подряд идущих пустых ответов.
     * Ступенчатое увеличение: 2s, 3s, 4s, ..., до 10s.
     */
    fun getEmptyPollDelay(consecutiveEmptyPolls: Int): Long {
        val step = (consecutiveEmptyPolls / 5).coerceIn(0, 8) // 0..8 шагов
        val baseDelay = EMPTY_POLL_BASE_DELAY_MS + (step * 1_000L)
        val delay = baseDelay.coerceAtMost(EMPTY_POLL_MAX_DELAY_MS)
        
        // Небольшой jitter (±500ms)
        val jitter = random.nextLong(-500, 501)
        return (delay + jitter).coerceAtLeast(1_000L)
    }
}
