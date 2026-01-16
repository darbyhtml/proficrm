package ru.groupprofi.crmprofi.dialer.support

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.recovery.SafeModeManager
import ru.groupprofi.crmprofi.dialer.support.CrashLogStore
import java.text.SimpleDateFormat
import java.util.*

/**
 * Построитель текстового отчёта диагностики для поддержки.
 * Безопасно: не включает токены, полные номера, персональные данные.
 */
object SupportReportBuilder {
    
    /**
     * Построить текстовый отчёт диагностики.
     */
    fun build(context: Context): String {
        val sb = StringBuilder()
        
        // Заголовок
        sb.appendLine("CRM Profi Dialer — Диагностика")
        sb.appendLine("=".repeat(40))
        sb.appendLine()
        
        // Дата/время
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())
        sb.appendLine("Дата/время: ${dateFormat.format(Date())}")
        
        // Версия приложения
        val appVersion = getAppVersion(context)
        sb.appendLine("Версия: $appVersion")
        
        // Устройство
        val deviceInfo = getDeviceInfo()
        sb.appendLine("Устройство: $deviceInfo")
        sb.appendLine()
        
        // Готовность
        val readinessProvider = AppContainer.readinessProvider
        val state = readinessProvider.getState()
        val uiModel = readinessProvider.getUiModel()
        sb.appendLine("Готовность: ${getReadinessStatus(state, uiModel)}")
        
        // Разрешения
        val permissionsStatus = getPermissionsStatus(context)
        sb.appendLine("Разрешения: $permissionsStatus")
        
        // Уведомления
        val notificationsStatus = getNotificationsStatus(context)
        sb.appendLine("Уведомления: $notificationsStatus")
        
        // Сеть
        val networkStatus = getNetworkStatus(context)
        sb.appendLine("Сеть: $networkStatus")
        
        // Авторизация
        val authStatus = getAuthStatus(state)
        sb.appendLine("Авторизация: $authStatus")
        sb.appendLine()
        
        // Очередь
        val queueStatus = getQueueStatus(context)
        sb.appendLine("Очередь: $queueStatus")
        
        // Ожидаемые звонки
        val pendingCallsStatus = getPendingCallsStatus()
        sb.appendLine("Ожидаемые звонки: $pendingCallsStatus")
        
        // История звонков
        val historyStatus = getHistoryStatus()
        sb.appendLine("История: $historyStatus")
        
        // Последняя активность (если доступна)
        val lastActivity = getLastActivity(context)
        if (lastActivity.isNotEmpty()) {
            sb.appendLine("Последняя активность: $lastActivity")
        }
        
        // Safe Mode: последний перезапуск
        val lastRestart = SafeModeManager.getLastRestartFormatted(context)
        if (lastRestart != null) {
            sb.appendLine("Safe mode: последний перезапуск: $lastRestart")
        } else {
            sb.appendLine("Safe mode: доступен")
        }
        
        // Последний сбой
        val lastCrashTime = CrashLogStore.getLastCrashTime(context)
        if (lastCrashTime != null) {
            val crashSummary = CrashLogStore.getLastCrashSummary(context)
            val crashDate = dateFormat.format(Date(lastCrashTime))
            val exceptionType = crashSummary?.lines()?.firstOrNull()?.substringAfter("Exception: ") ?: "неизвестно"
            sb.appendLine("Последний сбой: $crashDate ($exceptionType)")
        } else {
            sb.appendLine("Последний сбой: не было")
        }
        
        // Build info
        val buildType = getBuildType(context)
        val minifyStatus = getMinifyStatus(context)
        sb.appendLine("Build type: $buildType")
        sb.appendLine("Минификация: $minifyStatus")
        sb.appendLine()
        
        // Рекомендация
        val recommendation = getRecommendation(state, uiModel)
        sb.appendLine("Рекомендация:")
        sb.appendLine(recommendation)
        
