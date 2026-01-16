package ru.groupprofi.crmprofi.dialer.domain

import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.CallListenerService

/**
 * Единый источник правды о состоянии готовности приложения.
 * Определяет, готово ли приложение к работе, и что нужно исправить.
 */
class AppReadinessChecker(private val context: Context) : AppReadinessProvider {
    private val tokenManager = TokenManager.getInstance(context)
    
    /**
     * Состояние готовности приложения.
     */
    enum class ReadyState {
        READY,                      // Всё готово, работает
        NEEDS_PERMISSIONS,          // Нужны разрешения (CallLog, PhoneState)
        NEEDS_NOTIFICATIONS,        // Уведомления отключены
        NEEDS_AUTH,                 // Нет авторизации или токен истёк
        NO_NETWORK,                 // Нет сети
        SERVICE_STOPPED,            // Сервис остановлен
        UNKNOWN_ERROR               // Неизвестная ошибка
    }
    
    /**
     * Модель для отображения в UI.
     */
    data class ReadyUiModel(
        val iconEmoji: String,           // 🟢 или 🔴
        val title: String,                // "Готово к звонкам" или "Не работает"
        val message: String,              // Объяснение простым языком
        val showFixButton: Boolean,       // Показывать ли кнопку "Исправить"
        val fixActionType: FixActionType  // Тип действия для кнопки
    )
    
    /**
     * Тип действия для кнопки "Исправить".
     */
    enum class FixActionType {
        REQUEST_PERMISSIONS,      // Запросить разрешения
        OPEN_NOTIFICATION_SETTINGS, // Открыть настройки уведомлений
        SHOW_LOGIN,               // Показать экран входа
        OPEN_NETWORK_SETTINGS,    // Открыть настройки сети
        RESTART_SERVICE,          // Перезапустить сервис
        NONE                      // Ничего не делать
    }
    
    /**
     * Проверить состояние готовности приложения.
     */
    fun checkReadiness(): ReadyState {
        return getState()
    }
    
    /**
     * Получить текущее состояние готовности (реализация интерфейса).
     */
    override fun getState(): ReadyState {
        // 1. Проверка авторизации (самое важное)
        if (!tokenManager.hasTokens()) {
            return ReadyState.NEEDS_AUTH
        }
        
        val accessToken = tokenManager.getAccessToken()
        if (accessToken.isNullOrBlank()) {
            return ReadyState.NEEDS_AUTH
        }
        
        // 2. Проверка разрешений
        val callLogPerm = android.Manifest.permission.READ_CALL_LOG
        val phoneStatePerm = android.Manifest.permission.READ_PHONE_STATE
        val hasCallLog = ContextCompat.checkSelfPermission(context, callLogPerm) == PackageManager.PERMISSION_GRANTED
        val hasPhoneState = ContextCompat.checkSelfPermission(context, phoneStatePerm) == PackageManager.PERMISSION_GRANTED
        
        if (!hasCallLog || !hasPhoneState) {
            return ReadyState.NEEDS_PERMISSIONS
        }
        
        // 3. Проверка уведомлений (Android 13+)
        if (Build.VERSION.SDK_INT >= 33) {
            val notifPerm = android.Manifest.permission.POST_NOTIFICATIONS
            val hasNotifPerm = ContextCompat.checkSelfPermission(context, notifPerm) == PackageManager.PERMISSION_GRANTED
            if (!hasNotifPerm) {
                return ReadyState.NEEDS_NOTIFICATIONS
            }
        }
        
        // Проверка включены ли уведомления в настройках
        if (!NotificationManagerCompat.from(context).areNotificationsEnabled()) {
            return ReadyState.NEEDS_NOTIFICATIONS
        }
        
        // 4. Проверка сети
        if (!isNetworkAvailable()) {
            return ReadyState.NO_NETWORK
        }
        
        // 5. Проверка сервиса (есть ли недавний polling)
        val lastPollCode = tokenManager.getLastPollCode()
        val lastPollAt = tokenManager.getLastPollAt()
        
        // Если последний опрос был больше 2 минут назад и код не 0 (не сеть) - сервис мог остановиться
        if (lastPollAt != null && lastPollCode != -1 && lastPollCode != 0) {
            val lastPollTime = try {
                val sdf = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
                sdf.parse(lastPollAt)?.time ?: 0L
            } catch (e: Exception) {
                0L
            }
            
            val now = System.currentTimeMillis()
            val diff = now - lastPollTime
            // Если прошло больше 2 минут - сервис мог остановиться
            if (diff > 120000) {
                return ReadyState.SERVICE_STOPPED
            }
        }
        
        // Если код 401 - нужна авторизация
        if (lastPollCode == 401) {
            return ReadyState.NEEDS_AUTH
        }
        
        // Всё готово
        return ReadyState.READY
    }
    
    /**
     * Получить модель для UI (реализация интерфейса).
     */
    override fun getUiModel(): ReadyUiModel {
        return getUiModel(getState())
    }
    
    /**
     * Получить модель для UI на основе состояния готовности.
     */
    fun getUiModel(state: ReadyState): ReadyUiModel {
        return when (state) {
            ReadyState.READY -> ReadyUiModel(
                iconEmoji = "🟢",
                title = "Готово к звонкам",
                message = "Приложение работает в фоне и готово принимать команды из CRM",
                showFixButton = false,
                fixActionType = FixActionType.NONE
            )
            
            ReadyState.NEEDS_PERMISSIONS -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Нужны разрешения",
                message = "Нужны разрешения для работы. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.REQUEST_PERMISSIONS
            )
            
            ReadyState.NEEDS_NOTIFICATIONS -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Нужны уведомления",
                message = "Нужно включить уведомления. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.OPEN_NOTIFICATION_SETTINGS
            )
            
            ReadyState.NEEDS_AUTH -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Нужен вход",
                message = "Нужно войти в систему. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.SHOW_LOGIN
            )
            
            ReadyState.NO_NETWORK -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Нет интернета",
                message = "Нет подключения к интернету. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.OPEN_NETWORK_SETTINGS
            )
            
            ReadyState.SERVICE_STOPPED -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Приложение остановлено",
                message = "Приложение остановлено. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.RESTART_SERVICE
            )
            
            ReadyState.UNKNOWN_ERROR -> ReadyUiModel(
                iconEmoji = "🔴",
                title = "Не работает",
                message = "Произошла ошибка. Нажмите «Исправить» — мы поможем настроить.",
                showFixButton = true,
                fixActionType = FixActionType.RESTART_SERVICE
            )
        }
    }
    
    /**
     * Проверить доступность сети.
     */
    private fun isNetworkAvailable(): Boolean {
        val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
            ?: return false
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            val network = connectivityManager.activeNetwork ?: return false
            val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
            return capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET) &&
                   capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED)
        } else {
            @Suppress("DEPRECATION")
            val networkInfo = connectivityManager.activeNetworkInfo
            return networkInfo?.isConnected == true
        }
    }
}
