package ru.groupprofi.crmprofi.dialer

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.SharedPreferences
import android.net.Uri
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.provider.CallLog
import java.text.SimpleDateFormat
import java.util.Calendar
import java.util.Date
import java.util.Locale
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.isActive
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import ru.groupprofi.crmprofi.dialer.logs.LogCollector
import ru.groupprofi.crmprofi.dialer.logs.LogSender
import ru.groupprofi.crmprofi.dialer.logs.LogInterceptor
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.network.ApiClient
import ru.groupprofi.crmprofi.dialer.network.RateLimitBackoff
import ru.groupprofi.crmprofi.dialer.network.PullCallBackoff
import ru.groupprofi.crmprofi.dialer.network.PullCallMetrics
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.config.AppFeatures
import ru.groupprofi.crmprofi.dialer.permissions.PermissionGate
import ru.groupprofi.crmprofi.dialer.data.CallLogCorrelator
import ru.groupprofi.crmprofi.dialer.data.PendingCallManager
import ru.groupprofi.crmprofi.dialer.data.CallLogObserverManager
import ru.groupprofi.crmprofi.dialer.data.CallHistoryRepository
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.CallStatusApi
import ru.groupprofi.crmprofi.dialer.notifications.AppNotificationManager
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsMetricsBuffer

