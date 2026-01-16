package ru.groupprofi.crmprofi.dialer.domain

import org.junit.Assert.*
import org.junit.Test
import java.util.*

/**
 * Тесты для CallStatsUseCase.
 */
class CallStatsUseCaseTest {
    
    private val useCase = CallStatsUseCase()
    
    /**
     * Создать CallHistoryItem для тестов.
     */
    private fun createCall(
        startedAt: Long,
        status: CallHistoryItem.CallStatus,
        sentToCrm: Boolean = true
    ): CallHistoryItem {
        return CallHistoryItem(
            id = UUID.randomUUID().toString(),
            phone = "+79991112233",
            phoneDisplayName = null,
            status = status,
            statusText = when (status) {
                CallHistoryItem.CallStatus.CONNECTED -> "Разговор состоялся"
                CallHistoryItem.CallStatus.NO_ANSWER -> "Не ответили"
                CallHistoryItem.CallStatus.REJECTED -> "Сброс"
                CallHistoryItem.CallStatus.UNKNOWN -> "Не удалось определить"
            },
            durationSeconds = if (status == CallHistoryItem.CallStatus.CONNECTED) 60 else null,
            startedAt = startedAt,
            sentToCrm = sentToCrm,
            sentToCrmAt = if (sentToCrm) startedAt + 1000 else null
        )
    }
    
    /**
     * Получить начало дня для заданного времени.
     */
    private fun getStartOfDay(millis: Long): Long {
        val calendar = Calendar.getInstance()
        calendar.timeInMillis = millis
        calendar.set(Calendar.HOUR_OF_DAY, 0)
        calendar.set(Calendar.MINUTE, 0)
        calendar.set(Calendar.SECOND, 0)
        calendar.set(Calendar.MILLISECOND, 0)
        return calendar.timeInMillis
    }
    
    @Test
    fun `calculate TODAY - звонки до начала дня не считаются`() {
        // Подготовка: фиксированное время "сейчас" = 15:00 сегодня
        val now = System.currentTimeMillis()
        val startOfToday = getStartOfDay(now)
        val yesterday = startOfToday - 1000 // За секунду до начала дня
        
        val calls = listOf(
            createCall(yesterday, CallHistoryItem.CallStatus.CONNECTED), // Вчера - не должно считаться
            createCall(startOfToday, CallHistoryItem.CallStatus.CONNECTED), // Начало дня - должно считаться
            createCall(startOfToday + 1000, CallHistoryItem.CallStatus.CONNECTED), // Сегодня - должно считаться
            createCall(now, CallHistoryItem.CallStatus.CONNECTED) // Сейчас - должно считаться
        )
        
        val stats = useCase.calculate(calls, CallStatsUseCase.Period.TODAY, now)
        
        assertEquals("Должно быть 3 звонка за сегодня", 3, stats.total)
        assertEquals("Все 3 звонка - успешные", 3, stats.success)
    }
    
    @Test
    fun `calculate LAST_7_DAYS - правильно фильтрует 7 дней`() {
        val now = System.currentTimeMillis()
        val calendar = Calendar.getInstance()
        calendar.timeInMillis = now
        
        // Начало 7 дней назад (включая сегодня)
        calendar.add(Calendar.DAY_OF_YEAR, -6)
        calendar.set(Calendar.HOUR_OF_DAY, 0)
        calendar.set(Calendar.MINUTE, 0)
        calendar.set(Calendar.SECOND, 0)
        calendar.set(Calendar.MILLISECOND, 0)
        val sevenDaysAgoStart = calendar.timeInMillis
        
        val calls = listOf(
            createCall(sevenDaysAgoStart - 1000, CallHistoryItem.CallStatus.CONNECTED), // До периода - не должно считаться
            createCall(sevenDaysAgoStart, CallHistoryItem.CallStatus.CONNECTED), // Начало периода - должно считаться
            createCall(sevenDaysAgoStart + 24 * 60 * 60 * 1000L, CallHistoryItem.CallStatus.CONNECTED), // Второй день - должно считаться
            createCall(now, CallHistoryItem.CallStatus.CONNECTED) // Сегодня - должно считаться
        )
        
        val stats = useCase.calculate(calls, CallStatsUseCase.Period.LAST_7_DAYS, now)
        
        assertEquals("Должно быть 3 звонка за 7 дней", 3, stats.total)
    }
    
