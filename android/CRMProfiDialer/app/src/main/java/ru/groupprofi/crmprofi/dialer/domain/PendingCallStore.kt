package ru.groupprofi.crmprofi.dialer.domain

import kotlinx.coroutines.flow.StateFlow

/**
 * Интерфейс для управления ожидаемыми звонками.
 * UI использует только этот интерфейс, не зная о реализации.
 */
interface PendingCallStore {
    /**
     * Поток наличия активных ожидаемых звонков.
     */
    val hasActivePendingCallsFlow: StateFlow<Boolean>
    
    /**
     * Добавить новый ожидаемый звонок.
     */
    suspend fun addPendingCall(call: PendingCall)
    
    /**
     * Получить ожидаемый звонок по ID запроса.
     */
    suspend fun getPendingCall(callRequestId: String): PendingCall?
    
    /**
     * Получить все активные ожидаемые звонки.
     */
    suspend fun getActivePendingCalls(): List<PendingCall>
    
    /**
     * Обновить состояние звонка.
     */
    suspend fun updateCallState(
        callRequestId: String,
        newState: PendingCall.PendingState,
        incrementAttempts: Boolean = false
    )
    
    /**
     * Удалить ожидаемый звонок (после успешного определения результата).
     */
    suspend fun removePendingCall(callRequestId: String)
    
    /**
     * Очистить старые завершённые звонки.
     */
    suspend fun cleanupOldCalls()
    
    /**
     * Очистить все ожидаемые звонки (для Safe Mode).
     * Используется только в режиме поддержки для сброса зависших состояний.
     */
    suspend fun clearAll()
}