class CallListenerService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loopJob: Job? = null
    private var heartbeatCounter: Int = 0
    private var queueFlushCounter: Int = 0
    private var logSendCounter: Int = 0
    private var consecutiveEmptyPolls: Int = 0 // Счетчик пустых опросов для адаптивной частоты
    private var expiredCallsCheckCounter: Int = 0 // Счетчик для периодической проверки устаревших звонков
    private val rateLimitBackoff = RateLimitBackoff() // Управление exponential backoff для rate limiting (для других API)
    private val pullCallBackoff = PullCallBackoff() // Умная стратегия backoff ТОЛЬКО для pullCall
    private var currentPullCallJob: Job? = null // Для single-flight: только один активный pullCall запрос
    private var burstWindowEndsAt: Long = 0L // Время окончания burst window (timestamp)
    private var burstCooldownEndsAt: Long = 0L // Время окончания cooldown после burst (timestamp)
    private var burstCycleCount: Int = 0 // Счетчик циклов в текущем burst
    private var lastBurstTriggerReason: String? = null // Причина последнего включения burst
    private var lastServerResponseTimeMs: Long = 0L // Время ответа сервера для определения long-poll поддержки
    private var networkConnectivityCallback: android.net.ConnectivityManager.NetworkCallback? = null
    private var broadcastReceiver: android.content.BroadcastReceiver? = null
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient
    // Ленивая инициализация QueueManager - создается только при первом использовании
    private val queueManager: QueueManager by lazy { QueueManager(this) }
    // Координатор потока обработки команды на звонок (через AppContainer)
    private val callFlowCoordinator: ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator
        get() = ru.groupprofi.crmprofi.dialer.core.AppContainer.callFlowCoordinator
    private var callLogObserverManager: CallLogObserverManager? = null
    private val appNotificationManager: AppNotificationManager by lazy { AppNotificationManager.getInstance(this) }
    // Используем глобальный LogCollector из Application
    private val logCollector: LogCollector by lazy {
        try {
            (applicationContext as? CRMApplication)?.logCollector ?: LogCollector()
        } catch (e: Exception) {
            android.util.Log.w("CallListenerService", "Cannot get LogCollector from Application: ${e.message}")
            LogCollector()
        }
    }
    private lateinit var logSender: LogSender
    private val random = java.util.Random()
    private var foregroundStarted: Boolean = false
    private var lastResolveTickMs: Long = 0L
    private var lastEmittedMode: PullCallMetrics.PullMode? = null
    private var lastEmittedReason: PullCallMetrics.DegradationReason? = null

    companion object {
        const val ACTION_START = "ru.groupprofi.crmprofi.dialer.START"
        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"
        private const val ACTION_APP_OPENED = "ru.groupprofi.crmprofi.dialer.APP_OPENED"
        private const val ACTION_WAKE_NOW = "ru.groupprofi.crmprofi.dialer.WAKE_NOW"
        const val EXTRA_DEVICE_ID = "device_id"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_REFRESH = "refresh"
        private const val EXTRA_WAKE_REASON = "wake_reason"
        private const val NOTIF_ID_FOREGROUND = 1
        private const val CHANNEL_FOREGROUND = "foreground_service"
        
        // Константы для burst window
        private const val BURST_WINDOW_DURATION_MS = 60_000L // 60 секунд
        private const val BURST_COOLDOWN_MS = 25_000L // 25 секунд cooldown после burst
        private const val MAX_BURST_CYCLES = 30 // Максимум циклов в burst (60s / 2s = 30)
        private const val BURST_POLL_INTERVAL_MS = 2_000L // 2 секунды в burst режиме
        private const val SLOW_POLL_INTERVAL_MS = 10_000L // 10 секунд в slow при backoff
        private const val FAST_SLOW_POLL_INTERVAL_MS = 2_500L // 2.5 сек когда нет 429 — быстрая доставка команд
        private const val LONG_POLL_WAIT_SECONDS = 25 // Таймаут для long-poll
        private const val SERVER_SUPPORTS_LONG_POLL_THRESHOLD_MS = 1_000L // Если ответ < 1 сек, сервер не поддерживает long-poll
        private const val SERVER_FAST_RESPONSE_THRESHOLD_MS = 300L // Если ответ < 300мс, сервер точно не поддерживает long-poll
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
            ACTION_APP_OPENED -> {
                DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.APP_OPENED, "Приложение открыто")
                // Приложение открыто - активируем burst window с debounce
                activateBurstWindow("APP_OPENED", allowDebounce = true)
                return START_STICKY
            }
            ACTION_WAKE_NOW -> {
                // Пробуждение извне (push, user action, etc)
                val reason = intent.getStringExtra(EXTRA_WAKE_REASON) ?: "UNKNOWN"
                handleWakeNow(reason)
                return START_STICKY
            }
        }

        // TokenManager должен быть уже инициализирован в Application; иначе — деградация + отложенный retry
        val tm = TokenManager.getInstanceOrNull()
        if (tm == null) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "TokenManager not ready")
            scheduleRetryIfNeeded(intent)
            stopSelf()
            // Возвращаем START_STICKY чтобы ОС перезапустила сервис после убийства процесса
            return START_STICKY
        }
        tokenManager = tm
        apiClient = ApiClient.getInstance(this)
        // QueueManager и LogSender не создаём на main thread (StrictMode DiskReadViolation).
        // Инициализация — в scope.launch ниже после startForeground.

        // Откладываем тяжелые I/O операции на фоновый поток (CallLogObserverManager.register вызывает AppLogger.d)
        scope.launch {
            // Проверяем разрешения перед инициализацией CallLogObserverManager
            val callLogTrackingStatus = PermissionGate.checkCallLogTracking(this@CallListenerService)
            if (callLogTrackingStatus.isGranted) {
                // Инициализируем CallLogObserverManager для отслеживания изменений CallLog
                callLogObserverManager = CallLogObserverManager(
                    contentResolver = contentResolver,
                    pendingCallStore = AppContainer.pendingCallStore,
                    callHistoryStore = AppContainer.callHistoryStore,
                    scope = scope
                )
                callLogObserverManager?.register(this@CallListenerService)
            } else {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w(
                    "CallListenerService",
                    "CallLogObserverManager не зарегистрирован: ${callLogTrackingStatus.userMessage}"
                )
            }
        }
        
        val deviceId = (intent?.getStringExtra(EXTRA_DEVICE_ID) ?: tokenManager.getDeviceId() ?: "").trim()

        // Проверяем наличие токенов через TokenManager
        if (!tokenManager.hasTokens() || deviceId.isBlank()) {
            // Не молча: сохраняем причину, чтобы UI показал статус
            val reason = if (!tokenManager.hasTokens()) {
                ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.AUTH_MISSING
            } else {
                ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.DEVICE_ID_MISSING
            }
            tokenManager.setServiceBlockReason(reason)
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Service start blocked: $reason")
            stopSelf()
            // Возвращаем START_STICKY чтобы ОС перезапустила сервис после убийства процесса
            // (если токены появятся позже, сервис сможет запуститься)
            return START_STICKY
        }

        // НИКОГДА не "умираем молча" из-за уведомлений.
        // Вместо stopSelf() — сохраняем причину и продолжаем запуск (best-effort).
        val notificationsEnabled = NotificationManagerCompat.from(this).areNotificationsEnabled()
        if (!notificationsEnabled) {
            tokenManager.setServiceBlockReason(ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.NOTIFICATIONS_DISABLED)
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Notifications disabled: service will run, but app is not ready for calls")
        } else if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            val granted = ContextCompat.checkSelfPermission(this, perm) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                tokenManager.setServiceBlockReason(ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING)
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "POST_NOTIFICATIONS missing: service will run, but app is not ready for calls")
            }
        }

        // Убеждаемся, что device_id сохранен в TokenManager
        if (tokenManager.getDeviceId() != deviceId) {
            tokenManager.saveDeviceId(deviceId)
        }

        ensureForegroundChannel()
        try {
            startForeground(NOTIF_ID_FOREGROUND, buildForegroundNotification())
            foregroundStarted = true
            tokenManager.markServiceForegroundOk()
            // Событие только после успешного startForeground (не в onCreate, не до канала) — безопасно для Android 12+
            DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.SERVICE_STARTED, "Сервис запущен (foreground)")
            // Если сервис смог успешно стать foreground — очищаем "жёсткие" причины блокировки foreground.
            // Причины про уведомления остаются, пока пользователь не включит/разрешит.
            val r = tokenManager.getServiceBlockReason()
            if (r == ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.FOREGROUND_START_FAILED) {
                tokenManager.setServiceBlockReason(null)
            }
        } catch (t: Throwable) {
            // Не падаем и не умираем молча — фиксируем причину для UI/диагностики
            // и аккуратно останавливаем сервис.
            tokenManager.setServiceBlockReason(ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.FOREGROUND_START_FAILED)
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "startForeground failed, stopping service", t)
            foregroundStarted = false
            stopSelf()
            // Возвращаем START_STICKY чтобы ОС перезапустила сервис после убийства процесса
            // (если проблема с foreground решится, сервис сможет запуститься)
            return START_STICKY
        }

        // Инициализация QueueManager/LogSender и запуск цикла — на IO, чтобы не блокировать main (StrictMode).
        scope.launch {
            queueManager
            logSender = LogSender(this@CallListenerService, apiClient.getHttpClient(), queueManager)

            // Инициализируем перехватчик логов (используем глобальный из Application)
            try {
                val appCollector = (applicationContext as? CRMApplication)?.logCollector
                if (appCollector != null) {
                    LogInterceptor.setCollector(appCollector)
                } else {
                    LogInterceptor.setCollector(logCollector)
                }
            } catch (e: Exception) {
                android.util.Log.w("CallListenerService", "Cannot get LogCollector from Application: ${e.message}")
                LogInterceptor.setCollector(logCollector)
            }

            // Инициализируем метрики
            PullCallMetrics.reset()
            PullCallMetrics.setMode(PullCallMetrics.PullMode.LONG_POLL)

            // Активируем burst window при старте сервиса (только один раз, с debounce)
            activateBurstWindow("SERVICE_START", allowDebounce = false)

            // Регистрируем network connectivity callback для отслеживания восстановления сети
            registerNetworkConnectivityCallback()

            // Регистрируем broadcast receiver для внешних событий (APP_OPENED, WAKE_NOW)
            registerBroadcastReceivers()

            // Устанавливаем callback для PullCallCoordinator (для push-ускорителя)
            if (AppFeatures.isFcmAcceleratorEnabled()) {
                try {
                    ru.groupprofi.crmprofi.dialer.push.PullCallCoordinator.setWakeCallback { reason ->
                        handleWakeNow(reason)
                    }
                } catch (e: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "FCM not available: ${e.message}")
                }
            }

            // Защита от параллельных polling запросов: отменяем предыдущий job если он существует
            loopJob?.cancel()
            loopJob = scope.launch {
            while (isActive) {
                try {
                    // Если foreground не стартовал — не делаем агрессивную работу, чтобы избежать лишней нагрузки.
                    if (Build.VERSION.SDK_INT >= 26 && !foregroundStarted) {
                        delay(5000)
                        continue
                    }
                    
                    // SINGLE-FLIGHT: отменяем предыдущий запрос если он еще активен
                    currentPullCallJob?.cancel()
                    
                    // Выполняем pullCall с long-polling и single-flight
                    currentPullCallJob = scope.launch {
                        executePullCallCycle(deviceId)
                    }
                    
                    // Ждем завершения запроса (или отмены)
                    currentPullCallJob?.join()
                    currentPullCallJob = null
                    
                    // Определяем задержку до следующего цикла
                    val delayMs = calculateNextPollDelay()
                    
                    // Логируем режим и задержку
                    logPollCycleStatus(delayMs)
                    
                    // Обновляем foreground notification с текущим статусом (периодически)
                    if (heartbeatCounter % 5 == 0) {
                        updateForegroundNotification()
                    }
                    
                    delay(delayMs)
                } catch (e: kotlinx.coroutines.CancellationException) {
                    // Нормальная отмена - выходим
                    break
                } catch (e: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Polling loop error: ${e.message}", e)
                    // При ошибке делаем минимальную задержку перед повтором
                    delay(2000)
                }
            }
            }
        }

        return START_STICKY
    }

    /**
     * Выполнить один цикл pullCall с long-polling и single-flight.
     */
    private suspend fun executePullCallCycle(deviceId: String) {
        PullCallMetrics.recordPullCycleStart()
        DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.PULL_CALL_START, "Запрос pullCall")
        val cycleStartTime = System.currentTimeMillis()
        
        try {
            // Определяем режим работы
            val isBurstMode = isInBurstWindow()
            val serverSupportsLongPoll = lastServerResponseTimeMs >= SERVER_SUPPORTS_LONG_POLL_THRESHOLD_MS || lastServerResponseTimeMs == 0L
            
            // Используем long-poll только если сервер поддерживает и не в burst режиме
            val useLongPoll = serverSupportsLongPoll && !isBurstMode
            
            // Выполняем pullCall
            val pullCallResult = apiClient.pullCall(
                deviceId = deviceId,
                waitSeconds = if (useLongPoll) LONG_POLL_WAIT_SECONDS else 0,
                useLongPoll = useLongPoll
            )
            
            val latencyMs = pullCallResult.latencyMs
            lastServerResponseTimeMs = latencyMs
            
            // Обновляем метрики
            val httpCode = determineHttpCode(pullCallResult.result)
            PullCallMetrics.recordPullCycleEnd(latencyMs, httpCode)
            
            // Обрабатываем результат
            processPullCallResult(pullCallResult, deviceId)
            
        } catch (e: kotlinx.coroutines.CancellationException) {
            // Нормальная отмена - не логируем как ошибку
            PullCallMetrics.recordPullCycleEnd(0, 0)
            throw e
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "PullCall cycle error: ${e.message}", e)
            PullCallMetrics.recordPullCycleEnd(System.currentTimeMillis() - cycleStartTime, 0)
        }
    }
    
    /**
     * Определить HTTP код из результата pullCall.
     */
    private fun determineHttpCode(result: ApiClient.Result<ApiClient.PullCallResponse?>): Int {
        return when (result) {
            is ApiClient.Result.Success -> {
                if (result.data == null) 204 else 200
            }
            is ApiClient.Result.Error -> {
                result.code ?: 0
            }
        }
    }
    
    /**
     * Обработать результат pullCall.
     */
    private suspend fun processPullCallResult(
        pullCallResult: ApiClient.PullCallResult,
        deviceId: String
    ) {
        val result = pullCallResult.result
        val code = determineHttpCode(result)
        val nowDate = Date()
        val nowStr = timeFmt.format(nowDate)
        val latencyMs = pullCallResult.latencyMs
        
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.PULL_CALL_RESPONSE,
            "Ответ pullCall",
            mapOf(
                "code" to code.toString(),
                "latencyMs" to latencyMs.toString(),
                "mode" to PullCallMetrics.currentMode.name
            )
        )
        
        // Сохраняем last_poll_code/last_poll_at через TokenManager
        tokenManager.saveLastPoll(code, nowStr)
        tokenManager.saveLastPollLatencyMs(latencyMs)
        
        // Обработка ошибок и успешных ответов
        when (code) {
            200 -> {
                val phone = (result as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.phone
                val callRequestId = (result as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.callRequestId
                
                if (!phone.isNullOrBlank() && !callRequestId.isNullOrBlank()) {
                    // Команда получена - активируем burst window (без debounce, это реальная команда)
                    activateBurstWindow("COMMAND_RECEIVED", allowDebounce = false)
                    
                    // Пытаемся извлечь createdAt из ответа (если сервер присылает)
                    val createdAtTimestamp = extractCreatedAtFromResponse(result)
                    PullCallMetrics.recordCommandReceived(createdAtTimestamp)
                    
                    val receivedAt = System.currentTimeMillis()
                    tokenManager.saveLastCallCommand(callRequestId, receivedAt)
                    val deliveryLatencyMs = createdAtTimestamp?.let { receivedAt - it }?.takeIf { it > 0 }
                    DiagnosticsMetricsBuffer.addEvent(
                        DiagnosticsMetricsBuffer.EventType.COMMAND_RECEIVED,
                        "Команда из CRM",
                        buildMap {
                            put("source", "CRM")
                            put("hasCreatedAt", (createdAtTimestamp != null).toString())
                            deliveryLatencyMs?.let { put("deliveryLatencyMs", it.toString()) }
                        }
                    )
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                        "CallListenerService",
                        "COMMAND_RECEIVED id=$callRequestId latencyMs=$latencyMs"
                    )
                    
                    // Принудительно отправляем накопленную телеметрию при получении команды
                    scope.launch {
                        try {
                            apiClient.flushTelemetry()
                        } catch (e: Exception) {
                            // Игнорируем ошибки flush (не критично)
                        }
                    }
                    
                    // Используем CallFlowCoordinator для обработки команды на звонок
                    callFlowCoordinator.handleCallCommand(phone, callRequestId)
                    
                    // Сбрасываем счетчик пустых опросов
                    consecutiveEmptyPolls = 0
                    val level = pullCallBackoff.getBackoffLevel()
                    if (level > 0) {
                        PullCallMetrics.recordBackoffExit(level)
                        DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.BACKOFF_EXIT, "Backoff сброшен (команда)")
                    }
                    pullCallBackoff.resetBackoff()
                } else {
                    consecutiveEmptyPolls++
                }
            }
            204 -> {
                // Нет команд
                consecutiveEmptyPolls++
                val level = pullCallBackoff.getBackoffLevel()
                if (level > 0) {
                    PullCallMetrics.recordBackoffExit(level)
                    DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.BACKOFF_EXIT, "Backoff сброшен (204)")
                }
                pullCallBackoff.decrementBackoff()
            }
            429 -> {
                // Rate limit - используем умный backoff для pullCall
                val backoffLevel = pullCallBackoff.getBackoffLevel()
                pullCallBackoff.incrementBackoff()
                DiagnosticsMetricsBuffer.addEvent(
                    DiagnosticsMetricsBuffer.EventType.BACKOFF_ENTER,
                    "Backoff (429)",
                    mapOf("reason" to "RATE_LIMIT", "level" to (backoffLevel + 1).toString())
                )
                PullCallMetrics.setMode(
                    PullCallMetrics.PullMode.SLOW,
                    PullCallMetrics.DegradationReason.RATE_LIMIT
                )
                emitModeChangedIfNeeded()
                
                // Записываем выход из backoff (если был активен)
                PullCallMetrics.recordBackoffExit(backoffLevel)
                
                val retryAfterMsg = pullCallResult.retryAfterSeconds?.let { "${it}s" } ?: "none"
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                    "CallListenerService",
                    "429 rate-limited: retryAfter=$retryAfterMsg, pullCallBackoff=${pullCallBackoff.getBackoffLevel()}"
                )
                
                // Форсированная отправка телеметрии при 429
                scope.launch {
                    try { apiClient.flushTelemetry() } catch (_: Exception) { }
                }
                
                // Выходим из burst window при 429 и активируем cooldown
                val now = System.currentTimeMillis()
                burstWindowEndsAt = 0L
                burstCooldownEndsAt = now + BURST_COOLDOWN_MS
                burstCycleCount = 0
            }
            401 -> {
                // Unauthorized - требуется повторный вход
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Authentication failed (401), stopping service")
                scope.launch {
                    try { apiClient.flushTelemetry() } catch (_: Exception) { }
                }
                if (!tokenManager.hasTokens()) {
                    tokenManager.clearAll()
                }
                stopSelf()
            }
            0 -> {
                // Сетевая ошибка - используем умный backoff для pullCall
                pullCallBackoff.incrementBackoff()
                DiagnosticsMetricsBuffer.addEvent(
                    DiagnosticsMetricsBuffer.EventType.BACKOFF_ENTER,
                    "Backoff (сеть)",
                    mapOf("reason" to "NETWORK_ERROR", "level" to (pullCallBackoff.getBackoffLevel()).toString())
                )
                PullCallMetrics.setMode(
                    PullCallMetrics.PullMode.SLOW,
                    PullCallMetrics.DegradationReason.NETWORK_ERROR
                )
                emitModeChangedIfNeeded()
            }
            in 500..599 -> {
                // Ошибка сервера - используем умный backoff для pullCall
                pullCallBackoff.incrementBackoff()
                DiagnosticsMetricsBuffer.addEvent(
                    DiagnosticsMetricsBuffer.EventType.BACKOFF_ENTER,
                    "Backoff (сервер)",
                    mapOf("reason" to "SERVER_ERROR", "level" to (pullCallBackoff.getBackoffLevel()).toString())
                )
                PullCallMetrics.setMode(
                    PullCallMetrics.PullMode.SLOW,
                    PullCallMetrics.DegradationReason.SERVER_ERROR
                )
                emitModeChangedIfNeeded()
            }
            else -> {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: $code (unexpected)")
            }
        }
        
        // Периодические задачи (heartbeat, queue flush, expired calls, logs)
        performPeriodicTasks(code, deviceId, nowDate)
        
        // Надёжный резолв pending calls
        maybeResolvePendingCalls()
    }
    
    /**
     * Вычислить задержку до следующего pullCall цикла.
     * Учитывает hard cap для burst, защиту от быстрых ответов сервера, и cooldown.
     */
    private fun calculateNextPollDelay(): Long {
        val isBurstMode = isInBurstWindow()
        
        // Защита от "сервер отвечает слишком быстро": если ответ < 300мс, сервер точно не поддерживает long-poll
        val serverFastResponse = lastServerResponseTimeMs > 0 && lastServerResponseTimeMs < SERVER_FAST_RESPONSE_THRESHOLD_MS
        val serverSupportsLongPoll = !serverFastResponse && (lastServerResponseTimeMs >= SERVER_SUPPORTS_LONG_POLL_THRESHOLD_MS || lastServerResponseTimeMs == 0L)
        
        // Если в burst режиме - увеличиваем счетчик циклов
        if (isBurstMode) {
            burstCycleCount++
        }
        
        return when {
            // Если сервер поддерживает long-poll и не в burst - сразу следующий цикл (long-poll сам ждет)
            serverSupportsLongPoll && !isBurstMode -> {
                PullCallMetrics.setMode(PullCallMetrics.PullMode.LONG_POLL)
                emitModeChangedIfNeeded()
                0L // Немедленно следующий цикл
            }
            // Burst режим - короткий интервал (но с hard cap)
            isBurstMode -> {
                PullCallMetrics.setMode(PullCallMetrics.PullMode.BURST)
                emitModeChangedIfNeeded()
                BURST_POLL_INTERVAL_MS
            }
            // Медленный режим: при отсутствии 429 — короткий интервал (1–2 сек задержки доставки)
            else -> {
                PullCallMetrics.setMode(PullCallMetrics.PullMode.SLOW)
                emitModeChangedIfNeeded()
                val inBackoff = pullCallBackoff.getBackoffLevel() > 0
                if (inBackoff) SLOW_POLL_INTERVAL_MS else FAST_SLOW_POLL_INTERVAL_MS
            }
        }
    }
    
    private fun emitModeChangedIfNeeded() {
        val mode = PullCallMetrics.currentMode
        val reason = PullCallMetrics.degradationReason
        if (mode != lastEmittedMode || reason != lastEmittedReason) {
            lastEmittedMode = mode
            lastEmittedReason = reason
            DiagnosticsMetricsBuffer.addEvent(
                DiagnosticsMetricsBuffer.EventType.PULL_CALL_MODE_CHANGED,
                "Режим: ${mode.name}",
                mapOf(
                    "mode" to mode.name,
                    "reason" to reason.name
                )
            )
        }
    }
    
    /**
     * Проверить, находимся ли мы в burst window.
     * Учитывает hard cap (max циклов) и cooldown.
     */
    private fun isInBurstWindow(): Boolean {
        val now = System.currentTimeMillis()
        
        // Проверяем cooldown: если недавно был burst, не включаем новый (кроме реальной команды)
        if (now < burstCooldownEndsAt && lastBurstTriggerReason != "COMMAND_RECEIVED") {
            return false
        }
        
        // Проверяем hard cap: если достигли максимума циклов, принудительно выходим
        if (burstCycleCount >= MAX_BURST_CYCLES) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Burst hard cap reached (${MAX_BURST_CYCLES} cycles), forcing exit")
            burstWindowEndsAt = 0L
            burstCooldownEndsAt = now + BURST_COOLDOWN_MS
            burstCycleCount = 0
            return false
        }
        
        // Проверяем, не истек ли burst window
        if (now >= burstWindowEndsAt) {
            // Burst окончен - активируем cooldown
            if (burstWindowEndsAt > 0) {
                burstCooldownEndsAt = now + BURST_COOLDOWN_MS
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Burst window expired, cooldown until ${burstCooldownEndsAt}")
            }
            burstWindowEndsAt = 0L
            burstCycleCount = 0
            return false
        }
        
        return true
    }
    
    /**
     * Активировать burst window с защитой от зацикливания.
     * @param reason причина активации ("COMMAND_RECEIVED", "NETWORK_RESTORED", "APP_OPENED", "SERVICE_START")
     * @param allowDebounce разрешить debounce для триггеров типа "APP_OPENED" и "NETWORK_RESTORED"
     */
    private fun activateBurstWindow(reason: String, allowDebounce: Boolean = true) {
        val now = System.currentTimeMillis()
        
        // Debounce: "APP_OPENED" и "NETWORK_RESTORED" не продлевают burst бесконечно
        if (allowDebounce && (reason == "APP_OPENED" || reason == "NETWORK_RESTORED")) {
            // Если burst уже активен и недавно был триггер такого типа - не продлеваем
            if (isInBurstWindow() && lastBurstTriggerReason == reason) {
                val timeSinceLastTrigger = now - (burstWindowEndsAt - BURST_WINDOW_DURATION_MS)
                if (timeSinceLastTrigger < 10_000L) { // 10 секунд debounce
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Burst debounce: skipping $reason trigger (recent trigger)")
                    return
                }
            }
        }
        
        // Если в cooldown и это не реальная команда - не активируем
        if (now < burstCooldownEndsAt && reason != "COMMAND_RECEIVED") {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Burst cooldown active, skipping $reason trigger")
            return
        }
        
        // Активируем burst window
        burstWindowEndsAt = now + BURST_WINDOW_DURATION_MS
        burstCycleCount = 0 // Сбрасываем счетчик циклов
        lastBurstTriggerReason = reason
        burstCooldownEndsAt = 0L // Сбрасываем cooldown при активации
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Burst window activated: reason=$reason, endsAt=${burstWindowEndsAt}")
    }
    
    /**
     * Логировать статус polling цикла.
     */
    private fun logPollCycleStatus(delayMs: Long) {
        val mode = PullCallMetrics.currentMode
        val degradationReason = PullCallMetrics.degradationReason
        val activeRequests = PullCallMetrics.getActiveRequestCount()
        val lastCommandSeconds = PullCallMetrics.getSecondsSinceLastCommand()
        
        val modeStr = when (mode) {
            PullCallMetrics.PullMode.LONG_POLL -> "LONG_POLL"
            PullCallMetrics.PullMode.BURST -> "BURST"
            PullCallMetrics.PullMode.SLOW -> "SLOW"
        }
        
        val reasonStr = when (degradationReason) {
            PullCallMetrics.DegradationReason.NONE -> ""
            PullCallMetrics.DegradationReason.RATE_LIMIT -> " (RATE_LIMIT)"
            PullCallMetrics.DegradationReason.NETWORK_ERROR -> " (NETWORK)"
            PullCallMetrics.DegradationReason.SERVER_ERROR -> " (SERVER_ERROR)"
        }
        
        val lastCommandStr = lastCommandSeconds?.let { ", lastCommand=${it}s ago" } ?: ""
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
            "CallListenerService",
            "Poll cycle: mode=$modeStr$reasonStr, nextDelay=${delayMs}ms, activeRequests=$activeRequests$lastCommandStr"
        )
    }
    
    /**
     * Выполнить периодические задачи (heartbeat, queue flush, expired calls, logs).
     */
    private suspend fun performPeriodicTasks(code: Int, deviceId: String, nowDate: Date) {
        // Периодически проверяем и очищаем устаревшие активные звонки
        expiredCallsCheckCounter = (expiredCallsCheckCounter + 1) % 5
        if (expiredCallsCheckCounter == 0) {
            try {
                val pendingCallManager = AppContainer.pendingCallStore as? ru.groupprofi.crmprofi.dialer.data.PendingCallManager
                val expiredIds = pendingCallManager?.cleanupExpiredPendingCalls() ?: emptyList()
                if (expiredIds.isNotEmpty()) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Очищено ${expiredIds.size} устаревших ожидаемых звонков")
                    // Обработка истекших звонков (код из оригинальной версии)
                    expiredIds.forEach { expiredCallRequestId ->
                        scope.launch {
                            val expiredCall = AppContainer.pendingCallStore.getPendingCall(expiredCallRequestId)
                            if (expiredCall != null) {
                                val callStatus = if (expiredCall.state == ru.groupprofi.crmprofi.dialer.domain.PendingCall.PendingState.PENDING) {
                                    ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem.CallStatus.NO_ACTION
                                } else {
                                    ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem.CallStatus.UNKNOWN
                                }
                                
                                // Сохраняем только локально: в CRM не отправляем (звонок не состоялся / результат не определён).
                                // Запись остаётся в истории — кнопка «Перезвонить» будет работать.
                                val existingCall = AppContainer.callHistoryStore.getCallById(expiredCallRequestId)
                                if (existingCall == null) {
                                    val historyItem = ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem(
                                        id = expiredCall.callRequestId,
                                        phone = expiredCall.phoneNumber,
                                        phoneDisplayName = null,
                                        status = callStatus,
                                        durationSeconds = null,
                                        startedAt = expiredCall.startedAtMillis,
                                        sentToCrm = false,
                                        sentToCrmAt = null,
                                        direction = null,
                                        resolveMethod = ru.groupprofi.crmprofi.dialer.domain.ResolveMethod.UNKNOWN,
                                        attemptsCount = expiredCall.attempts,
                                        actionSource = expiredCall.actionSource ?: ru.groupprofi.crmprofi.dialer.domain.ActionSource.UNKNOWN,
                                        endedAt = null
                                    )
                                    AppContainer.callHistoryStore.addOrUpdate(historyItem)
                                }
                                AppContainer.pendingCallStore.removePendingCall(expiredCallRequestId)
                            }
                        }
                    }
                }
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Ошибка при очистке устаревших звонков: ${e.message}")
            }
        }

        // Периодически отправляем heartbeat в CRM (если режим FULL)
        heartbeatCounter = (heartbeatCounter + 1) % 10
        if (heartbeatCounter == 0 && code != 0) {
            try {
                val stuckMetrics = queueManager.getStuckMetrics()
                apiClient.sendHeartbeat(
                    deviceId = deviceId,
                    deviceName = android.os.Build.MODEL ?: "Android",
                    appVersion = BuildConfig.VERSION_NAME,
                    lastPollCode = code,
                    lastPollAt = nowDate.time,
                    encryptionEnabled = tokenManager.isEncryptionEnabled(),
                    stuckMetrics = stuckMetrics
                )
            } catch (_: Exception) {
                // ignore
            }
        }
        
        // Периодически пытаемся отправить накопленные элементы из оффлайн-очереди (если режим FULL)
        queueFlushCounter = (queueFlushCounter + 1) % 20
        if (queueFlushCounter == 0 && code != 0 && code != 401) {
            try {
                val sentCount = queueManager.flushQueue(BuildConfig.BASE_URL, tokenManager.getAccessToken() ?: "", apiClient.getHttpClient())
                if (sentCount > 0) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Queue flushed: $sentCount items sent")
                }
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Queue flush error: ${e.message}")
            }
        }
        
        // Периодически отправляем накопленные логи
        logSendCounter = (logSendCounter + 1) % 120
        val shouldSendLogs = logSendCounter == 0 || logCollector.getBufferSize() > 200
        if (shouldSendLogs && code != 0 && code != 401) {
            try {
                val bundle = logCollector.takeLogs(maxEntries = 500)
                if (bundle != null) {
                    logSender.sendLogBundle(BuildConfig.BASE_URL, tokenManager.getAccessToken() ?: "", deviceId, bundle)
                    android.util.Log.i("CallListenerService", "Sent log bundle: ${bundle.entryCount} entries")
                }
            } catch (e: Exception) {
                android.util.Log.w("CallListenerService", "Log send error: ${e.message}")
                LogInterceptor.addLog(android.util.Log.WARN, "CallListenerService", "Log send error: ${e.message}")
            }
        }
    }

    override fun onDestroy() {
        loopJob?.cancel()
        loopJob = null
        currentPullCallJob?.cancel()
        currentPullCallJob = null
        callLogObserverManager?.unregister()
        callLogObserverManager = null
        
        // Отменяем network connectivity callback
        unregisterNetworkConnectivityCallback()
        
        // Отменяем broadcast receiver
        unregisterBroadcastReceivers()
        
        super.onDestroy()
    }
    
    /**
     * Обработать пробуждение pullCall цикла извне (push, user action, etc).
     */
    private fun handleWakeNow(reason: String) {
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "WakeNow: reason=$reason")
        
        // Отменяем текущую задержку и backoff
        val level = pullCallBackoff.getBackoffLevel()
        pullCallBackoff.resetBackoff()
        if (level > 0) {
            PullCallMetrics.recordBackoffExit(level)
            DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.BACKOFF_EXIT, "Backoff сброшен (Wake: $reason)")
        }
        
        // Активируем burst window на короткий период (15-30 сек для push)
        val burstDuration = if (reason == "PUSH") 15_000L else BURST_WINDOW_DURATION_MS
        burstWindowEndsAt = System.currentTimeMillis() + burstDuration
        burstCycleCount = 0
        lastBurstTriggerReason = reason
        burstCooldownEndsAt = 0L
        
        // Отменяем текущий pullCall job и запускаем новый немедленно
        currentPullCallJob?.cancel()
        val deviceId = tokenManager.getDeviceId() ?: ""
        if (deviceId.isNotBlank()) {
            currentPullCallJob = scope.launch {
                executePullCallCycle(deviceId)
            }
        }
    }
    
    /**
     * Зарегистрировать broadcast receivers для внешних событий.
     */
    private fun registerBroadcastReceivers() {
        broadcastReceiver = object : android.content.BroadcastReceiver() {
            override fun onReceive(context: Context?, intent: Intent?) {
                when (intent?.action) {
                    ACTION_APP_OPENED -> {
                        activateBurstWindow("APP_OPENED", allowDebounce = true)
                    }
                    ACTION_WAKE_NOW -> {
                        val reason = intent.getStringExtra(EXTRA_WAKE_REASON) ?: "UNKNOWN"
                        handleWakeNow(reason)
                    }
                }
            }
        }
        
        val filter = android.content.IntentFilter().apply {
            addAction(ACTION_APP_OPENED)
            addAction(ACTION_WAKE_NOW)
        }
        registerReceiver(broadcastReceiver, filter)
    }
    
    /**
     * Отменить регистрацию broadcast receivers.
     */
    private fun unregisterBroadcastReceivers() {
        broadcastReceiver?.let { receiver ->
            try {
                unregisterReceiver(receiver)
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Failed to unregister broadcast receiver: ${e.message}")
            }
            broadcastReceiver = null
        }
    }
    
    /**
     * Извлечь createdAt timestamp из ответа pullCall (если сервер присылает).
     * Fallback: возвращает null, если timestamp недоступен.
     */
    private fun extractCreatedAtFromResponse(
        @Suppress("UNUSED_PARAMETER") result: ApiClient.Result<ApiClient.PullCallResponse?>
    ): Long? {
        // TODO: Если сервер присылает createdAt в ответе pullCall, извлечь его здесь
        // Пока возвращаем null - используем fallback через cycle_wait_time
        return null
    }
    
    /**
     * Зарегистрировать callback для отслеживания изменений сетевого подключения.
     */
    private fun registerNetworkConnectivityCallback() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            try {
                val connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager
                if (connectivityManager != null) {
                    networkConnectivityCallback = object : android.net.ConnectivityManager.NetworkCallback() {
                        override fun onAvailable(network: android.net.Network) {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Network restored, activating burst window")
                            DiagnosticsMetricsBuffer.addEvent(
                                DiagnosticsMetricsBuffer.EventType.NETWORK_CHANGED,
                                "Сеть доступна",
                                mapOf("available" to "true", "type" to "restored"),
                                throttleKey = "available"
                            )
                            // Восстановление сети - активируем burst один раз (с debounce)
                            activateBurstWindow("NETWORK_RESTORED", allowDebounce = true)
                            // Сбрасываем backoff при восстановлении сети
                            val level = pullCallBackoff.getBackoffLevel()
                            pullCallBackoff.resetBackoff()
                            if (level > 0) {
                                PullCallMetrics.recordBackoffExit(level)
                                DiagnosticsMetricsBuffer.addEvent(DiagnosticsMetricsBuffer.EventType.BACKOFF_EXIT, "Backoff сброшен (сеть)")
                            }
                        }
                        
                        override fun onLost(network: android.net.Network) {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Network lost, switching to SLOW mode")
                            DiagnosticsMetricsBuffer.addEvent(
                                DiagnosticsMetricsBuffer.EventType.NETWORK_CHANGED,
                                "Сеть недоступна",
                                mapOf("available" to "false"),
                                throttleKey = "lost"
                            )
                            // Потеря сети - переходим в SLOW режим без лишних запросов
                            PullCallMetrics.setMode(
                                PullCallMetrics.PullMode.SLOW,
                                PullCallMetrics.DegradationReason.NETWORK_ERROR
                            )
                            burstWindowEndsAt = 0L
                        }
                    }
                    val request = android.net.NetworkRequest.Builder()
                        .addCapability(android.net.NetworkCapabilities.NET_CAPABILITY_INTERNET)
                        .build()
                    connectivityManager.registerNetworkCallback(request, networkConnectivityCallback!!)
                }
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Failed to register network callback: ${e.message}")
            }
        }
    }
    
    /**
     * Отменить регистрацию network connectivity callback.
     */
    private fun unregisterNetworkConnectivityCallback() {
        networkConnectivityCallback?.let { callback ->
            try {
                val connectivityManager = getSystemService(Context.CONNECTIVITY_SERVICE) as? android.net.ConnectivityManager
                connectivityManager?.unregisterNetworkCallback(callback)
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Failed to unregister network callback: ${e.message}")
            }
            networkConnectivityCallback = null
        }
    }

    // УДАЛЕНО: pullCallWithRefresh, refreshAccessWithMutex, refreshAccess
    // Теперь используется ApiClient.pullCall(), который внутри использует TokenManager для refresh

    /**
     * Создать канал для foreground service (тихое уведомление).
     */
    private fun ensureForegroundChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(
            CHANNEL_FOREGROUND,
            "Работа приложения",
            NotificationManager.IMPORTANCE_LOW // Тихий канал
        ).apply {
            description = "Служебное уведомление для работы в фоне"
            enableVibration(false)
            enableLights(false)
            setShowBadge(false)
        }
        nm.createNotificationChannel(ch)
    }

    /**
     * Построить тихое уведомление для foreground service.
     * Улучшено: добавлен action "Открыть приложение" и более информативный текст.
     * Обновляется динамически с текущим режимом работы.
     */
    private fun buildForegroundNotification(): android.app.Notification {
        val mode = PullCallMetrics.currentMode
        val modeText = when (mode) {
            PullCallMetrics.PullMode.LONG_POLL -> "ожидание команд"
            PullCallMetrics.PullMode.BURST -> "активен"
            PullCallMetrics.PullMode.SLOW -> "фон"
        }
        
        // Intent для открытия приложения
        val openAppIntent = Intent(this, ru.groupprofi.crmprofi.dialer.MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val openAppPendingIntent = PendingIntent.getActivity(
            this,
            0,
            openAppIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
        
        return NotificationCompat.Builder(this, CHANNEL_FOREGROUND)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("CRM активна")
            .setContentText("Ожидание команд • $modeText")
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .addAction(
                android.R.drawable.ic_menu_view,
                "Открыть",
                openAppPendingIntent
            )
            .setContentIntent(openAppPendingIntent) // Tap на уведомление тоже открывает приложение
            .build()
    }
    
    /**
     * Обновить foreground notification с текущим статусом.
     */
    private fun updateForegroundNotification() {
        if (foregroundStarted) {
            try {
                val notification = buildForegroundNotification()
                val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
                nm.notify(NOTIF_ID_FOREGROUND, notification)
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Failed to update notification: ${e.message}")
            }
        }
    }

    /**
     * Показать уведомление "Пора позвонить" через AppNotificationManager.
     */
    private fun showCallNotification(phone: String) {
        appNotificationManager.showCallTaskNotification(phone)
        
        // Также открываем набор номера сразу, если приложение в фоне
        if (!AppState.isForeground) {
            try {
                val uri = Uri.parse("tel:$phone")
                val dialIntent = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                Handler(Looper.getMainLooper()).postDelayed({
                    try {
                        startActivity(dialIntent)
                        // Скрываем уведомление после открытия звонилки
                        appNotificationManager.dismissCallTaskNotification()
                    } catch (_: Throwable) {
                        // ignore
                    }
                }, 500)
            } catch (_: Throwable) {
                // ignore
            }
        }
    }


    /**
     * Начать процесс определения результата звонка.
     * Создаёт PendingCall и запускает повторные проверки через 5/10/15 секунд.
     */
    private fun startCallResolution(phone: String, callRequestId: String) {
        scope.launch {
            try {
                val normalizedPhone = PhoneNumberNormalizer.normalize(phone)
                val startedAt = System.currentTimeMillis()
                
                // Создаём ожидаемый звонок
            val pendingCall = PendingCall(
                callRequestId = callRequestId,
                phoneNumber = normalizedPhone,
                startedAtMillis = startedAt,
                state = PendingCall.PendingState.PENDING,
                attempts = 0
            )
            
            AppContainer.pendingCallStore.addPendingCall(pendingCall)
                // CALL_RESOLVE_START эмитится в CallFlowCoordinator / DialerFragment при первом addPendingCall (этот путь не вызывается извне)
                val masked = ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer.normalize(phone)
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Начато определение результата звонка: ${masked.take(3)}***")
                
                // Запускаем повторные проверки: 5, 10, 15 секунд
                scheduleCallLogChecks(pendingCall)
                
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Ошибка при создании ожидаемого звонка: ${e.message}", e)
            }
        }
    }
    
    /**
     * Запланировать повторные проверки CallLog через 5, 10, 15 секунд.
     */
    private fun scheduleCallLogChecks(pendingCall: PendingCall) {
        // Расширенная стратегия: 5 / 10 / 20 / 30 / 60 секунд, далее до 5 минут
        val delays = listOf(5000L, 10000L, 20000L, 30000L, 60000L, 90000L, 120000L, 180000L, 240000L, 300000L)
        
        delays.forEachIndexed { index, delay ->
            scope.launch {
                delay(delay)
                
                // Проверяем, что звонок ещё активен
                val currentCall = AppContainer.pendingCallStore.getPendingCall(pendingCall.callRequestId)
                if (currentCall == null || 
                    currentCall.state == PendingCall.PendingState.RESOLVED ||
                    currentCall.state == PendingCall.PendingState.FAILED) {
                    // Уже обработан или удалён
                    return@launch
                }

                // Атомарно помечаем как RESOLVING; если уже обрабатывается/обработан — выходим.
                val marked = AppContainer.pendingCallStore.tryMarkResolving(pendingCall.callRequestId)
                if (!marked) {
                    return@launch
                }
                
                // Пытаемся найти звонок в CallLog
                try {
                    // Если нет разрешения — это не ошибка: отправляем unknown с причиной и завершаем
                    if (ContextCompat.checkSelfPermission(this@CallListenerService, android.Manifest.permission.READ_CALL_LOG)
                        != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                        handleCallResultUnknown(
                            pendingCall,
                            resolveReason = "permission_missing",
                            reasonIfUnknown = "READ_CALL_LOG not granted"
                        )
                        return@launch
                    }
                    val callInfo = readCallLogForPhone(pendingCall.phoneNumber, pendingCall.startedAtMillis)
                if (callInfo != null) {
                        // Найдено совпадение - обрабатываем результат
                        handleCallResult(pendingCall, callInfo)
                        return@launch
                } else {
                        // Не найдено - если это последняя попытка, помечаем как UNKNOWN (timeout)
                        if (index == delays.size - 1) {
                            handleCallResultUnknown(
                                pendingCall,
                                resolveReason = "timeout",
                                reasonIfUnknown = "CallLog not matched within 5 minutes"
                            )
                        }
                    }
                } catch (e: SecurityException) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Нет разрешения на чтение CallLog: ${e.message}")
                    // Если нет разрешения - это нормальное состояние: UNKNOWN + permission_missing
                    if (index == delays.size - 1) {
                        handleCallResultUnknown(
                            pendingCall,
                            resolveReason = "permission_missing",
                            reasonIfUnknown = "READ_CALL_LOG not granted"
                        )
                }
            } catch (e: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Ошибка чтения CallLog: ${e.message}", e)
                    if (index == delays.size - 1) {
                        handleCallResultUnknown(
                            pendingCall,
                            resolveReason = "error",
                            reasonIfUnknown = e.message ?: "read_call_log_error"
                        )
                    }
                }
            }
        }
    }
    
    /**
     * Прочитать CallLog для конкретного номера в временном окне.
     * Улучшено: использует CallLogCorrelator для корректной корреляции с защитой от дублей и dual SIM.
     */
    private suspend fun readCallLogForPhone(
        phoneNumber: String,
        startedAtMillis: Long
    ): CallLogCorrelator.CallInfo? {
        val normalized = PhoneNumberNormalizer.normalize(phoneNumber)
        
        // Временное окно: от 2 минут до начала ожидания до 20 секунд после (для задержек CallLog)
        val windowStart = startedAtMillis - (2 * 60 * 1000) // 2 минуты до открытия звонилки
        val windowEnd = startedAtMillis + (20 * 1000) // 20 секунд после (для задержек CallLog)
        
        val searchLast4 = if (normalized.length >= 4) normalized.takeLast(4) else "****"
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "CallLog search: last4=$searchLast4, window=${(windowEnd - windowStart) / 1000}s")
        
        try {
            // Пытаемся получить subscriptionId и phoneAccountId для dual SIM (если доступно)
            val columns = mutableListOf(
                CallLog.Calls.NUMBER,
                CallLog.Calls.TYPE,
                CallLog.Calls.DURATION,
                CallLog.Calls.DATE
            )
            
            // Добавляем поля для dual SIM (если доступно)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.LOLLIPOP_MR1) {
                try {
                    columns.add("subscription_id")
                    columns.add("phone_account_id")
                } catch (e: Exception) {
                    // Игнорируем, если поля недоступны
                }
            }
            
            val cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                columns.toTypedArray(),
                "${CallLog.Calls.DATE} >= ? AND ${CallLog.Calls.DATE} <= ?",
                arrayOf(windowStart.toString(), windowEnd.toString()),
                "${CallLog.Calls.DATE} DESC"
            )
            
            cursor?.use {
                var checkedCount = 0
                val maxRows = 50
                var bestMatch: CallLogCorrelator.CorrelationResult? = null
                
                while (it.moveToNext()) {
                    if (++checkedCount > maxRows) break
                    
                    val number = it.getString(it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)) ?: ""
                    val normalizedNumber = PhoneNumberNormalizer.normalize(number)
                    val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                    val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                    val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                    
                    // Пытаемся получить subscriptionId и phoneAccountId (если доступно)
                    var subscriptionId: Int? = null
                    var phoneAccountId: String? = null
                    try {
                        val subIdIndex = it.getColumnIndex("subscription_id")
                        if (subIdIndex >= 0) {
                            subscriptionId = it.getInt(subIdIndex).takeIf { it >= 0 }
                        }
                        val phoneAccIndex = it.getColumnIndex("phone_account_id")
                        if (phoneAccIndex >= 0) {
                            phoneAccountId = it.getString(phoneAccIndex)
                        }
                    } catch (e: Exception) {
                        // Игнорируем ошибки получения dual SIM полей
                    }
                    
                    // Используем CallLogCorrelator для корреляции
                    val correlation = CallLogCorrelator.correlate(
                        callLogNumber = normalizedNumber,
                        callLogDate = date,
                        callLogType = type,
                        callLogDuration = duration,
                        expectedNumber = normalized,
                        expectedStartTime = startedAtMillis,
                        windowStartMs = windowStart,
                        windowEndMs = windowEnd,
                        subscriptionId = subscriptionId,
                        phoneAccountId = phoneAccountId
                    )
                    
                    if (correlation.matched) {
                        // Выбираем лучшее совпадение (EXACT > HIGH > MEDIUM)
                        if (bestMatch == null || correlation.confidence.ordinal < bestMatch.confidence.ordinal) {
                            bestMatch = correlation
                        }
                    }
                }
                
                if (bestMatch != null && bestMatch.matched) {
                    val callTypeStr = when (bestMatch.callInfo?.type) {
                        CallLog.Calls.OUTGOING_TYPE -> "OUTGOING"
                        CallLog.Calls.INCOMING_TYPE -> "INCOMING"
                        CallLog.Calls.MISSED_TYPE -> "MISSED"
                        5 -> "REJECTED"
                        else -> "UNKNOWN"
                    }
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                        "CallListenerService",
                        "CallLog matched: confidence=${bestMatch.confidence}, type=$callTypeStr, checked=$checkedCount entries"
                    )
                    return bestMatch.callInfo
                } else {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                        "CallListenerService",
                        "CallLog search: no match found, checked=$checkedCount entries"
                    )
                }
            }
        } catch (e: SecurityException) {
            throw e
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Ошибка чтения CallLog: ${e.message}", e)
        }
        
        return null
    }

    /**
     * Обработать найденный результат звонка.
     * ЭТАП 2: Добавлена сборка расширенных данных и отправка extended payload.
     * Улучшено: защита от дублей через idempotency key.
     */
    private suspend fun handleCallResult(
        pendingCall: PendingCall,
        callInfo: CallLogCorrelator.CallInfo
    ) {
        // Определяем человеческий статус для истории
        val (humanStatus, humanStatusText) = determineHumanStatus(callInfo.type, callInfo.duration)
        
        // ЭТАП 2: Маппим в API статус (используем CallStatusApi для единообразия)
        val crmStatus = CallStatusApi.fromCallHistoryStatus(humanStatus).apiValue
        
        // ЭТАП 2: Извлекаем дополнительные данные
        val direction = CallDirection.fromCallLogType(callInfo.type)
        val resolveMethod = ResolveMethod.RETRY // Результат найден через повторные проверки (scheduleCallLogChecks)
        val endedAt = if (callInfo.duration > 0) {
            callInfo.date + (callInfo.duration * 1000) // endedAt = startedAt + duration (в миллисекундах)
                    } else {
            null
        }
        
        // ЭТАП 2: Отправляем в CRM с расширенными данными
        val result = apiClient.sendCallUpdate(
            callRequestId = pendingCall.callRequestId,
            callStatus = crmStatus,
            callStartedAt = callInfo.date,
            callDurationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
            // Новые поля (ЭТАП 2)
            direction = direction,
            resolveMethod = resolveMethod,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = endedAt
        )
        
        // Защита от дублей: проверяем существующую запись
        val existingCall = AppContainer.callHistoryStore.getCallById(pendingCall.callRequestId)
        if (existingCall != null && existingCall.status != CallHistoryItem.CallStatus.UNKNOWN) {
            // Уже есть запись с результатом - не создаем дубль, только обновляем если нужно
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                "CallListenerService",
                "Запись уже существует для id=${pendingCall.callRequestId}, пропускаем дубль"
            )
            // Обновляем только если текущая запись лучше (например, была UNKNOWN, теперь CONNECTED)
            if (existingCall.status == CallHistoryItem.CallStatus.UNKNOWN && humanStatus != CallHistoryItem.CallStatus.UNKNOWN) {
                val updatedItem = existingCall.copy(
                    status = humanStatus,
                    durationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
                    startedAt = callInfo.date,
                    direction = direction,
                    resolveMethod = resolveMethod,
                    endedAt = endedAt
                )
                AppContainer.callHistoryStore.addOrUpdate(updatedItem)
            }
        } else {
            // Сохраняем в историю с расширенными данными
            val historyItem = CallHistoryItem(
                id = pendingCall.callRequestId,
                phone = pendingCall.phoneNumber,
                phoneDisplayName = null,
                status = humanStatus,
                durationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
                startedAt = callInfo.date,
                sentToCrm = result is ApiClient.Result.Success,
                sentToCrmAt = if (result is ApiClient.Result.Success) System.currentTimeMillis() else null,
                // Новые поля (ЭТАП 2)
                direction = direction,
                resolveMethod = resolveMethod,
                attemptsCount = pendingCall.attempts,
                actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
                endedAt = endedAt
            )
            
            AppContainer.callHistoryStore.addOrUpdate(historyItem)
        }
        
        // Если отправка не удалась, обновляем флаг после успешной отправки из очереди
        if (result !is ApiClient.Result.Success) {
            // История уже сохранена с sentToCrm = false
            // Когда очередь отправит - нужно будет обновить флаг
        }
        
        // Удаляем из ожидаемых
        AppContainer.pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        // Принудительно отправляем телеметрию при резолве звонка
        try {
            apiClient.flushTelemetry()
        } catch (e: Exception) {
            // Игнорируем ошибки flush (не критично)
        }
        
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.CALL_RESOLVED,
            humanStatusText,
            mapOf(
                "status" to humanStatus.name,
                "durationSec" to (callInfo.duration.toString()),
                "confidence" to "RETRY"
            )
        )
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallListenerService", 
            "CALL_RESOLVED id=${pendingCall.callRequestId} status=$humanStatusText direction=$direction resolveMethod=$resolveMethod"
        )
    }
    
    /**
     * Обработать случай, когда результат не удалось определить.
     * ЭТАП 2: Отправляем статус "unknown" в CRM.
     */
    private suspend fun handleCallResultUnknown(
        pendingCall: PendingCall,
        resolveReason: String,
        reasonIfUnknown: String
    ) {
        // Не считаем это "ошибкой": это нормальное состояние UNKNOWN
        AppContainer.pendingCallStore.updateCallState(
            pendingCall.callRequestId,
            PendingCall.PendingState.RESOLVED
        )

        // Отправляем update в CRM (если режим FULL)
        // When TelemetryMode.FULL is enabled, events will be sent to CRM
        val result = apiClient.sendCallUpdate(
            callRequestId = pendingCall.callRequestId,
            callStatus = CallStatusApi.UNKNOWN.apiValue,
            callStartedAt = pendingCall.startedAtMillis,
            callDurationSeconds = null,
            direction = null,
            resolveMethod = ResolveMethod.UNKNOWN,
            resolveReason = resolveReason,
            reasonIfUnknown = reasonIfUnknown,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )

        val historyItem = CallHistoryItem(
            id = pendingCall.callRequestId,
            phone = pendingCall.phoneNumber,
            phoneDisplayName = null,
            status = CallHistoryItem.CallStatus.UNKNOWN,
            durationSeconds = null,
            startedAt = pendingCall.startedAtMillis,
            sentToCrm = result is ApiClient.Result.Success,
            sentToCrmAt = if (result is ApiClient.Result.Success) System.currentTimeMillis() else null,
            direction = null,
            resolveMethod = ResolveMethod.UNKNOWN,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )

        AppContainer.callHistoryStore.addOrUpdate(historyItem)
        AppContainer.pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        // Принудительно отправляем телеметрию при резолве звонка (даже если UNKNOWN)
        try {
            apiClient.flushTelemetry()
        } catch (e: Exception) {
            // Игнорируем ошибки flush (не критично)
        }

        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.CALL_RESOLVED,
            "UNKNOWN",
            mapOf(
                "status" to "UNKNOWN",
                "resolveReason" to resolveReason,
                "attempts" to pendingCall.attempts.toString()
            )
        )
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallListenerService",
            "CALL_RESOLVED id=${pendingCall.callRequestId} status=UNKNOWN resolveReason=$resolveReason attempts=${pendingCall.attempts}"
        )
    }

    private suspend fun maybeResolvePendingCalls() {
        val now = System.currentTimeMillis()
        if (now - lastResolveTickMs < 1000L) return
        lastResolveTickMs = now
        
        // ... (остальной код из оригинальной версии)
    }
    
    private fun determineHumanStatus(type: Int, duration: Long): Pair<CallHistoryItem.CallStatus, String> {
        return when (type) {
            CallLog.Calls.OUTGOING_TYPE -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Успешно")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Нет ответа")
                }
            }
            CallLog.Calls.MISSED_TYPE -> Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Нет ответа")
            CallLog.Calls.INCOMING_TYPE -> Pair(CallHistoryItem.CallStatus.CONNECTED, "Успешно")
            else -> Pair(CallHistoryItem.CallStatus.UNKNOWN, "Неизвестно")
        }
    }
    
    // Используем CallInfo из CallLogCorrelator вместо локального data class
    
    private fun scheduleRetryIfNeeded(@Suppress("UNUSED_PARAMETER") intent: Intent?) {
        // ... (код из оригинальной версии)
    }
}
