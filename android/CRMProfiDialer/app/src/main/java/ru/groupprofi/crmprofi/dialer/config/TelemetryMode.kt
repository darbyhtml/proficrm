package ru.groupprofi.crmprofi.dialer.config

/**
 * Режим работы телеметрии и отправки данных в CRM.
 * 
 * LOCAL_ONLY: Приложение работает в локальном режиме, все данные сохраняются локально,
 *             но НЕ отправляются в CRM. Приложение является PRIMARY SOURCE OF TRUTH.
 * 
 * FULL: Полный режим - все данные отправляются в CRM как обычно.
 * 
 * ВАЖНО: При включении FULL режима все накопленные локальные данные будут готовы к отправке,
 *        но не будут отправлены автоматически (требуется явная миграция, если нужно).
 */
enum class TelemetryMode {
    LOCAL_ONLY,  // Локальный режим - данные только в приложении
    FULL         // Полный режим - отправка в CRM включена
}

/**
 * Feature flag для управления режимом телеметрии.
 * 
 * ИЗМЕНИТЬ НА TelemetryMode.FULL для включения отправки в CRM.
 */
object AppFeatures {
    /**
     * Текущий режим телеметрии.
     * По умолчанию: LOCAL_ONLY (данные не отправляются в CRM).
     * 
     * Для включения CRM измените на: TelemetryMode.FULL
     */
    val TELEMETRY_MODE: TelemetryMode = TelemetryMode.LOCAL_ONLY
    
    /**
     * Проверка, включена ли отправка в CRM.
     */
    fun isCrmEnabled(): Boolean = TELEMETRY_MODE == TelemetryMode.FULL
    
    /**
     * Feature flag для FCM push-ускорителя (НЕ включать по умолчанию).
     * Когда включен: push-уведомления ускоряют получение команд, но основная доставка остается через long-poll.
     * 
     * ВАЖНО: Требует настройки Firebase Cloud Messaging в проекте.
     * По умолчанию: false (push не используется).
     */
    val ENABLE_FCM_ACCELERATOR: Boolean = false
    
    /**
     * Проверка, включен ли FCM accelerator.
     */
    fun isFcmAcceleratorEnabled(): Boolean = ENABLE_FCM_ACCELERATOR
}
