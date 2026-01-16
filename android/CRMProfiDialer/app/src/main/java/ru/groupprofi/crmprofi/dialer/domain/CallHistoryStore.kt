package ru.groupprofi.crmprofi.dialer.domain

import kotlinx.coroutines.flow.StateFlow

/**
 * Интерфейс для хранения истории звонков.
 * UI использует только этот интерфейс, не зная о реализации.
 */
interface CallHistoryStore {
    /**
     * Поток всех звонков в истории.
     */
    val callsFlow: StateFlow<List<CallHistoryItem>>
    
    /**
     * Поток количества звонков в истории.
     */
    val countFlow: StateFlow<Int>
    
    /**
     * Добавить или обновить звонок в истории.
     */
    suspend fun addOrUpdate(item: CallHistoryItem)
    
    /**
     * Отметить звонок как отправленный в CRM.
     */
    suspend fun markSent(callRequestId: String, sentAt: Long)
    
    /**
     * Получить все звонки (синхронно, для совместимости).
     */
    suspend fun getAllCalls(): List<CallHistoryItem>
    
    /**
     * Получить звонок по ID.
     */
    suspend fun getCallById(id: String): CallHistoryItem?
}