    @Test
    fun `calculate ALL - считает все звонки`() {
        val now = System.currentTimeMillis()
        val oldCall = createCall(now - 30 * 24 * 60 * 60 * 1000L, CallHistoryItem.CallStatus.CONNECTED) // 30 дней назад
        val recentCall = createCall(now, CallHistoryItem.CallStatus.CONNECTED)
        
        val stats = useCase.calculate(listOf(oldCall, recentCall), CallStatsUseCase.Period.ALL, now)
        
        assertEquals("Должно быть 2 звонка", 2, stats.total)
    }
    
    @Test
    fun `calculate - правильно считает pendingCrm`() {
        val now = System.currentTimeMillis()
        val calls = listOf(
            createCall(now, CallHistoryItem.CallStatus.CONNECTED, sentToCrm = true),
            createCall(now, CallHistoryItem.CallStatus.CONNECTED, sentToCrm = false), // Ожидает отправки
            createCall(now, CallHistoryItem.CallStatus.NO_ANSWER, sentToCrm = false) // Ожидает отправки
        )
        
        val stats = useCase.calculate(calls, CallStatsUseCase.Period.ALL, now)
        
        assertEquals("Должно быть 2 звонка, ожидающих отправки", 2, stats.pendingCrm)
    }
    
    @Test
    fun `calculate - правильно считает статусы`() {
        val now = System.currentTimeMillis()
        val calls = listOf(
            createCall(now, CallHistoryItem.CallStatus.CONNECTED),
            createCall(now, CallHistoryItem.CallStatus.CONNECTED),
            createCall(now, CallHistoryItem.CallStatus.NO_ANSWER),
            createCall(now, CallHistoryItem.CallStatus.NO_ANSWER),
            createCall(now, CallHistoryItem.CallStatus.REJECTED),
            createCall(now, CallHistoryItem.CallStatus.UNKNOWN) // UNKNOWN считается как dropped
        )
        
        val stats = useCase.calculate(calls, CallStatsUseCase.Period.ALL, now)
        
        assertEquals("Всего 6 звонков", 6, stats.total)
        assertEquals("2 успешных", 2, stats.success)
        assertEquals("2 не ответили", 2, stats.noAnswer)
        assertEquals("2 сброс (1 REJECTED + 1 UNKNOWN)", 2, stats.dropped)
    }
    
    @Test
    fun `filterByPeriod TODAY - граничные случаи`() {
        val now = System.currentTimeMillis()
        val startOfToday = getStartOfDay(now)
        
        val calls = listOf(
            createCall(startOfToday - 1, CallHistoryItem.CallStatus.CONNECTED), // За миллисекунду до начала дня
            createCall(startOfToday, CallHistoryItem.CallStatus.CONNECTED), // Ровно начало дня
            createCall(now, CallHistoryItem.CallStatus.CONNECTED) // Сейчас
        )
        
        val filtered = useCase.filterByPeriod(calls, CallStatsUseCase.Period.TODAY, now)
        
        assertEquals("Должно быть 2 звонка (начало дня и сейчас)", 2, filtered.size)
        assertTrue("Первый звонок не должен быть включён", filtered.none { it.startedAt < startOfToday })
    }
    
    @Test
    fun `filterByPeriod LAST_7_DAYS - граничные случаи`() {
        val now = System.currentTimeMillis()
        val calendar = Calendar.getInstance()
        calendar.timeInMillis = now
        calendar.add(Calendar.DAY_OF_YEAR, -6)
        calendar.set(Calendar.HOUR_OF_DAY, 0)
        calendar.set(Calendar.MINUTE, 0)
        calendar.set(Calendar.SECOND, 0)
        calendar.set(Calendar.MILLISECOND, 0)
        val sevenDaysAgoStart = calendar.timeInMillis
        
        val calls = listOf(
            createCall(sevenDaysAgoStart - 1, CallHistoryItem.CallStatus.CONNECTED), // За миллисекунду до начала периода
            createCall(sevenDaysAgoStart, CallHistoryItem.CallStatus.CONNECTED), // Ровно начало периода
            createCall(now, CallHistoryItem.CallStatus.CONNECTED) // Сейчас
        )
        
        val filtered = useCase.filterByPeriod(calls, CallStatsUseCase.Period.LAST_7_DAYS, now)
        
        assertEquals("Должно быть 2 звонка (начало периода и сейчас)", 2, filtered.size)
        assertTrue("Первый звонок не должен быть включён", filtered.none { it.startedAt < sevenDaysAgoStart })
    }
}
