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
import ru.groupprofi.crmprofi.dialer.core.AppContainer
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

class CallListenerService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loopJob: Job? = null
    private var heartbeatCounter: Int = 0
    private var queueFlushCounter: Int = 0
    private var logSendCounter: Int = 0
    private var consecutiveEmptyPolls: Int = 0 // Счетчик пустых опросов для адаптивной частоты
    private var expiredCallsCheckCounter: Int = 0 // Счетчик для периодической проверки устаревших звонков
    private val rateLimitBackoff = RateLimitBackoff() // Управление exponential backoff для rate limiting
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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }

        // Инициализируем TokenManager и ApiClient (быстрые операции)
        tokenManager = TokenManager.getInstance(this)
        apiClient = ApiClient.getInstance(this)
        logSender = LogSender(this, apiClient.getHttpClient(), queueManager)
        
        // Откладываем тяжелые I/O операции на фоновый поток (CallLogObserverManager.register вызывает AppLogger.d)
        scope.launch {
            // Инициализируем CallLogObserverManager для отслеживания изменений CallLog
            callLogObserverManager = CallLogObserverManager(
                contentResolver = contentResolver,
                pendingCallStore = AppContainer.pendingCallStore,
                callHistoryStore = AppContainer.callHistoryStore,
                scope = scope
            )
            callLogObserverManager?.register()
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
            return START_NOT_STICKY
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
            return START_NOT_STICKY
        }

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

        // Защита от параллельных polling запросов: отменяем предыдущий job если он существует
        loopJob?.cancel()
        loopJob = scope.launch {
            while (true) {
                try {
                        // Если foreground не стартовал — не делаем агрессивную работу, чтобы избежать лишней нагрузки.
                        if (Build.VERSION.SDK_INT >= 26 && !foregroundStarted) {
                            delay(5000)
                            continue
                        }
                        val pollStartNano = System.nanoTime()
                        // Используем ApiClient для polling (внутри он использует TokenManager и обрабатывает refresh)
                        val pullCallResult = apiClient.pullCall(deviceId)
                        val pollLatencyMs = ((System.nanoTime() - pollStartNano) / 1_000_000L).coerceAtLeast(0L)
                        val nowDate = Date()
                        val nowStr = timeFmt.format(nowDate)
                        
                        // Определяем код ответа из Result
                        val code = when (pullCallResult.result) {
                            is ApiClient.Result.Success -> {
                                if (pullCallResult.result.data == null) {
                                    204 // Нет команд
                                } else {
                                    200 // Есть команда
                                }
                            }
                            is ApiClient.Result.Error -> {
                                when (pullCallResult.result.code) {
                                    401 -> 401 // Требуется повторный вход
                                    0 -> 0 // Сетевая ошибка
                                    else -> pullCallResult.result.code ?: 0
                                }
                            }
                        }
                        
                        // Логируем результат polling с информацией о Retry-After
                        when (code) {
                            200 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "PullCall: 200 (command received)")
                            204 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "PullCall: 204 (no commands)")
                            401 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: 401 (auth failed)")
                            429 -> {
                                val retryAfterMsg = pullCallResult.retryAfterSeconds?.let { "${it}s" } ?: "none"
                                val backoffDelay = rateLimitBackoff.getRateLimitDelay(pullCallResult.retryAfterSeconds)
                                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                                    "CallListenerService",
                                    "429 rate-limited: retryAfter=$retryAfterMsg, backoff=${backoffDelay}ms, mode=RATE_LIMIT"
                                )
                            }
                            0 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: 0 (network error)")
                            else -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: $code (error)")
                        }
                        
                        // Сохраняем last_poll_code/last_poll_at через TokenManager
                        tokenManager.saveLastPoll(code, nowStr)
                        tokenManager.saveLastPollLatencyMs(pollLatencyMs)
                        
                        val phone = (pullCallResult.result as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.phone
                        val callRequestId = (pullCallResult.result as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.callRequestId
                        
                        // Управление backoff для rate limiting
                        if (code == 429) {
                            rateLimitBackoff.incrementBackoff()
                            // Принудительно отправляем телеметрию при входе в rate limit режим
                            scope.launch {
                                try {
                                    apiClient.flushTelemetry()
                                } catch (e: Exception) {
                                    // Игнорируем ошибки flush (не критично)
                                }
                            }
                        } else if (code == 200 || code == 204) {
                            // Мягко снижаем backoff при успешных ответах (200/204)
                            // Используем decrement вместо reset для избежания "пилы" при чередовании успешных/неудачных запросов
                            rateLimitBackoff.decrementBackoff()
                        }
                        
                        // Адаптивная частота: при пустых командах (204) увеличиваем задержку
                        // При получении команды (200) - сбрасываем счетчик и возвращаемся к быстрой частоте
                        if (code == 204) {
                            consecutiveEmptyPolls++
                        } else if (code == 200 && phone != null) {
                            consecutiveEmptyPolls = 0 // Сброс при получении команды
                        }

                        // Периодически проверяем и очищаем устаревшие активные звонки (каждые 10 итераций, примерно каждые 15-30 секунд)
                        expiredCallsCheckCounter = (expiredCallsCheckCounter + 1) % 10
                        if (expiredCallsCheckCounter == 0) {
                            try {
                                val pendingCallManager = AppContainer.pendingCallStore as? ru.groupprofi.crmprofi.dialer.data.PendingCallManager
                                val expiredIds = pendingCallManager?.cleanupExpiredPendingCalls() ?: emptyList()
                                if (expiredIds.isNotEmpty()) {
                                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Очищено ${expiredIds.size} устаревших ожидаемых звонков (таймаут 5 минут)")
                                    // Помечаем устаревшие звонки в истории как UNKNOWN (если они там есть)
                                    expiredIds.forEach { expiredCallRequestId ->
                                        scope.launch {
                                            val expiredCall = AppContainer.pendingCallStore.getPendingCall(expiredCallRequestId)
                                            if (expiredCall != null) {
                                                // Если записи в истории нет, создаем с UNKNOWN статусом
                                                val existingCall = AppContainer.callHistoryStore.getCallById(expiredCallRequestId)
                                                if (existingCall == null) {
                                                    val historyItem = ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem(
                                                        id = expiredCall.callRequestId,
                                                        phone = expiredCall.phoneNumber,
                                                        phoneDisplayName = null,
                                                        status = ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem.CallStatus.UNKNOWN,
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
                                                // Удаляем из ожидаемых
                                                AppContainer.pendingCallStore.removePendingCall(expiredCallRequestId)
                                            }
                                        }
                                    }
                                }
                            } catch (e: Exception) {
                                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Ошибка при очистке устаревших звонков: ${e.message}")
                            }
                        }

                        // Периодически отправляем heartbeat в CRM, чтобы админ видел "живость" устройства.
                        heartbeatCounter = (heartbeatCounter + 1) % 10
                        if (heartbeatCounter == 0 && code != 0) {
                            // heartbeat не критичен: любые ошибки просто логируем.
                            try {
                                // Получаем метрики застрявших элементов очереди (если есть)
                                val stuckMetrics = queueManager.getStuckMetrics()
                                
                                // Используем ApiClient для отправки heartbeat
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
                        
                        // Периодически пытаемся отправить накопленные элементы из оффлайн-очереди.
                        queueFlushCounter = (queueFlushCounter + 1) % 20 // Каждые 20 циклов (примерно раз в минуту)
                        if (queueFlushCounter == 0 && code != 0 && code != 401) {
                            // Пытаемся отправить очередь только если есть интернет и не требуется авторизация
                            try {
                                // QueueManager.flushQueue теперь использует ApiClient внутри (через QueueManager)
                                // Но для совместимости оставляем текущий вызов, QueueManager сам использует httpClient
                                val sentCount = queueManager.flushQueue(BuildConfig.BASE_URL, tokenManager.getAccessToken() ?: "", apiClient.getHttpClient())
                                if (sentCount > 0) {
                                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Queue flushed: $sentCount items sent")
                                }
                            } catch (e: Exception) {
                                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Queue flush error: ${e.message}")
                            }
                        }
                        
                        // Периодически отправляем накопленные логи (раз в час или при накоплении > 200 логов)
                        logSendCounter = (logSendCounter + 1) % 120 // Каждые 120 циклов (примерно раз в час при рабочем времени)
                        val shouldSendLogs = logSendCounter == 0 || logCollector.getBufferSize() > 200
                        if (shouldSendLogs && code != 0 && code != 401) {
                            try {
                                val bundle = logCollector.takeLogs(maxEntries = 500)
                                if (bundle != null) {
                                    // Используем ApiClient для отправки логов (он сам добавит в очередь при ошибках)
                                    // Маскирование уже сделано в LogSender, но используем ApiClient для единообразия
                                    // Пока оставляем logSender для совместимости, но можно перейти на apiClient.sendLogBundle
                                    logSender.sendLogBundle(BuildConfig.BASE_URL, tokenManager.getAccessToken() ?: "", deviceId, bundle)
                                    android.util.Log.i("CallListenerService", "Sent log bundle: ${bundle.entryCount} entries")
                                }
                            } catch (e: Exception) {
                                android.util.Log.w("CallListenerService", "Log send error: ${e.message}")
                                LogInterceptor.addLog(android.util.Log.WARN, "CallListenerService", "Log send error: ${e.message}")
                            }
                        }
                        
                        // Обработка ошибок авторизации - refresh token истек, требуется повторный вход
                        if (code == 401) {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Authentication failed (401), stopping service")
                            // Очищаем токены через TokenManager (ApiClient уже мог очистить их при refresh failure)
                            if (!tokenManager.hasTokens()) {
                                tokenManager.clearAll()
                            }
                            // Останавливаем сервис при ошибке авторизации
                            stopSelf()
                            return@launch
                        }
                        
                        // Сетевые ошибки (код 0) - просто логируем, продолжаем работу
                        // Убрано обновление foreground notification - оно тихое и не должно мешать
                        
                        if (!phone.isNullOrBlank() && !callRequestId.isNullOrBlank()) {
                            // device_received_at + локальное сохранение call_request_id
                            val receivedAt = System.currentTimeMillis()
                            tokenManager.saveLastCallCommand(callRequestId, receivedAt)
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                                "CallListenerService",
                                "COMMAND_RECEIVED id=$callRequestId pollLatencyMs=$pollLatencyMs"
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
                        } else {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Номер или ID пустой, пропускаем")
                        }

                        // Надёжный резолв pending calls: расширенные ретраи + таймаут
                        maybeResolvePendingCalls()
                    
                        // ДВА РЕЖИМА polling:
                        // - быстрый: 500–800мс, когда приложение активно/готово (не делаем агрессивный backoff)
                        //   ПРИМЕЧАНИЕ: Частота выбрана для минимальной задержки доставки команды "позвонить".
                        //   Если нагрузка на батарею критична, можно увеличить до 1-2 сек или использовать push/FCM.
                        // - медленный: 2–10с, когда нет активных команд и приложение не активно (экономим батарею)
                        val isFastMode = AppState.isForeground ||
                                (AppContainer.pendingCallStore.hasActivePendingCallsFlow.value) ||
                                (System.currentTimeMillis() - (tokenManager.getLastCallCommandReceivedAt() ?: 0L) < 2 * 60 * 1000L)

                        val baseDelay = when {
                            code == 429 -> {
                                // Rate limiting: используем exponential backoff с Retry-After
                                rateLimitBackoff.getRateLimitDelay(pullCallResult.retryAfterSeconds)
                            }
                            !isFastMode -> {
                                // Медленный режим: ступенчатое увеличение при пустых ответах
                                rateLimitBackoff.getEmptyPollDelay(consecutiveEmptyPolls)
                            }
                            else -> {
                                // Быстрый режим: базовый интервал с небольшим увеличением при множественных пустых ответах
                                if (consecutiveEmptyPolls > 10) {
                                    // После 10+ пустых ответов в быстром режиме немного увеличиваем интервал
                                    val step = ((consecutiveEmptyPolls - 10) / 5).coerceIn(0, 3)
                                    (650L + step * 200L).coerceAtMost(1_250L)
                                } else {
                                    650L
                                }
                            }
                        }
                        
                        // Джиттер: быстрый режим ±150мс (попадаем в 500..800), медленный ±20% от baseDelay
                        val jitter = if (baseDelay <= 1000L) {
                            random.nextInt(301) - 150 // -150..+150
                        } else {
                            val jitterRange = (baseDelay * 0.2).toInt()
                            random.nextInt(jitterRange * 2 + 1) - jitterRange
                        }
                        val delayMs = (baseDelay + jitter).coerceAtLeast(500L)
                        
                        // Определяем режим для логирования
                        val mode = when {
                            code == 429 -> "RATE_LIMIT"
                            !isFastMode -> "SLOW"
                            else -> "FAST"
                        }
                        
                        // Логируем детали polling с режимом и задержкой
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                            "CallListenerService",
                            "Poll: code=$code, nextDelayMs=${delayMs}ms, mode=$mode, emptyCount=$consecutiveEmptyPolls" +
                                    if (code == 429) {
                                        ", retryAfter=${pullCallResult.retryAfterSeconds?.let { "${it}s" } ?: "none"}, backoff=${rateLimitBackoff.getBackoffLevel()}"
                                    } else {
                                        ""
                                    }
                        )
                    
                    delay(delayMs)
                    } catch (_: Exception) {
                        // silent for MVP
                        // При ошибке делаем минимальную задержку перед повтором
                        delay(2000)
                    }
                }
            }

        return START_STICKY
    }

    override fun onDestroy() {
        loopJob?.cancel()
        loopJob = null
        callLogObserverManager?.unregister()
        callLogObserverManager = null
        super.onDestroy()
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
     */
    private fun buildForegroundNotification() = NotificationCompat.Builder(this, CHANNEL_FOREGROUND)
        .setSmallIcon(android.R.drawable.sym_action_call)
        .setContentTitle("CRM ПРОФИ")
        .setContentText("Приложение готово к звонкам")
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .setPriority(NotificationCompat.PRIORITY_LOW)
        .setCategory(NotificationCompat.CATEGORY_SERVICE)
        .build()

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
     */
    private suspend fun readCallLogForPhone(
        phoneNumber: String,
        startedAtMillis: Long
    ): CallInfo? {
        val normalized = PhoneNumberNormalizer.normalize(phoneNumber)
        
        // Временное окно: от 2 минут до начала ожидания до 15 минут после
        // Расширено для более надежного поиска звонков, которые могли быть совершены с задержкой
        val windowStart = startedAtMillis - (2 * 60 * 1000) // 2 минуты до открытия звонилки
        val windowEnd = startedAtMillis + (15 * 60 * 1000) // 15 минут после открытия звонилки
        
        val searchLast4 = if (normalized.length >= 4) normalized.takeLast(4) else "****"
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "CallLog search: last4=$searchLast4, window=${(windowEnd - windowStart) / 1000}s")
        
        try {
            val cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(
                    CallLog.Calls.NUMBER,
                    CallLog.Calls.TYPE,
                    CallLog.Calls.DURATION,
                    CallLog.Calls.DATE
                ),
                "${CallLog.Calls.DATE} >= ? AND ${CallLog.Calls.DATE} <= ?",
                arrayOf(windowStart.toString(), windowEnd.toString()),
                "${CallLog.Calls.DATE} DESC"
            )
            
            cursor?.use {
                var checkedCount = 0
                val maxRows = 50
                val checkedEntries = mutableListOf<String>() // Для диагностики (без PII)
                // Some CallLog providers don't support "LIMIT" in sortOrder; limit rows in code for compatibility.
                while (it.moveToNext()) {
                    if (++checkedCount > maxRows) break
                    
                    val number = it.getString(it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)) ?: ""
                    val normalizedNumber = PhoneNumberNormalizer.normalize(number)
                    val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                    val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                    
                    // Сохраняем диагностическую информацию (без PII - только последние 4 цифры)
                    val last4 = if (normalizedNumber.length >= 4) normalizedNumber.takeLast(4) else "****"
                    checkedEntries.add("type=$type,date=${date},last4=$last4")
                    
                    // Проверяем совпадение номера (улучшенная проверка с fallback на последние 10 цифр)
                    val match = when {
                        normalizedNumber == normalized -> {
                            true // Полное совпадение
                        }
                        normalizedNumber.length >= 10 && normalized.length >= 10 -> {
                            // Сравниваем последние 10 цифр (более надежно чем 7)
                            val last10Match = normalizedNumber.takeLast(10) == normalized.takeLast(10)
                            if (last10Match) {
                                true
                            } else {
                                // Fallback: проверяем совпадение окончаний (более гибко)
                                val minLen = minOf(normalizedNumber.length, normalized.length)
                                val compareLen = minLen.coerceAtMost(10).coerceAtLeast(7)
                                normalizedNumber.takeLast(compareLen) == normalized.takeLast(compareLen) ||
                                normalizedNumber.endsWith(normalized.takeLast(minOf(compareLen, normalized.length))) ||
                                normalized.endsWith(normalizedNumber.takeLast(minOf(compareLen, normalizedNumber.length)))
                            }
                        }
                        normalizedNumber.length >= 7 && normalized.length >= 7 -> {
                            // Сравниваем последние 7+ цифр (старая логика для коротких номеров)
                            val last7Match = normalizedNumber.takeLast(7) == normalized.takeLast(7)
                            val endsWithMatch = normalizedNumber.endsWith(normalized.takeLast(minOf(7, normalized.length))) ||
                                              normalized.endsWith(normalizedNumber.takeLast(minOf(7, normalizedNumber.length)))
                            last7Match || endsWithMatch
                        }
                        else -> false
                    }
                    
                    if (match) {
                        val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                        val elapsedMs = System.currentTimeMillis() - startedAtMillis
                        val callTypeStr = when (type) {
                            CallLog.Calls.OUTGOING_TYPE -> "OUTGOING"
                            CallLog.Calls.INCOMING_TYPE -> "INCOMING"
                            CallLog.Calls.MISSED_TYPE -> "MISSED"
                            5 -> "REJECTED"
                            else -> "UNKNOWN($type)"
                        }
                        
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                            "CallListenerService",
                            "CallLog matched: last4=$last4, type=$callTypeStr, duration=${duration}s, elapsed=${elapsedMs}ms, checked=$checkedCount"
                        )
                        return CallInfo(type, duration, date)
                    }
                }
                
                // Диагностика: логируем информацию о проверенных записях (без PII)
                if (checkedEntries.isNotEmpty()) {
                    val sampleEntries = checkedEntries.take(5).joinToString("; ")
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                        "CallListenerService",
                        "CallLog search: checked=$checkedCount entries (sample: $sampleEntries), no match found"
                    )
                }
            }
        } catch (e: SecurityException) {
            throw e
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Ошибка чтения CallLog: ${e.message}", e)
        }
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "CallLog search: no match found for last4=$searchLast4")
        return null
    }

    /**
     * Обработать найденный результат звонка.
     * ЭТАП 2: Добавлена сборка расширенных данных и отправка extended payload.
     */
    private suspend fun handleCallResult(
        pendingCall: PendingCall,
        callInfo: CallInfo
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

        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallListenerService",
            "CALL_RESOLVED id=${pendingCall.callRequestId} status=UNKNOWN resolveReason=$resolveReason attempts=${pendingCall.attempts}"
        )
    }

    private suspend fun maybeResolvePendingCalls() {
        val now = System.currentTimeMillis()
        if (now - lastResolveTickMs < 1000L) return
        lastResolveTickMs = now

        val activeCalls = AppContainer.pendingCallStore.getActivePendingCalls()
        if (activeCalls.isEmpty()) return

        // Стратегия ретраев до 5 минут
        val scheduleSec = listOf(5, 10, 20, 30, 60, 90, 120, 180, 240, 300)
        val maxMs = 5 * 60 * 1000L

        val hasCallLogPermission =
            ContextCompat.checkSelfPermission(this, android.Manifest.permission.READ_CALL_LOG) ==
                    android.content.pm.PackageManager.PERMISSION_GRANTED

        for (pendingCall in activeCalls) {
            val elapsed = now - pendingCall.startedAtMillis
            if (!hasCallLogPermission) {
                handleCallResultUnknown(
                    pendingCall,
                    resolveReason = "permission_missing",
                    reasonIfUnknown = "READ_CALL_LOG not granted"
                )
                continue
            }

            if (elapsed >= maxMs) {
                handleCallResultUnknown(
                    pendingCall,
                    resolveReason = "timeout",
                    reasonIfUnknown = "CallLog not matched within 5 minutes"
                )
                continue
            }

            val attemptIndex = pendingCall.attempts.coerceIn(0, scheduleSec.lastIndex)
            val dueMs = scheduleSec[attemptIndex] * 1000L
            if (elapsed < dueMs) continue

            // Пытаемся матчить: атомарно берём право на резолв, иначе пропускаем
            val marked = AppContainer.pendingCallStore.tryMarkResolving(pendingCall.callRequestId)
            if (!marked) continue

            try {
                val callInfo = readCallLogForPhone(pendingCall.phoneNumber, pendingCall.startedAtMillis)
                if (callInfo != null) {
                    handleCallResult(pendingCall, callInfo)
                } else {
                    // Возвращаемся в ожидание до следующего due
                    AppContainer.pendingCallStore.updateCallState(
                        pendingCall.callRequestId,
                        PendingCall.PendingState.PENDING,
                        incrementAttempts = false
                    )
                }
            } catch (se: SecurityException) {
                handleCallResultUnknown(
                    pendingCall,
                    resolveReason = "permission_missing",
                    reasonIfUnknown = "READ_CALL_LOG not granted"
                )
            } catch (e: Exception) {
                // Ошибка чтения CallLog не должна валить процесс: ждём следующий тик/попытку
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w(
                    "CallListenerService",
                    "CallLog resolve attempt error: ${e.message}"
                )
                AppContainer.pendingCallStore.updateCallState(
                    pendingCall.callRequestId,
                    PendingCall.PendingState.PENDING,
                    incrementAttempts = false
                )
            }
        }
    }
    
    /**
     * Определить человеческий статус звонка.
     */
    private fun determineHumanStatus(type: Int, duration: Long): Pair<CallHistoryItem.CallStatus, String> {
        return when (type) {
            CallLog.Calls.OUTGOING_TYPE -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
            CallLog.Calls.MISSED_TYPE -> {
                Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
            }
            CallLog.Calls.INCOMING_TYPE -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
            5 -> { // REJECTED_TYPE (API 29+)
                Pair(CallHistoryItem.CallStatus.REJECTED, "Сброс")
            }
            else -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
        }
    }
    
    
    /**
     * Информация о звонке из CallLog.
     */
    private data class CallInfo(
        val type: Int,      // Тип звонка (OUTGOING, MISSED, INCOMING, etc.)
        val duration: Long, // Длительность в секундах
        val date: Long      // Timestamp звонка
    )

    // УДАЛЕНО: sendCallInfoToCRM, sendHeartbeat, sendTelemetryLatency
    // Теперь используются методы ApiClient: sendCallUpdate(), sendHeartbeat(), sendTelemetryBatch()
    // TelemetryInterceptor автоматически собирает latency для всех /api/phone/* запросов

    /**
     * Рабочие часы, в которые приложение должно работать максимально отзывчиво.
     * Используем локальное время устройства.
     */
    private fun isWorkingHours(): Boolean {
        val cal = Calendar.getInstance()
        val hour = cal.get(Calendar.HOUR_OF_DAY) // 0..23
        return hour in 7..19
    }

    /**
     * Безопасное хранилище настроек для сервиса (токены, device_id, pending_call_*).
     * Стараемся использовать EncryptedSharedPreferences, при ошибке — обычные SharedPreferences.
     */
    private fun securePrefs(): SharedPreferences {
        return try {
            val masterKey = MasterKey.Builder(this)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                this,
                PREFS,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            )
        } catch (_: Exception) {
            getSharedPreferences(PREFS, MODE_PRIVATE)
        }
    }

    companion object {
        private const val CHANNEL_FOREGROUND = "crmprofi_foreground"
        private const val NOTIF_ID_FOREGROUND = 1000

        private const val PREFS = "crmprofi_dialer"
        
        // УСТАРЕЛО: KEY_TOKEN, KEY_REFRESH, KEY_DEVICE_ID теперь в TokenManager
        // Оставлены для совместимости с securePrefs() для pending_call_* и других локальных данных
        @Deprecated("Use TokenManager instead", ReplaceWith("TokenManager.getInstance(context).getAccessToken()"))
        private const val KEY_TOKEN = "token"
        @Deprecated("Use TokenManager instead", ReplaceWith("TokenManager.getInstance(context).getRefreshToken()"))
        private const val KEY_REFRESH = "refresh"
        @Deprecated("Use TokenManager instead", ReplaceWith("TokenManager.getInstance(context).getDeviceId()"))
        private const val KEY_DEVICE_ID = "device_id"
        
        // УСТАРЕЛО: KEY_LAST_POLL_AT, KEY_LAST_POLL_CODE теперь в TokenManager
        // Оставлены для совместимости, но рекомендуется использовать TokenManager.getLastPollAt() / getLastPollCode()
        @Deprecated("Use TokenManager.getLastPollAt() instead")
        const val KEY_LAST_POLL_AT = "last_poll_at"
        @Deprecated("Use TokenManager.getLastPollCode() instead")
        const val KEY_LAST_POLL_CODE = "last_poll_code"

        // EXTRA_* оставлены для совместимости с Intent extras, но токены теперь берутся из TokenManager
        const val EXTRA_TOKEN = "token"
        const val EXTRA_REFRESH = "refresh"
        const val EXTRA_DEVICE_ID = "device_id"

        const val ACTION_START = "ru.groupprofi.crmprofi.dialer.START"
        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"
    }
}