        return sb.toString()
    }
    
    /**
     * Получить версию приложения.
     */
    private fun getAppVersion(context: Context): String {
        return try {
            val pm = context.packageManager
            val pkgInfo = pm.getPackageInfo(context.packageName, 0)
            "${pkgInfo.versionName} (${pkgInfo.longVersionCode})"
        } catch (e: Exception) {
            "Неизвестно"
        }
    }
    
    /**
     * Получить информацию об устройстве (безопасно, без IMEI/серийников).
     */
    private fun getDeviceInfo(): String {
        val sdk = Build.VERSION.SDK_INT
        val model = Build.MODEL
        val manufacturer = Build.MANUFACTURER
        
        // Маскируем модель устройства
        val maskedModel = if (model.length > 8) {
            "${model.take(4)}***"
        } else {
            model
        }
        
        return "$manufacturer $maskedModel, Android ${Build.VERSION.RELEASE} (SDK $sdk)"
    }
    
    /**
     * Получить статус готовности.
     */
    private fun getReadinessStatus(
        state: AppReadinessChecker.ReadyState,
        uiModel: AppReadinessChecker.ReadyUiModel
    ): String {
        return when (state) {
            AppReadinessChecker.ReadyState.READY -> "ГОТОВО"
            AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS -> "НЕ ГОТОВО (Нужны разрешения)"
            AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS -> "НЕ ГОТОВО (Уведомления выключены)"
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> "НЕ ГОТОВО (Нужна авторизация)"
            AppReadinessChecker.ReadyState.NO_NETWORK -> "НЕ ГОТОВО (Нет сети)"
            AppReadinessChecker.ReadyState.SERVICE_STOPPED -> "НЕ ГОТОВО (Сервис остановлен)"
            AppReadinessChecker.ReadyState.UNKNOWN_ERROR -> "НЕ ГОТОВО (Неизвестная ошибка)"
        }
    }
    
    /**
     * Получить статус разрешений.
     */
    private fun getPermissionsStatus(context: Context): String {
        val hasCallLog = ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        
        val hasPhoneState = ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.READ_PHONE_STATE
        ) == PackageManager.PERMISSION_GRANTED
        
        return when {
            hasCallLog && hasPhoneState -> "OK"
            !hasCallLog && !hasPhoneState -> "READ_CALL_LOG=нет, READ_PHONE_STATE=нет"
            !hasCallLog -> "READ_CALL_LOG=нет, READ_PHONE_STATE=да"
            else -> "READ_CALL_LOG=да, READ_PHONE_STATE=нет"
        }
    }
    
    /**
     * Получить статус уведомлений.
     */
    private fun getNotificationsStatus(context: Context): String {
        val enabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        return if (enabled) "включены" else "выключены"
    }
    
    /**
     * Получить статус сети.
     */
    private fun getNetworkStatus(context: Context): String {
        val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager
        val network = connectivityManager?.activeNetwork
        val capabilities = connectivityManager?.getNetworkCapabilities(network)
        val hasNetwork = capabilities != null && (
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) ||
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) ||
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET)
        )
        return if (hasNetwork) "есть" else "нет сети"
    }
    
    /**
     * Получить статус авторизации.
     */
    private fun getAuthStatus(state: AppReadinessChecker.ReadyState): String {
        return when (state) {
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> "нужно войти"
            AppReadinessChecker.ReadyState.READY -> {
                val tokenManager = AppContainer.tokenManager
                if (tokenManager.hasTokens()) "ок" else "неизвестно"
            }
            else -> {
                val tokenManager = AppContainer.tokenManager
                if (tokenManager.hasTokens()) "ок" else "неизвестно"
            }
        }
    }
    
    /**
     * Получить статус очереди.
     */
    private fun getQueueStatus(context: Context): String {
        return try {
            val queueManager = ru.groupprofi.crmprofi.dialer.queue.QueueManager(context)
            val stats = queueManager.getStats()
            "${stats.pendingCount} элементов (ожидают отправки)"
        } catch (e: Exception) {
            "не удалось проверить"
        }
    }
    
    /**
     * Получить статус ожидаемых звонков.
     */
    private fun getPendingCallsStatus(): String {
        return try {
            val pendingCallStore = AppContainer.pendingCallStore
            val hasActive = pendingCallStore.hasActivePendingCallsFlow.value
            if (hasActive) {
                // Пытаемся получить количество (асинхронно, но используем текущее значение)
                "есть активные"
            } else {
                "нет"
            }
        } catch (e: Exception) {
            "не удалось проверить"
        }
    }
    
    /**
     * Получить статус истории звонков.
     */
    private fun getHistoryStatus(): String {
        return try {
            val callHistoryStore = AppContainer.callHistoryStore
            val allCalls = callHistoryStore.callsFlow.value
            val statsUseCase = CallStatsUseCase()
            val todayStats = statsUseCase.calculate(allCalls, CallStatsUseCase.Period.TODAY)
            "всего ${allCalls.size}, сегодня ${todayStats.total}"
        } catch (e: Exception) {
            "не удалось проверить"
        }
    }
    
    /**
     * Получить информацию о последней активности (если доступна).
     */
    private fun getLastActivity(context: Context): String {
        // Можно добавить сохранение времени последнего успешного poll/send в SharedPreferences
        // Пока возвращаем пустую строку, если данных нет
        return ""
    }
    
    /**
     * Получить build type.
     */
    private fun getBuildType(context: Context): String {
        return try {
            if (ru.groupprofi.crmprofi.dialer.BuildConfig.DEBUG) {
                "debug"
            } else {
                "release"
            }
        } catch (e: Exception) {
            "unknown"
        }
    }
    
    /**
     * Получить статус минификации.
     */
    private fun getMinifyStatus(context: Context): String {
        return try {
            if (ru.groupprofi.crmprofi.dialer.BuildConfig.DEBUG) {
                "нет"
            } else {
                "да (staging/release)"
            }
        } catch (e: Exception) {
            "неизвестно"
        }
    }
    
    /**
     * Получить рекомендацию на основе состояния.
     */
    private fun getRecommendation(
        state: AppReadinessChecker.ReadyState,
        uiModel: AppReadinessChecker.ReadyUiModel
    ): String {
        return when (state) {
            AppReadinessChecker.ReadyState.READY -> {
                "Приложение готово к работе. Если есть проблемы, проверьте очередь и ожидаемые звонки."
            }
            AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS -> {
                "Откройте приложение → нажмите \"Исправить\" → выдайте разрешения READ_CALL_LOG и READ_PHONE_STATE в настройках Android."
            }
            AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS -> {
                "Откройте приложение → нажмите \"Исправить\" → включите уведомления в настройках Android."
            }
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> {
                "Войдите в приложение через форму входа или QR-код."
            }
            AppReadinessChecker.ReadyState.NO_NETWORK -> {
                "Проверьте подключение к интернету (Wi-Fi или мобильные данные)."
            }
            AppReadinessChecker.ReadyState.SERVICE_STOPPED -> {
                "Перезапустите приложение или нажмите \"Исправить\" для перезапуска сервиса."
            }
            else -> {
                "Откройте приложение → нажмите \"Исправить\" для диагностики и исправления проблем."
            }
        }
    }
}
