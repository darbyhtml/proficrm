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
    private val queueManager = QueueManager(this)
    private val logCollector = LogCollector()
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

        ensureChannel()
        try {
            startForeground(NOTIF_ID, buildListeningNotification())
        } catch (_: Throwable) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Инициализируем перехватчик логов
        LogInterceptor.setCollector(logCollector)

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
                        
                        // Сохраняем last_poll_code/last_poll_at через TokenManager
                        tokenManager.saveLastPoll(code, nowStr)
                        
                        val phone = (pullResult as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.phone
                        val callRequestId = (pullResult as? ApiClient.Result.Success<ApiClient.PullCallResponse?>)?.data?.callRequestId
                        
                        // Адаптивная частота: при пустых командах (204) увеличиваем задержку
                        // При получении команды (200) - сбрасываем счетчик и возвращаемся к быстрой частоте
                        if (code == 204) {
                            consecutiveEmptyPolls++
                        } else if (code == 200 && phone != null) {
                            consecutiveEmptyPolls = 0 // Сброс при получении команды
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
                                    android.util.Log.i("CallListenerService", "Flushed $sentCount items from queue")
                                }
                        } catch (e: Exception) {
                            android.util.Log.w("CallListenerService", "Queue flush error: ${e.message}")
                            LogInterceptor.addLog(android.util.Log.WARN, "CallListenerService", "Queue flush error: ${e.message}")
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
                                    val now = Date()
                                    val isoTime = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US).apply {
                                        timeZone = java.util.TimeZone.getTimeZone("UTC")
                                    }.format(now)
                                    
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
                            android.util.Log.w("CallListenerService", "Authentication failed (401), stopping service")
                            // Очищаем токены через TokenManager (ApiClient уже мог очистить их при refresh failure)
                            if (!tokenManager.hasTokens()) {
                                tokenManager.clearAll()
                            }
                            updateListeningNotification("Требуется повторный вход в приложении")
                            // Даем время увидеть сообщение, затем останавливаем сервис
                            delay(10000) // 10 секунд на то, чтобы пользователь увидел уведомление
                            stopSelf()
                            return@launch
                        }
                        
                        // Сетевые ошибки (код 0) - просто логируем, продолжаем работу
                        val working = isWorkingHours()
                        val prefix = if (working) "" else "Вне рабочего времени · "
                        if (code == 0) {
                            updateListeningNotification("${prefix}Нет подключения · $nowStr")
                        } else {
                            updateListeningNotification("${prefix}Опрос: $code · $nowStr")
                        }
                        
                        if (!phone.isNullOrBlank()) {
                            android.util.Log.i("CallListenerService", "Processing call command: phone=$phone, id=$callRequestId")
                            
                            // 1) Всегда показываем уведомление с действием (работает и в фоне).
                            try {
                                showCallNotification(phone)
                                android.util.Log.i("CallListenerService", "Call notification shown for $phone")
                            } catch (e: Throwable) {
                                android.util.Log.e("CallListenerService", "Error showing notification: ${e.message}")
                            }
                            // 2) Если приложение на экране — открываем звонилку сразу.
                            if (AppState.isForeground) {
                                try {
                                    val uri = Uri.parse("tel:$phone")
                                    val dial = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                    android.util.Log.i("CallListenerService", "Opening dialer for $phone (foreground)")
                                    // запуск activity делаем на main thread, чтобы не словить странные краши на прошивках
                                    Handler(Looper.getMainLooper()).post {
                                        try {
                                            startActivity(dial)
                                            android.util.Log.i("CallListenerService", "Dialer opened successfully")
                                            // Сохраняем call_request_id для последующей проверки CallLog
                                            if (!callRequestId.isNullOrBlank()) {
                                                securePrefs().edit()
                                                    .putString("pending_call_$phone", callRequestId)
                                                    .putLong("pending_call_time_$phone", System.currentTimeMillis())
                                                    .apply()
                                                // Проверяем CallLog через 5 секунд
                                                checkCallLogAndSend(phone)
                                            }
                                        } catch (e: Throwable) {
                                            android.util.Log.e("CallListenerService", "Error opening dialer: ${e.message}")
                                        }
                                    }
                                } catch (e: Throwable) {
                                    android.util.Log.e("CallListenerService", "Error creating dial intent: ${e.message}")
                                }
                            } else {
                                android.util.Log.d("CallListenerService", "App in background, notification only")
                                // Даже в фоне сохраняем call_request_id и проверяем CallLog
                                if (!callRequestId.isNullOrBlank()) {
                                    securePrefs().edit()
                                        .putString("pending_call_$phone", callRequestId)
                                        .putLong("pending_call_time_$phone", System.currentTimeMillis())
                                        .apply()
                                    checkCallLogAndSend(phone)
                                }
                            }
                        } else {
                            android.util.Log.d("CallListenerService", "Phone is blank, skipping")
                        }
                    } catch (_: Exception) {
                        // silent for MVP
                    }
                    
                    // Адаптивная частота опроса с джиттером для предотвращения синхронизации устройств
                    val baseDelay = when {
                        // При получении команды - быстрый возврат к активному опросу
                        code == 200 && phone != null -> 1500L
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
                }
            }
        }

        return START_STICKY
    }

    override fun onDestroy() {
        loopJob?.cancel()
        loopJob = null
        super.onDestroy()
    }

    // УДАЛЕНО: pullCallWithRefresh, refreshAccessWithMutex, refreshAccess
    // Теперь используется ApiClient.pullCall(), который внутри использует TokenManager для refresh

    private fun ensureChannel() {
        if (Build.VERSION.SDK_INT < 26) return
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val ch = NotificationChannel(
            CHANNEL_ID,
            "CRM ПРОФИ — звонки",
            NotificationManager.IMPORTANCE_HIGH
        )
        ch.description = "Команды на звонок из CRM"
        nm.createNotificationChannel(ch)
    }

    private fun buildListeningNotification() = NotificationCompat.Builder(this, CHANNEL_ID)
        .setSmallIcon(android.R.drawable.sym_action_call)
        .setContentTitle("CRM ПРОФИ")
        .setContentText("Слушаю команды на звонок…")
        .setOngoing(true)
        .setOnlyAlertOnce(true)
        .addAction(
            android.R.drawable.ic_menu_close_clear_cancel,
            "Остановить",
            PendingIntent.getService(
                this,
                1,
                Intent(this, CallListenerService::class.java).setAction(ACTION_STOP),
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
            )
        )
        .build()

    private fun updateListeningNotification(text: String) {
        try {
            val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
            nm.notify(
                NOTIF_ID,
                NotificationCompat.Builder(this, CHANNEL_ID)
                    .setSmallIcon(android.R.drawable.sym_action_call)
                    .setContentTitle("CRM ПРОФИ")
                    .setContentText(text)
                    .setOngoing(true)
                    .setOnlyAlertOnce(true)
                    .addAction(
                        android.R.drawable.ic_menu_close_clear_cancel,
                        "Остановить",
                        PendingIntent.getService(
                            this,
                            1,
                            Intent(this, CallListenerService::class.java).setAction(ACTION_STOP),
                            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
                        )
                    )
                    .build()
            )
        } catch (_: Throwable) {
            // ignore
        }
    }

    private fun showCallNotification(phone: String) {
        val uri = Uri.parse("tel:$phone")
        val dialIntent = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        val pi = PendingIntent.getActivity(
            this,
            2,
            dialIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
        )
        
        // Красивое уведомление с номером и иконкой телефона
        val n = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("CRM ПРОФИ — Звонок")
            .setContentText("Номер: $phone")
            .setStyle(NotificationCompat.BigTextStyle()
                .bigText("Нажмите, чтобы открыть набор номера\n$phone"))
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setCategory(NotificationCompat.CATEGORY_CALL)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .addAction(android.R.drawable.sym_action_call, "Позвонить", pi)
            .setShowWhen(true)
            .setWhen(System.currentTimeMillis())
            .build()
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_CALL_ID, n)
        
        // Также открываем набор номера сразу, если приложение в фоне
        if (!AppState.isForeground) {
            try {
                Handler(Looper.getMainLooper()).postDelayed({
                    try {
                        startActivity(dialIntent)
                    } catch (_: Throwable) {
                        // ignore
                    }
                }, 500)
            } catch (_: Throwable) {
                // ignore
            }
        }
    }

    private fun checkCallLogAndSend(phone: String) {
        // Проверяем CallLog через 5 секунд после открытия звонилки (даем время на звонок)
        scope.launch {
            delay(5000)
            try {
                val prefs = securePrefs()
                val callRequestId = prefs.getString("pending_call_$phone", null)
                if (callRequestId.isNullOrBlank()) {
                    android.util.Log.d("CallListenerService", "No pending call request ID for $phone")
                    return@launch
                }

                // Читаем CallLog для этого номера
                val callInfo = readCallLogForPhone(phone)
                if (callInfo != null) {
                    android.util.Log.i("CallListenerService", "Found call in CallLog: status=${callInfo.first}, duration=${callInfo.second}, started=${callInfo.third}")
                    // Используем ApiClient для отправки результата звонка
                    val status = callInfo.first
                    val duration = callInfo.second?.toInt()
                    val startedAt = callInfo.third
                    
                    val result = apiClient.sendCallUpdate(
                        callRequestId = callRequestId,
                        callStatus = status,
                        callStartedAt = startedAt,
                        callDurationSeconds = duration
                    )
                    
                    if (result is ApiClient.Result.Success) {
                        android.util.Log.i("CallListenerService", "Call info sent successfully to CRM")
                    } else {
                        android.util.Log.w("CallListenerService", "Failed to send call info: ${(result as? ApiClient.Result.Error)?.message}")
                    }
                    
                    // Очищаем сохраненный ID
                    prefs.edit().remove("pending_call_$phone").remove("pending_call_time_$phone").apply()
                } else {
                    android.util.Log.d("CallListenerService", "No call found in CallLog for $phone")
                }
            } catch (e: Exception) {
                android.util.Log.e("CallListenerService", "Error checking CallLog: ${e.message}")
            }
        }
    }

    private fun readCallLogForPhone(phone: String): Triple<String?, Long?, Long?>? {
        // Нормализуем номер (убираем пробелы, скобки, дефисы)
        val normalized = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
        
        try {
            val cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(
                    CallLog.Calls.TYPE,
                    CallLog.Calls.DURATION,
                    CallLog.Calls.DATE
                ),
                "${CallLog.Calls.NUMBER} LIKE ?",
                arrayOf("%$normalized%"),
                "${CallLog.Calls.DATE} DESC LIMIT 1"
            )
            
            cursor?.use {
                if (it.moveToFirst()) {
                    val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                    val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                    val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                    
                    // TYPE: 1=входящий, 2=исходящий, 3=пропущенный, 4=голосовая почта, 5=отклоненный (API 29+)
                    val status = when (type) {
                        CallLog.Calls.OUTGOING_TYPE -> "connected" // Исходящий - считаем дозвонился
                        CallLog.Calls.MISSED_TYPE -> "no_answer" // Пропущенный
                        CallLog.Calls.INCOMING_TYPE -> {
                            // Для входящих: если длительность 0 - не дозвонился, иначе - дозвонился
                            if (duration > 0) "connected" else "no_answer"
                        }
                        5 -> "rejected" // REJECTED_TYPE (доступен с API 29+)
                        else -> {
                            // Для других случаев (VOICEMAIL_TYPE=4 и т.д.)
                            if (duration == 0L) "no_answer" else "connected"
                        }
                    }
                    
                    return Triple(status, duration, date)
                }
            }
        } catch (e: SecurityException) {
            android.util.Log.w("CallListenerService", "No permission to read CallLog: ${e.message}")
        } catch (e: Exception) {
            android.util.Log.e("CallListenerService", "Error reading CallLog: ${e.message}")
        }
        return null
    }

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
        private const val CHANNEL_ID = "crmprofi_calls"
        private const val NOTIF_ID = 1001
        private const val NOTIF_CALL_ID = 1002

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

        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"
    }
}


