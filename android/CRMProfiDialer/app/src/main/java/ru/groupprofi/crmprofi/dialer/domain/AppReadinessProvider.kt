package ru.groupprofi.crmprofi.dialer.domain

/**
 * Интерфейс для получения состояния готовности приложения.
 * UI использует только этот интерфейс, не зная о реализации.
 */
interface AppReadinessProvider {
    /**
     * Получить текущее состояние готовности.
     */
    fun getState(): AppReadinessChecker.ReadyState
    
    /**
     * Получить модель для отображения в UI.
     */
    fun getUiModel(): AppReadinessChecker.ReadyUiModel
}
