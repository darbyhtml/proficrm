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
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.data.PendingCallManager
import ru.groupprofi.crmprofi.dialer.data.CallLogObserverManager
import ru.groupprofi.crmprofi.dialer.data.CallHistoryRepository
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
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
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient
    // Ленивая инициализация QueueManager - создается только при первом использовании
    private val queueManager: QueueManager by lazy { QueueManager(this) }
    // Координатор потока обработки команды на звонок
    private val callFlowCoordinator: CallFlowCoordinator by lazy { CallFlowCoordinator.getInstance(this) }
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

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }

        // Инициализируем TokenManager и ApiClient
        tokenManager = TokenManager.getInstance(this)
        apiClient = ApiClient.getInstance(this)
        logSender = LogSender(this, apiClient.getHttpClient(), queueManager)
        
        // Инициализируем CallLogObserverManager для отслеживания изменений CallLog
        callLogObserverManager = CallLogObserverManager(
            contentResolver = contentResolver,
            pendingCallStore = AppContainer.pendingCallStore,
            callHistoryStore = AppContainer.callHistoryStore,
            scope = scope
        )
        callLogObserverManager?.register()
        
        val deviceId = (intent?.getStringExtra(EXTRA_DEVICE_ID) ?: tokenManager.getDeviceId() ?: "").trim()

        // Проверяем наличие токенов через TokenManager
        if (!tokenManager.hasTokens() || deviceId.isBlank()) {
            stopSelf()
            return START_NOT_STICKY
        }

        // If notifications are disabled, foreground-service becomes pointless (user won't see anything).
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Android 13+ (targetSdk 33+) may crash/startForeground fail if notifications are not allowed.
        if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            val granted = ContextCompat.checkSelfPermission(this, perm) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                stopSelf()
                return START_NOT_STICKY
            }
        }

        // Убеждаемся, что device_id сохранен в TokenManager
        if (tokenManager.getDeviceId() != deviceId) {
            tokenManager.saveDeviceId(deviceId)
        }

        ensureForegroundChannel()
        try {
            startForeground(NOTIF_ID_FOREGROUND, buildForegroundNotification())
        } catch (_: Throwable) {
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

        if (loopJob == null) {
            loopJob = scope.launch {
                while (true) {
                    try {
                        // Используем ApiClient для polling (внутри он использует TokenManager и обрабатывает refresh)
                        val pullResult = apiClient.pullCall(deviceId)
                        val nowDate = Date()
                        val nowStr = timeFmt.format(nowDate)
                        
                        // Определяем код ответа из Result
                        val code = when (pullResult) {
                            is ApiClient.Result.Success -> {
                                if (pullResult.data == null) {
                                    204 // Нет команд
                                } else {
                                    200 // Есть команда
                                }
                            }
                            is ApiClient.Result.Error -> {
                                when (pullResult.code) {
                                    401 -> 401 // Требуется повторный вход
                                    0 -> 0 // Сетевая ошибка
                                    else -> pullResult.code ?: 0
                                }
                            }
                        }
                        
                        // Логируем результат polling
                        when (code) {
                            200 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "PullCall: 200 (command received)")
                            204 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "PullCall: 204 (no commands)")
                            401 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: 401 (auth failed)")
                            429 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "PullCall: 429 (rate limited, will retry with delay)")
                            0 -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: 0 (network error)")
                            else -> ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "PullCall: $code (error)")
                        }
                        
                        // Сохраняем last_poll_code/last_poll_at через TokenManager
                        tokenManager.saveLastPoll(code, nowStr)
                        
                        val phone = (pullResult as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.phone
                        val callRequestId = (pullResult as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.callRequestId
                        
                        // Адаптивная частота: при пустых командах (204) увеличиваем задержку
                        // При получении команды (200) - сбрасываем счетчик и возвращаемся к быстрой частоте
                        // При rate limiting (429) - увеличиваем счетчик быстрее для увеличения задержки
                        if (code == 204) {
                            consecutiveEmptyPolls++
                        } else if (code == 200 && phone != null) {
                            consecutiveEmptyPolls = 0 // Сброс при получении команды
                        } else if (code == 429) {
                            consecutiveEmptyPolls += 3 // При rate limiting увеличиваем счетчик быстрее
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
                            // Используем CallFlowCoordinator для обработки команды на звонок
                            callFlowCoordinator.handleCallCommand(phone, callRequestId)
                        } else {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallListenerService", "Номер или ID пустой, пропускаем")
                        }
                        
                        // Адаптивная частота опроса с джиттером для предотвращения синхронизации устройств
                        val phoneNotNull = !phone.isNullOrBlank()
                        val baseDelay = when {
                            // При получении команды - быстрый возврат к активному опросу
                            code == 200 && phoneNotNull -> 1500L
                        // При rate limiting (429) - увеличиваем задержку значительно
                        code == 429 -> {
                            when {
                                consecutiveEmptyPolls < 10 -> 5000L // Первые 10 - 5 секунд
                                consecutiveEmptyPolls < 20 -> 10000L // Следующие 10 - 10 секунд
                                else -> 30000L // Дальше - 30 секунд (чтобы не перегружать сервер)
                            }
                        }
                        // При пустых командах (204) - увеличиваем задержку постепенно
                        code == 204 -> {
                            when {
                                consecutiveEmptyPolls < 5 -> 1500L // Первые 5 пустых - быстро
                                consecutiveEmptyPolls < 15 -> 3000L // Следующие 10 - средняя частота
                                else -> 5000L // Дальше - медленная частота
                            }
                        }
                        // Вне рабочего времени - медленная частота
                        !isWorkingHours() -> 5000L
                        // В рабочее время - базовая частота
                        else -> 1500L
                        }
                        
                        // Джиттер: добавляем случайную задержку ±200мс для предотвращения синхронизации устройств
                        val jitter = random.nextInt(401) - 200 // -200..+200 мс
                        val delayMs = (baseDelay + jitter).coerceAtLeast(1000L) // Минимум 1 секунда
                        
                        delay(delayMs)
                    } catch (_: Exception) {
                        // silent for MVP
                        // При ошибке делаем минимальную задержку перед повтором
                        delay(2000)
                    }
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
     * Маскировать номер телефона для логов.
     */
    private fun maskPhone(phone: String): String {
        if (phone.length <= 4) return "***"
        return "${phone.take(3)}***${phone.takeLast(4)}"
    }

    /**
     * Начать процесс определения результата звонка.
     * Создаёт PendingCall и запускает повторные проверки через 5/10/15 секунд.
     */
    private fun startCallResolution(phone: String, callRequestId: String) {
        scope.launch {
            try {
                val normalizedPhone = PendingCall.normalizePhone(phone)
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
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("CallListenerService", "Начато определение результата звонка: ${maskPhone(phone)}")
                
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
        val delays = listOf(5000L, 10000L, 15000L) // 5, 10, 15 секунд
        
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
                
                // Обновляем состояние на RESOLVING
                AppContainer.pendingCallStore.updateCallState(
                    pendingCall.callRequestId,
                    PendingCall.PendingState.RESOLVING,
                    incrementAttempts = true
                )
                
                // Пытаемся найти звонок в CallLog
                try {
                    val callInfo = readCallLogForPhone(pendingCall.phoneNumber, pendingCall.startedAtMillis)
                    if (callInfo != null) {
                        // Найдено совпадение - обрабатываем результат
                        handleCallResult(pendingCall, callInfo)
                        return@launch
                    } else {
                        // Не найдено - если это последняя попытка, помечаем как FAILED
                        if (index == delays.size - 1) {
                            handleCallResultFailed(pendingCall)
                        }
                    }
                } catch (e: SecurityException) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallListenerService", "Нет разрешения на чтение CallLog: ${e.message}")
                    // Если нет разрешения - помечаем как FAILED
                    if (index == delays.size - 1) {
                        handleCallResultFailed(pendingCall)
                    }
                } catch (e: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallListenerService", "Ошибка чтения CallLog: ${e.message}", e)
                    if (index == delays.size - 1) {
                        handleCallResultFailed(pendingCall)
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
        val normalized = PendingCall.normalizePhone(phoneNumber)
        
        // Временное окно: ±5 минут от времени начала ожидания
        val windowStart = startedAtMillis - (5 * 60 * 1000)
        val windowEnd = startedAtMillis + (5 * 60 * 1000)
        
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
                "${CallLog.Calls.DATE} DESC LIMIT 10"
            )
            
            cursor?.use {
                while (it.moveToNext()) {
                    val number = it.getString(it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)) ?: ""
                    val normalizedNumber = PendingCall.normalizePhone(number)
                    
                    // Проверяем совпадение номера (последние 7-10 цифр)
                    if (normalizedNumber.endsWith(normalized.takeLast(7)) || 
                        normalized.endsWith(normalizedNumber.takeLast(7))) {
                        val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                        val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                        val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                        
                        return CallInfo(type, duration, date)
                    }
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
            statusText = humanStatusText,
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
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallListenerService", 
            "Результат звонка определён и отправлен: ${maskPhone(pendingCall.phoneNumber)} -> $humanStatusText (direction=$direction, resolveMethod=$resolveMethod)"
        )
    }
    
    /**
     * Обработать случай, когда результат не удалось определить.
     * ЭТАП 2: Отправляем статус "unknown" в CRM.
     */
    private suspend fun handleCallResultFailed(pendingCall: PendingCall) {
        // Помечаем как FAILED
        AppContainer.pendingCallStore.updateCallState(
            pendingCall.callRequestId,
            PendingCall.PendingState.FAILED
        )
        
        // ЭТАП 2: Отправляем статус "unknown" в CRM
        val result = apiClient.sendCallUpdate(
            callRequestId = pendingCall.callRequestId,
            callStatus = CallStatusApi.UNKNOWN.apiValue,
            callStartedAt = pendingCall.startedAtMillis,
            callDurationSeconds = null,
            // Новые поля (ЭТАП 2)
            direction = null, // Неизвестно, так как звонок не найден в CallLog
            resolveMethod = ResolveMethod.UNKNOWN,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )
        
        // Сохраняем в историю с статусом "Не удалось определить"
        val historyItem = CallHistoryItem(
            id = pendingCall.callRequestId,
            phone = pendingCall.phoneNumber,
            phoneDisplayName = null,
            status = CallHistoryItem.CallStatus.UNKNOWN,
            statusText = "Не удалось определить результат",
            durationSeconds = null,
            startedAt = pendingCall.startedAtMillis,
            sentToCrm = result is ApiClient.Result.Success,
            sentToCrmAt = if (result is ApiClient.Result.Success) System.currentTimeMillis() else null,
            // Новые поля (ЭТАП 2)
            direction = null,
            resolveMethod = ResolveMethod.UNKNOWN,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )
        
        AppContainer.callHistoryStore.addOrUpdate(historyItem)
        
        // Удаляем из ожидаемых
        AppContainer.pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.w(
            "CallListenerService", 
            "Не удалось определить результат звонка: ${maskPhone(pendingCall.phoneNumber)} (attempts=${pendingCall.attempts})"
        )
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
     * Маскировать номер телефона для логов.
     */
    private fun maskPhone(phone: String): String {
        if (phone.length <= 4) return "***"
        return "${phone.take(3)}***${phone.takeLast(4)}"
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


