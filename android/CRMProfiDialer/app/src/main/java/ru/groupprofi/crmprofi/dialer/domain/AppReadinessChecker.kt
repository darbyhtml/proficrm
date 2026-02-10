package ru.groupprofi.crmprofi.dialer.domain

import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.os.PowerManager
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.CallListenerService

/**
 * Единый источник правды о состоянии готовности приложения.
 * Определяет, готово ли приложение к работе, и что нужно исправить.
 */
class AppReadinessChecker(private val context: Context) : AppReadinessProvider {
    /**
     * TokenManager может быть ещё не инициализирован сразу после старта процесса.
     * Используем getInstanceOrNull() и деградируем в NEEDS_AUTH вместо крэша.
     */
    private val tokenManager: TokenManager?
        get() = TokenManager.getInstanceOrNull()
    
    /**
     * Состояние готовности приложения.
     */
    enum class ReadyState {
        READY,                      // Всё готово, работает
        NEEDS_PERMISSIONS,          // Нужны разрешения (CallLog, PhoneState)
        NEEDS_NOTIFICATIONS,        // Уведомления отключены
        NEEDS_AUTH,                 // Нет авторизации или токен истёк
        NO_NETWORK,                 // Нет сети
        SERVICE_BLOCKED,            // Сервис/приложение заблокированы причиной (ServiceBlockReason)
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
        OPEN_BATTERY_SETTINGS,    // Открыть настройки работы в фоне / battery optimization
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
        val tm = tokenManager ?: return ReadyState.NEEDS_AUTH

        // 0. Явная причина блокировки (сохраняется сервисом, чтобы не было silent fail)
        // Если причина есть, показываем её пользователю как "не готово к звонкам".
        // Важно: авторизация/permissions по-прежнему имеют приоритет.
        val serviceBlockReason = tm.getServiceBlockReason()

        // 1. Проверка авторизации (самое важное)
        val hasTokens = tm.hasTokens() && !tm.getAccessToken().isNullOrBlank()
        if (!hasTokens) {
            return ReadyState.NEEDS_AUTH
        }
        // При восстановлении авторизации очищаем связанные причины блокировки.
        if (serviceBlockReason == ServiceBlockReason.AUTH_MISSING) {
            tm.setServiceBlockReason(null)
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
            } else if (serviceBlockReason == ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING) {
                // Разрешение выдали — очищаем причину блокировки.
                tm.setServiceBlockReason(null)
            }
        }
        
        // Проверка включены ли уведомления в настройках
        val notificationsEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        if (!notificationsEnabled) {
            return ReadyState.NEEDS_NOTIFICATIONS
        } else if (serviceBlockReason == ServiceBlockReason.NOTIFICATIONS_DISABLED) {
            // Уведомления включили — очищаем причину блокировки.
            tm.setServiceBlockReason(null)
        }
        
        // 4. Проверка сети
        if (!isNetworkAvailable()) {
            return ReadyState.NO_NETWORK
        }

        // Если до сюда дошли — базовые условия ОК. Если сервис ранее сообщил причину блокировки,
        // отражаем её, пока пользователь не исправит (или пока сервис/чекер не очистят причину).
        if (serviceBlockReason != null) {
            // Для причин, зависящих от системных настроек (battery optimization, foreground failure),
            // проверяем, не восстановились ли условия, и при необходимости очищаем причину.
            val cleared = when (serviceBlockReason) {
                ServiceBlockReason.BATTERY_OPTIMIZATION -> {
                    if (isIgnoringBatteryOptimizationsSafe()) {
                        tm.setServiceBlockReason(null)
                        true
                    } else {
                        false
                    }
                }
                ServiceBlockReason.FOREGROUND_START_FAILED -> {
                    // Если foreground уже успешно запускался, считаем, что причина устарела.
                    val lastOk = tm.getLastServiceForegroundOkAt()
                    if (lastOk != null && lastOk > 0L) {
                        tm.setServiceBlockReason(null)
                        true
                    } else {
                        false
                    }
                }
                ServiceBlockReason.AUTH_MISSING,
                ServiceBlockReason.DEVICE_ID_MISSING,
                ServiceBlockReason.NOTIFICATIONS_DISABLED,
                ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING,
                ServiceBlockReason.UNKNOWN -> false
            }
            if (!cleared) {
                return ReadyState.SERVICE_BLOCKED
            }
        }
        
        // 5. Проверка сервиса (есть ли недавний polling)
        val lastPollCode = tm.getLastPollCode()
        val lastPollAt = tm.getLastPollAt()
        
        // Если код 401 - нужна авторизация (приоритет)
        if (lastPollCode == 401) {
            return ReadyState.NEEDS_AUTH
        }
        
