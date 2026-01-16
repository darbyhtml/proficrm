package ru.groupprofi.crmprofi.dialer.recovery

import android.content.Context
import android.content.Intent
import android.os.Build
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.CallListenerService
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.notifications.AppNotificationManager
import java.util.concurrent.atomic.AtomicInteger

/**
 * Менеджер автоматического восстановления приложения.
 * Проверяет состояние готовности и пытается исправить проблемы автоматически.
 */
class AutoRecoveryManager private constructor(context: Context) {
    private val appContext = context.applicationContext
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val readinessChecker = AppReadinessChecker(appContext)
    private val tokenManager = TokenManager.getInstance(appContext)
    private val notificationManager = AppNotificationManager.getInstance(appContext)
    
    private var recoveryJob: Job? = null
    private val recoveryAttempts = AtomicInteger(0)
    private var lastNotificationState: AppReadinessChecker.ReadyState? = null
    private var lastNotificationTime: Long = 0
    private val NOTIFICATION_COOLDOWN_MS = 15 * 60 * 1000L // 15 минут
    
    companion object {
        @Volatile
        private var INSTANCE: AutoRecoveryManager? = null
        
        fun getInstance(context: Context): AutoRecoveryManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: AutoRecoveryManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    /**
     * Запустить автоматическое восстановление.
     */
    fun start() {
        if (recoveryJob?.isActive == true) {
            return
        }
        
        recoveryJob = scope.launch {
            while (true) {
                try {
                    checkAndRecover()
                    delay(30000) // Проверяем каждые 30 секунд
                } catch (e: Exception) {
                    AppLogger.e("AutoRecoveryManager", "Ошибка в цикле восстановления: ${e.message}", e)
                    delay(60000) // При ошибке ждём минуту
                }
            }
        }
        
        AppLogger.i("AutoRecoveryManager", "Автоматическое восстановление запущено")
    }
    
    /**
     * Остановить автоматическое восстановление.
     */
    fun stop() {
        recoveryJob?.cancel()
        recoveryJob = null
        AppLogger.i("AutoRecoveryManager", "Автоматическое восстановление остановлено")
    }
    
    /**
     * Проверить состояние и попытаться восстановить.
     */
    private suspend fun checkAndRecover() {
        val state = readinessChecker.checkReadiness()
        
        when (state) {
            AppReadinessChecker.ReadyState.READY -> {
                // Всё готово - скрываем уведомление и сбрасываем счётчик попыток
                notificationManager.dismissAppStateNotification()
                recoveryAttempts.set(0)
                lastNotificationState = null
                return
            }
            
            AppReadinessChecker.ReadyState.NO_NETWORK -> {
                // Нет сети - ничего не делаем, просто ждём
                // Не показываем уведомление, чтобы не спамить
                return
            }
            
            AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS,
            AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS -> {
                // Разрешения/уведомления - не пытаемся чинить автоматически
                // Только показываем уведомление (с антидребезгом)
                showAppStateNotificationIfNeeded(state)
                return
            }
            
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> {
                // Нужна авторизация - пытаемся обновить токен один раз
                if (recoveryAttempts.get() == 0) {
                    tryRefreshToken()
                } else {
                    // Если не удалось - показываем уведомление
                    showAppStateNotificationIfNeeded(state)
                }
            }
            
            AppReadinessChecker.ReadyState.SERVICE_STOPPED -> {
                // Сервис остановлен - пытаемся перезапустить (с лимитом попыток)
                if (recoveryAttempts.get() < 3) {
                    tryRestartService()
                } else {
                    // Превышен лимит - показываем уведомление
                    showAppStateNotificationIfNeeded(state)
                }
            }
            
            AppReadinessChecker.ReadyState.UNKNOWN_ERROR -> {
                // Неизвестная ошибка - показываем уведомление
                showAppStateNotificationIfNeeded(state)
            }
        }
    }
    
    /**
     * Показать уведомление "Приложение не работает" с антидребезгом.
     */
    private fun showAppStateNotificationIfNeeded(state: AppReadinessChecker.ReadyState) {
        val now = System.currentTimeMillis()
        val stateChanged = lastNotificationState != state
        val cooldownPassed = (now - lastNotificationTime) >= NOTIFICATION_COOLDOWN_MS
        
        if (stateChanged || cooldownPassed) {
            notificationManager.showAppStateNotification(state)
            lastNotificationState = state
            lastNotificationTime = now
        }
    }
    
    /**
     * Попытаться обновить токен.
     */
    private suspend fun tryRefreshToken() {
        try {
            AppLogger.i("AutoRecoveryManager", "Попытка обновления токена...")
            
            val refreshToken = tokenManager.getRefreshToken()
            if (refreshToken.isNullOrBlank()) {
                AppLogger.w("AutoRecoveryManager", "Refresh токен отсутствует")
                recoveryAttempts.incrementAndGet()
                return
            }
            
            // Используем ApiClient для обновления токена
            val apiClient = ru.groupprofi.crmprofi.dialer.network.ApiClient.getInstance(appContext)
            val result = apiClient.refreshToken()
            
            when (result) {
                is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success -> {
                    val access = result.data
                    if (access != null) {
                        tokenManager.updateAccessToken(access)
                        AppLogger.i("AutoRecoveryManager", "Токен успешно обновлён")
                        recoveryAttempts.set(0)
                    } else {
                        AppLogger.w("AutoRecoveryManager", "Refresh token вернул null")
                        recoveryAttempts.incrementAndGet()
                    }
                }
                else -> {
                    AppLogger.w("AutoRecoveryManager", "Не удалось обновить токен: ${(result as? ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Error)?.message}")
                    recoveryAttempts.incrementAndGet()
                }
            }
        } catch (e: Exception) {
            AppLogger.e("AutoRecoveryManager", "Ошибка обновления токена: ${e.message}", e)
            recoveryAttempts.incrementAndGet()
        }
    }
    
    /**
     * Попытаться перезапустить сервис.
     */
    private suspend fun tryRestartService() {
        try {
            AppLogger.i("AutoRecoveryManager", "Попытка перезапуска сервиса... (попытка ${recoveryAttempts.get() + 1}/3)")
            
            val deviceId = tokenManager.getDeviceId() ?: return
            val accessToken = tokenManager.getAccessToken() ?: return
            val refreshToken = tokenManager.getRefreshToken() ?: return
            
            val intent = Intent(appContext, CallListenerService::class.java).apply {
                putExtra(CallListenerService.EXTRA_TOKEN, accessToken)
                putExtra(CallListenerService.EXTRA_REFRESH, refreshToken)
                putExtra(CallListenerService.EXTRA_DEVICE_ID, deviceId)
            }
            
            if (Build.VERSION.SDK_INT >= 26) {
                appContext.startForegroundService(intent)
            } else {
                appContext.startService(intent)
            }
            
            // Даём время на запуск
            delay(2000)
            
            // Проверяем, запустился ли сервис
            val newState = readinessChecker.checkReadiness()
            if (newState == AppReadinessChecker.ReadyState.READY || 
                newState == AppReadinessChecker.ReadyState.NEEDS_AUTH) {
                AppLogger.i("AutoRecoveryManager", "Сервис успешно перезапущен")
                recoveryAttempts.set(0)
            } else {
                AppLogger.w("AutoRecoveryManager", "Сервис не запустился, состояние: $newState")
                recoveryAttempts.incrementAndGet()
            }
        } catch (e: Exception) {
            AppLogger.e("AutoRecoveryManager", "Ошибка перезапуска сервиса: ${e.message}", e)
            recoveryAttempts.incrementAndGet()
        }
    }
}
