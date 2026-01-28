package ru.groupprofi.crmprofi.dialer.domain

/**
 * Единая причина, почему сервис/приложение не готовы к звонкам.
 *
 * Важно:
 * - Это НЕ "ошибка" — это состояние, которое нужно явно показать пользователю.
 * - Причина хранится локально (TokenManager) и используется в AppReadinessChecker/диагностике.
 */
enum class ServiceBlockReason(
    val userTitle: String,
    val userMessage: String
) {
    AUTH_MISSING(
        userTitle = "Нет авторизации",
        userMessage = "Нужно войти в систему, чтобы принимать команды на звонок."
    ),
    DEVICE_ID_MISSING(
        userTitle = "Нет device_id",
        userMessage = "Устройство ещё не зарегистрировано. Перезапустите приложение и проверьте вход."
    ),
    NOTIFICATIONS_DISABLED(
        userTitle = "Уведомления отключены",
        userMessage = "Включите уведомления для приложения, иначе вы не увидите задачу на звонок."
    ),
    NOTIFICATION_PERMISSION_MISSING(
        userTitle = "Нет разрешения на уведомления",
        userMessage = "Дайте разрешение на уведомления (Android 13+), иначе уведомления о звонках не будут показываться."
    ),
    FOREGROUND_START_FAILED(
        userTitle = "Не удалось запустить работу в фоне",
        userMessage = "Система не дала запустить фоновую работу. Откройте диагностику и разрешите работу в фоне."
    ),
    BATTERY_OPTIMIZATION(
        userTitle = "Ограничения батареи",
        userMessage = "Рекомендуется разрешить работу в фоне (Battery optimization), иначе сервис может быть остановлен системой."
    ),
    UNKNOWN(
        userTitle = "Неизвестная причина",
        userMessage = "Приложение не готово к звонкам по неизвестной причине. Откройте диагностику."
    )
}

