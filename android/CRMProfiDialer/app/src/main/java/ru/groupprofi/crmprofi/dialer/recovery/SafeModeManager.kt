package ru.groupprofi.crmprofi.dialer.recovery

import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import androidx.core.content.ContextCompat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.CallListenerService
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import java.text.SimpleDateFormat
import java.util.*

/**
 * Менеджер безопасного перезапуска работы приложения (Safe Mode).
 * Используется в режиме поддержки для мягкого перезапуска без потери данных.
 */
object SafeModeManager {
    
    private const val PREFS_NAME = "safe_mode_prefs"
    private const val KEY_LAST_RESTART = "last_restart_timestamp"
    
    /**
     * Результат выполнения перезапуска.
     */
    sealed class SafeModeResult {
        object Success : SafeModeResult()
        data class PartialSuccess(val message: String) : SafeModeResult()
        data class Failed(val reason: String) : SafeModeResult()
    }
    
    /**
     * Перезапустить работу приложения безопасно.
     * 
     * Что делает:
     * - Останавливает AutoRecoveryManager
     * - Останавливает и перезапускает CallListenerService
     * - Очищает зависшие ожидаемые звонки (pending calls)
     * - НЕ очищает историю звонков
     * - НЕ очищает очередь отправки
     * - НЕ сбрасывает логин
     * - Инициирует проверку готовности и обновление уведомлений
     */
    suspend fun restartAppWork(context: Context): SafeModeResult = withContext(Dispatchers.IO) {
        try {
            AppLogger.i("SafeModeManager", "Начало безопасного перезапуска работы приложения")
            
            val appContext = context.applicationContext
            val errors = mutableListOf<String>()
            
            // 1. Остановить AutoRecoveryManager
            try {
                val autoRecoveryManager = AppContainer.autoRecoveryManager
                autoRecoveryManager.stop()
                AppLogger.i("SafeModeManager", "AutoRecoveryManager остановлен")
                delay(500) // Небольшая пауза
            } catch (e: Exception) {
                val error = "Не удалось остановить AutoRecoveryManager: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 2. Остановить CallListenerService
            try {
                val stopIntent = Intent(appContext, CallListenerService::class.java).apply {
                    action = CallListenerService.ACTION_STOP
                }
                appContext.stopService(stopIntent)
                AppLogger.i("SafeModeManager", "CallListenerService остановлен")
                delay(1000) // Пауза для корректной остановки
            } catch (e: Exception) {
                val error = "Не удалось остановить CallListenerService: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 3. Очистить зависшие ожидаемые звонки
            try {
                val pendingCallStore = AppContainer.pendingCallStore
                pendingCallStore.clearAll()
                AppLogger.i("SafeModeManager", "Ожидаемые звонки очищены")
            } catch (e: Exception) {
                val error = "Не удалось очистить ожидаемые звонки: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 4. Перезапустить CallListenerService
            try {
                val startIntent = Intent(appContext, CallListenerService::class.java).apply {
                    action = CallListenerService.ACTION_START
                }
                if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.O) {
                    ContextCompat.startForegroundService(appContext, startIntent)
                } else {
                    appContext.startService(startIntent)
                }
                AppLogger.i("SafeModeManager", "CallListenerService перезапущен")
                delay(1000) // Пауза для запуска
            } catch (e: Exception) {
                val error = "Не удалось перезапустить CallListenerService: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 5. Перезапустить AutoRecoveryManager
            try {
                val autoRecoveryManager = AppContainer.autoRecoveryManager
                autoRecoveryManager.start()
                AppLogger.i("SafeModeManager", "AutoRecoveryManager перезапущен")
            } catch (e: Exception) {
                val error = "Не удалось перезапустить AutoRecoveryManager: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 6. Инициировать проверку готовности и обновление уведомлений
            try {
                val readinessProvider = AppContainer.readinessProvider
                val state = readinessProvider.getState()
                val notificationManager = AppContainer.appNotificationManager
                
                // Скрыть уведомление "приложение не работает", если READY
                if (state == AppReadinessChecker.ReadyState.READY) {
                    notificationManager.dismissAppStateNotification()
                } else {
                    // Иначе показать по текущим правилам
                    notificationManager.showAppStateNotification(state)
                }
                AppLogger.i("SafeModeManager", "Уведомления обновлены, состояние: $state")
            } catch (e: Exception) {
                val error = "Не удалось обновить уведомления: ${e.message}"
                errors.add(error)
                AppLogger.w("SafeModeManager", error, e)
            }
            
            // 7. Сохранить timestamp последнего перезапуска
            saveLastRestartTimestamp(appContext)
            
            // Определяем результат
            when {
                errors.isEmpty() -> {
                    AppLogger.i("SafeModeManager", "Безопасный перезапуск выполнен успешно")
                    SafeModeResult.Success
                }
                errors.size < 3 -> {
                    val message = "Перезапуск выполнен с предупреждениями: ${errors.joinToString("; ")}"
                    AppLogger.w("SafeModeManager", message)
                    SafeModeResult.PartialSuccess(message)
                }
                else -> {
                    val reason = "Критические ошибки: ${errors.joinToString("; ")}"
                    AppLogger.e("SafeModeManager", reason)
                    SafeModeResult.Failed(reason)
                }
            }
        } catch (e: Exception) {
            val reason = "Неожиданная ошибка при перезапуске: ${e.message}"
            AppLogger.e("SafeModeManager", reason, e)
            SafeModeResult.Failed(reason)
        }
    }
    
    /**
     * Сохранить timestamp последнего перезапуска.
     */
    private fun saveLastRestartTimestamp(context: Context) {
        try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            prefs.edit().putLong(KEY_LAST_RESTART, System.currentTimeMillis()).apply()
        } catch (e: Exception) {
            AppLogger.w("SafeModeManager", "Не удалось сохранить timestamp: ${e.message}")
        }
    }
    
    /**
     * Получить timestamp последнего перезапуска.
     */
    fun getLastRestartTimestamp(context: Context): Long? {
        return try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val timestamp = prefs.getLong(KEY_LAST_RESTART, 0L)
            if (timestamp > 0) timestamp else null
        } catch (e: Exception) {
            AppLogger.w("SafeModeManager", "Не удалось получить timestamp: ${e.message}")
            null
        }
    }
    
    /**
     * Получить форматированную строку последнего перезапуска.
     */
    fun getLastRestartFormatted(context: Context): String? {
        val timestamp = getLastRestartTimestamp(context) ?: return null
        return try {
            val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())
            dateFormat.format(Date(timestamp))
        } catch (e: Exception) {
            null
        }
    }
}
