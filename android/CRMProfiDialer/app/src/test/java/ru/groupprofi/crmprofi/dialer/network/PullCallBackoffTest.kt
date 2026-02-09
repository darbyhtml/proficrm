package ru.groupprofi.crmprofi.dialer.network

import org.junit.Assert.*
import org.junit.Test

/**
 * Unit tests для PullCallBackoff.
 * Проверяет правильность работы умного backoff для pullCall.
 */
class PullCallBackoffTest {
    
    @Test
    fun testRateLimitBackoff_429_withoutRetryAfter() {
        val backoff = PullCallBackoff()
        
        // Первая 429: 1s
        backoff.incrementBackoff()
        val delay1 = backoff.getRateLimitDelay(null)
        assertTrue("First 429 should be ~1s", delay1 in 800L..1200L)
        
        // Вторая 429: 2s
        backoff.incrementBackoff()
        val delay2 = backoff.getRateLimitDelay(null)
        assertTrue("Second 429 should be ~2s", delay2 in 1600L..2400L)
        
        // Третья 429: 4s
        backoff.incrementBackoff()
        val delay3 = backoff.getRateLimitDelay(null)
        assertTrue("Third 429 should be ~4s", delay3 in 3200L..4800L)
        
        // Четвертая 429: 8s
        backoff.incrementBackoff()
        val delay4 = backoff.getRateLimitDelay(null)
        assertTrue("Fourth 429 should be ~8s", delay4 in 6400L..9600L)
        
        // Пятая 429: 15s (cap)
        backoff.incrementBackoff()
        val delay5 = backoff.getRateLimitDelay(null)
        assertTrue("Fifth 429 should be capped at 15s", delay5 <= 15_000L)
        
        // Шестая 429: все еще 15s (cap)
        backoff.incrementBackoff()
        val delay6 = backoff.getRateLimitDelay(null)
        assertTrue("Sixth 429 should still be capped at 15s", delay6 <= 15_000L)
    }
    
    @Test
    fun testRateLimitBackoff_429_withRetryAfter() {
        val backoff = PullCallBackoff()
        
        // Retry-After = 5s, но backoff еще низкий (1s)
        backoff.incrementBackoff()
        val delay1 = backoff.getRateLimitDelay(5)
        assertTrue("Should use max(backoff, retryAfter)", delay1 in 4800L..6000L)
        
        // Retry-After = 3s, но backoff уже выше (2s)
        backoff.incrementBackoff()
        val delay2 = backoff.getRateLimitDelay(3)
        assertTrue("Should use max(backoff, retryAfter)", delay2 in 2400L..3600L)
        
        // Retry-After = 20s, но cap = 15s
        backoff.incrementBackoff()
        val delay3 = backoff.getRateLimitDelay(20)
        assertTrue("Should cap at 15s even if retryAfter is higher", delay3 <= 15_000L)
    }
    
    @Test
    fun testNetworkErrorBackoff() {
        val backoff = PullCallBackoff()
        
        // Первая ошибка: 2s
        backoff.incrementBackoff()
        val delay1 = backoff.getNetworkErrorDelay()
        assertTrue("First network error should be ~2s", delay1 in 1600L..2400L)
        
        // Вторая ошибка: 4s
        backoff.incrementBackoff()
        val delay2 = backoff.getNetworkErrorDelay()
        assertTrue("Second network error should be ~4s", delay2 in 3200L..4800L)
        
        // Третья ошибка: 8s
        backoff.incrementBackoff()
        val delay3 = backoff.getNetworkErrorDelay()
        assertTrue("Third network error should be ~8s", delay3 in 6400L..9600L)
        
        // Четвертая ошибка: 15s (cap)
        backoff.incrementBackoff()
        val delay4 = backoff.getNetworkErrorDelay()
        assertTrue("Fourth network error should be capped at 15s", delay4 <= 15_000L)
    }
    
    @Test
    fun testServerErrorBackoff() {
        val backoff = PullCallBackoff()
        
        // Первая 5xx: 2s
        backoff.incrementBackoff()
        val delay1 = backoff.getServerErrorDelay()
        assertTrue("First 5xx should be ~2s", delay1 in 1600L..2400L)
        
        // Вторая 5xx: 4s
        backoff.incrementBackoff()
        val delay2 = backoff.getServerErrorDelay()
        assertTrue("Second 5xx should be ~4s", delay2 in 3200L..4800L)
        
        // Третья 5xx: 8s
        backoff.incrementBackoff()
        val delay3 = backoff.getServerErrorDelay()
        assertTrue("Third 5xx should be ~8s", delay3 in 6400L..9600L)
        
        // Четвертая 5xx: 20s (cap для server errors)
        backoff.incrementBackoff()
        val delay4 = backoff.getServerErrorDelay()
        assertTrue("Fourth 5xx should be capped at 20s", delay4 <= 20_000L)
    }
    
    @Test
    fun testBackoffReset() {
        val backoff = PullCallBackoff()
        
        // Увеличиваем backoff до уровня 3
        backoff.incrementBackoff()
        backoff.incrementBackoff()
        backoff.incrementBackoff()
        assertEquals("Backoff level should be 3", 3, backoff.getBackoffLevel())
        
        // Сбрасываем
        backoff.resetBackoff()
        assertEquals("Backoff level should be 0 after reset", 0, backoff.getBackoffLevel())
        
        val delay = backoff.getRateLimitDelay(null)
        assertTrue("After reset, delay should be ~1s", delay in 800L..1200L)
    }
    
    @Test
    fun testBackoffDecrement() {
        val backoff = PullCallBackoff()
        
        // Увеличиваем backoff до уровня 3
        backoff.incrementBackoff()
        backoff.incrementBackoff()
        backoff.incrementBackoff()
        assertEquals("Backoff level should be 3", 3, backoff.getBackoffLevel())
        
        // Мягко снижаем
        backoff.decrementBackoff()
        assertEquals("Backoff level should be 2 after decrement", 2, backoff.getBackoffLevel())
        
        val delay = backoff.getRateLimitDelay(null)
        assertTrue("After decrement, delay should be ~4s", delay in 3200L..4800L)
    }
    
    @Test
    fun testBackoffCap() {
        val backoff = PullCallBackoff()
        
        // Увеличиваем backoff до максимума
        repeat(10) {
            backoff.incrementBackoff()
        }
        
        // Проверяем, что уровень не превышает MAX_BACKOFF_LEVEL
        assertTrue("Backoff level should not exceed MAX", backoff.getBackoffLevel() <= 4)
        
        // Проверяем, что задержка не превышает cap
        val delay = backoff.getRateLimitDelay(null)
        assertTrue("Delay should be capped at 15s for rate limit", delay <= 15_000L)
    }
}