        // Проверяем, запущен ли сервис (если есть данные о последнем опросе)
        if (lastPollAt != null && lastPollCode != -1 && lastPollCode != 0) {
            val lastPollTime = try {
                // Парсим время в формате timestamp (миллисекунды) или HH:mm:ss
                val timestamp = lastPollAt.toLongOrNull()
                if (timestamp != null) {
                    timestamp
                } else {
                    // Пробуем парсить как время HH:mm:ss (относительно текущего дня)
                    val sdf = java.text.SimpleDateFormat("HH:mm:ss", java.util.Locale.getDefault())
                    val today = java.util.Calendar.getInstance()
                    val parsedTime = sdf.parse(lastPollAt)
                    if (parsedTime != null) {
                        val cal = java.util.Calendar.getInstance()
                        cal.time = parsedTime
                        today.set(java.util.Calendar.HOUR_OF_DAY, cal.get(java.util.Calendar.HOUR_OF_DAY))
                        today.set(java.util.Calendar.MINUTE, cal.get(java.util.Calendar.MINUTE))
                        today.set(java.util.Calendar.SECOND, cal.get(java.util.Calendar.SECOND))
                        today.set(java.util.Calendar.MILLISECOND, 0)
                        today.timeInMillis
                    } else {
                        0L
                    }
                }
            } catch (e: Exception) {
                0L
            }
            
            if (lastPollTime > 0) {
                val now = System.currentTimeMillis()
                val diff = now - lastPollTime
                // Если прошло больше 3 минут - сервис мог остановиться (увеличено с 2 до 3 минут для надежности)
                if (diff > 180000) {
                    return ReadyState.SERVICE_STOPPED
                }
            }
        } else {
            // Если нет данных о последнем опросе - проверяем, запущен ли сервис через проверку процесса
            // Если сервис только что запустился, даем ему время на первый опрос (не считаем остановленным)
            // В этом случае считаем, что сервис работает (READY)
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
    
    private fun isIgnoringBatteryOptimizationsSafe(): Boolean {
        // Battery optimization APIs доступны с Android 6 (API 23)
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return true
        return try {
            val pm = context.getSystemService(Context.POWER_SERVICE) as? PowerManager
            pm?.isIgnoringBatteryOptimizations(context.packageName) == true
        } catch (_: Exception) {
            // На некоторых OEM может быть странное поведение — не блокируем работу.
            true
        }
    }

    /**
     * Получить модель для UI на основе состояния готовности.
     */
    fun getUiModel(state: ReadyState): ReadyUiModel {
        return when (state) {
            ReadyState.READY -> ReadyUiModel(
                iconEmoji = "🟢",
                title = "Готово к звонкам",
                message = if (!isIgnoringBatteryOptimizationsSafe()) {
                    "Приложение готово. Для надёжной работы в фоне рекомендуется разрешить работу без ограничений батареи."
                } else {
                    "Приложение работает в фоне и готово принимать команды из CRM"
                },
                showFixButton = !isIgnoringBatteryOptimizationsSafe(),
                fixActionType = if (!isIgnoringBatteryOptimizationsSafe()) FixActionType.OPEN_BATTERY_SETTINGS else FixActionType.NONE
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

            ReadyState.SERVICE_BLOCKED -> {
                val reason = tokenManager.getServiceBlockReason()
                val title = reason?.userTitle ?: "Приложение не готово к звонкам"
                val msg = reason?.userMessage ?: "Приложение не готово к звонкам по неизвестной причине."
                val fixAction = when (reason) {
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.NOTIFICATIONS_DISABLED,
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING -> FixActionType.OPEN_NOTIFICATION_SETTINGS
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.AUTH_MISSING -> FixActionType.SHOW_LOGIN
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.BATTERY_OPTIMIZATION -> FixActionType.OPEN_BATTERY_SETTINGS
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.DEVICE_ID_MISSING,
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.FOREGROUND_START_FAILED,
                    ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.UNKNOWN,
                    null -> FixActionType.RESTART_SERVICE
                }
                ReadyUiModel(
                    iconEmoji = "🔴",
                    title = "Приложение не готово к звонкам: $title",
                    message = msg,
                    showFixButton = true,
                    fixActionType = fixAction
                )
            }
            
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
     * Безопасная проверка с обработкой ошибок (избегаем SecurityException).
     * Использует более мягкую проверку: достаточно наличия интернета, валидация не обязательна.
     */
    private fun isNetworkAvailable(): Boolean {
        return try {
            val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
                ?: return false
            
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val network = connectivityManager.activeNetwork ?: return false
                val capabilities = connectivityManager.getNetworkCapabilities(network) ?: return false
                
                // Проверяем наличие интернета (основное требование)
                val hasInternet = capabilities.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                
                // Проверяем наличие транспорта (WiFi, мобильная сеть и т.д.)
                val hasTransport = capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                        capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ||
                        capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) ||
                        capabilities.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
                
                // Сеть доступна, если есть интернет И есть транспорт
                // Валидация (NET_CAPABILITY_VALIDATED) не обязательна, т.к. может быть false даже при работающем интернете
                hasInternet && hasTransport
            } else {
                @Suppress("DEPRECATION")
                val networkInfo = connectivityManager.activeNetworkInfo
                @Suppress("DEPRECATION")
                networkInfo?.isConnected == true
            }
        } catch (e: SecurityException) {
            // На некоторых устройствах может быть SecurityException при проверке сети
            // В этом случае считаем, что сеть может быть доступна (не блокируем работу)
            // Логируем для отладки
            android.util.Log.w("AppReadinessChecker", "SecurityException при проверке сети: ${e.message}")
            true // Более мягкий подход: если не можем проверить - считаем что сеть есть
        } catch (e: Exception) {
            // Любая другая ошибка - логируем, но не блокируем работу
            android.util.Log.w("AppReadinessChecker", "Ошибка при проверке сети: ${e.message}")
            true // Более мягкий подход: если не можем проверить - считаем что сеть есть
        }
    }
}
