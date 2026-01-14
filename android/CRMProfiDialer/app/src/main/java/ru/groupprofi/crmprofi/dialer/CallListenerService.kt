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
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import ru.groupprofi.crmprofi.dialer.logs.LogCollector
import ru.groupprofi.crmprofi.dialer.logs.LogSender
import ru.groupprofi.crmprofi.dialer.logs.LogInterceptor

class CallListenerService : Service() {
    private val http = OkHttpClient()
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loopJob: Job? = null
    private var heartbeatCounter: Int = 0
    private var queueFlushCounter: Int = 0
    private var logSendCounter: Int = 0
    private var consecutiveEmptyPolls: Int = 0 // Счетчик пустых опросов для адаптивной частоты
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private val queueManager = QueueManager(this)
    private val logCollector = LogCollector()
    private val logSender = LogSender(this, http, queueManager)
    private val random = java.util.Random()

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }

        val prefs = securePrefs()
        val baseUrl = BASE_URL
        val token = intent?.getStringExtra(EXTRA_TOKEN) ?: prefs.getString(KEY_TOKEN, null)
        val refresh = intent?.getStringExtra(EXTRA_REFRESH) ?: prefs.getString(KEY_REFRESH, null)
        val deviceId = (intent?.getStringExtra(EXTRA_DEVICE_ID) ?: prefs.getString(KEY_DEVICE_ID, "") ?: "").trim()

        if (token.isNullOrBlank() || refresh.isNullOrBlank() || deviceId.isBlank()) {
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

        prefs.edit()
            .putString(KEY_TOKEN, token)
            .putString(KEY_REFRESH, refresh)
            .putString(KEY_DEVICE_ID, deviceId)
            .apply()

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
                        val latestToken = prefs.getString(KEY_TOKEN, null) ?: token
                        val latestRefresh = prefs.getString(KEY_REFRESH, null) ?: refresh
                        val (code, result) = pullCallWithRefresh(baseUrl, latestToken, latestRefresh, deviceId)
                        val phone = result?.first
                        val callRequestId = result?.second
                        val nowDate = Date()
                        val nowStr = timeFmt.format(nowDate)
                        prefs.edit()
                            .putString(KEY_LAST_POLL_AT, nowStr)
                            .putInt(KEY_LAST_POLL_CODE, code)
                            .apply()
                        
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
                                sendHeartbeat(
                                    baseUrl = BASE_URL,
                                    token = latestToken,
                                    deviceId = deviceId,
                                    lastPollCode = code,
                                    lastPollAt = nowDate.time
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
                                val sentCount = queueManager.flushQueue(BASE_URL, latestToken, http)
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
                                    logSender.sendLogBundle(BASE_URL, latestToken, deviceId, bundle)
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
                                                val currentToken = securePrefs().getString(KEY_TOKEN, null) ?: token
                                                checkCallLogAndSend(BASE_URL, currentToken, phone)
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
                                    val currentToken = securePrefs().getString(KEY_TOKEN, null) ?: token
                                    checkCallLogAndSend(BASE_URL, currentToken, phone)
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

    private fun pullCallWithRefresh(baseUrl: String, token: String, refresh: String, deviceId: String): Pair<Int, Pair<String?, String?>?> {
        val url = "$baseUrl/api/phone/calls/pull/?device_id=$deviceId"
        fun doPull(access: String): Pair<Int, String?> {
            val req = Request.Builder()
                .url(url)
                .get()
                .addHeader("Authorization", "Bearer $access")
                .build()
            val start = System.currentTimeMillis()
            var code = 0
            var body: String? = null
            try {
                http.newCall(req).execute().use { res ->
                    code = res.code
                    body = res.body?.string()
                }
            } catch (e: java.net.UnknownHostException) {
                // Нет интернета - возвращаем специальный код
                code = 0
            } catch (e: java.net.SocketTimeoutException) {
                // Таймаут - возвращаем специальный код
                code = 0
            } catch (_: Exception) {
                // Другие сетевые ошибки
                code = 0
            } finally {
                val duration = System.currentTimeMillis() - start
                // Лёгкая телеметрия по latency/ошибкам, не влияет на основную логику
                try {
                    sendTelemetryLatency(
                        baseUrl = baseUrl,
                        access = access,
                        deviceId = deviceId,
                        endpointPath = "/api/phone/calls/pull/",
                        httpCode = if (code == 0) null else code,
                        valueMs = duration
                    )
                } catch (_: Exception) {
                    // ignore
                }
            }
            return Pair(code, body)
        }

        // 1) try with current access
        val (code1, body1) = doPull(token)
        android.util.Log.d("CallListenerService", "PullCall: code=$code1, body length=${body1?.length ?: 0}")
        if (code1 == 0) return Pair(0, null) // Сетевая ошибка
        if (code1 == 204) {
            android.util.Log.d("CallListenerService", "PullCall: No pending calls (204)")
            return Pair(204, null)
        }
        if (code1 == 401) {
            android.util.Log.w("CallListenerService", "PullCall: Unauthorized (401), refreshing token")
            // 2) refresh + retry once
            try {
                val newAccess = refreshAccess(baseUrl, refresh)
                if (newAccess == null) {
                    // Refresh token истек - нужно перелогиниться
                    // Очищаем токены, чтобы пользователь перелогинился
                    android.util.Log.w("CallListenerService", "Refresh token expired, clearing tokens")
                    securePrefs().edit()
                        .remove(KEY_TOKEN)
                        .remove(KEY_REFRESH)
                        .apply()
                    return Pair(401, null)
                }
                // Сохраняем новый access token
                securePrefs().edit().putString(KEY_TOKEN, newAccess).apply()
                android.util.Log.i("CallListenerService", "Access token refreshed successfully")
                val (code2, body2) = doPull(newAccess)
                if (code2 == 0) return Pair(0, null) // Сетевая ошибка
                if (code2 == 204) return Pair(204, null)
                if (code2 != 200) return Pair(code2, null)
                val body2Str = body2 ?: return Pair(code2, null)
                try {
                    val obj2 = JSONObject(body2Str)
                    val phone2 = obj2.optString("phone", "")
                    val callRequestId2 = obj2.optString("id", "")
                    android.util.Log.i("CallListenerService", "PullCall: Received call command (after refresh), phone=$phone2, id=$callRequestId2")
                    return Pair(200, if (phone2.isNotBlank()) Pair(phone2, callRequestId2) else null)
                } catch (e: Exception) {
                    android.util.Log.e("CallListenerService", "PullCall: JSON parse error (after refresh): ${e.message}, body: $body2Str")
                    return Pair(code2, null)
                }
            } catch (e: RuntimeException) {
                // Сетевая ошибка при refresh - не критично, не очищаем токены
                // Просто возвращаем сетевую ошибку, сервис продолжит работу
                android.util.Log.w("CallListenerService", "Refresh token network error: ${e.message}, will retry later")
                return Pair(0, null) // Сетевая ошибка, не 401
            }
        }
        if (code1 != 200) {
            android.util.Log.w("CallListenerService", "PullCall: Unexpected code $code1, body: ${body1?.take(200)}")
            return Pair(code1, null)
        }
        val body1Str = body1 ?: return Pair(code1, null)
        try {
            val obj = JSONObject(body1Str)
            val phone = obj.optString("phone", "")
            val callRequestId = obj.optString("id", "")
            android.util.Log.i("CallListenerService", "PullCall: Received call command, phone=$phone, id=$callRequestId")
            // Сохраняем call_request_id для последующей отправки данных о звонке
            if (callRequestId.isNotBlank()) {
                securePrefs().edit()
                    .putString("pending_call_${phone}", callRequestId)
                    .putLong("pending_call_time_${phone}", System.currentTimeMillis())
                    .apply()
            }
            return Pair(200, if (phone.isNotBlank()) Pair(phone, callRequestId) else null)
        } catch (e: Exception) {
            android.util.Log.e("CallListenerService", "PullCall: JSON parse error: ${e.message}, body: $body1Str")
            return Pair(code1, null)
        }
    }

    /**
     * Обновление access token через refresh token.
     * Возвращает:
     * - новый access token (String) при успехе
     * - null при истечении refresh token (401/403) - требуется повторный вход
     * - выбрасывает RuntimeException при сетевых ошибках (не критично, можно повторить позже)
     */
    private fun refreshAccess(baseUrl: String, refresh: String): String? {
        val url = "$baseUrl/api/token/refresh/"
        val bodyJson = JSONObject().put("refresh", refresh).toString()
        val req = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()
        try {
            http.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (!res.isSuccessful) {
                    // 401/403 = refresh token истек, требуется повторный вход
                    if (res.code == 401 || res.code == 403) {
                        android.util.Log.w("CallListenerService", "Refresh token expired: HTTP ${res.code}")
                        return null
                    }
                    // Другие ошибки сервера (500, 502 и т.д.) - временная проблема, не очищаем токены
                    android.util.Log.w("CallListenerService", "Refresh token server error: HTTP ${res.code}")
                    throw RuntimeException("Server error: HTTP ${res.code}")
                }
                val access = JSONObject(raw).optString("access", "").ifBlank { null }
                if (access == null) {
                    android.util.Log.w("CallListenerService", "Refresh token response missing access token")
                    return null
                }
                return access
            }
        } catch (e: java.net.UnknownHostException) {
            android.util.Log.w("CallListenerService", "Refresh token network error: no internet")
            throw RuntimeException("No internet connection")
        } catch (e: java.net.SocketTimeoutException) {
            android.util.Log.w("CallListenerService", "Refresh token network error: timeout")
            throw RuntimeException("Request timeout")
        } catch (e: RuntimeException) {
            // Пробрасываем дальше
            throw e
        } catch (e: Exception) {
            android.util.Log.w("CallListenerService", "Refresh token error: ${e.message}")
            throw RuntimeException("Network error: ${e.message}")
        }
    }

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

    private fun checkCallLogAndSend(baseUrl: String, token: String, phone: String) {
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
                    sendCallInfoToCRM(baseUrl, token, callRequestId, callInfo.first, callInfo.third, callInfo.second)
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

    private fun sendCallInfoToCRM(baseUrl: String, token: String, callRequestId: String, status: String?, startedAt: Long?, duration: Long?) {
        try {
            val url = "$baseUrl/api/phone/calls/update/"
            val bodyJson = JSONObject().apply {
                put("call_request_id", callRequestId)
                if (status != null) put("call_status", status)
                if (startedAt != null) {
                    // Конвертируем миллисекунды в ISO datetime
                    val date = java.util.Date(startedAt)
                    val sdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
                    sdf.timeZone = java.util.TimeZone.getTimeZone("UTC")
                    put("call_started_at", sdf.format(date))
                }
                if (duration != null) put("call_duration_seconds", duration.toInt())
            }.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            try {
                http.newCall(req).execute().use { res ->
                    val raw = res.body?.string() ?: ""
                    if (res.isSuccessful) {
                        android.util.Log.i("CallListenerService", "Call info sent successfully to CRM")
                    } else {
                        android.util.Log.w("CallListenerService", "Failed to send call info: HTTP ${res.code}, $raw")
                        // При ошибке сервера (не сети) - добавляем в очередь для повторной отправки
                        if (res.code in 500..599) {
                            queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
                            android.util.Log.i("CallListenerService", "Call info queued for retry (server error)")
                        }
                    }
                }
            } catch (e: java.net.UnknownHostException) {
                // Нет интернета - добавляем в очередь
                android.util.Log.w("CallListenerService", "No internet, queuing call info")
                queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            } catch (e: java.net.SocketTimeoutException) {
                // Таймаут - добавляем в очередь
                android.util.Log.w("CallListenerService", "Timeout, queuing call info")
                queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            } catch (e: java.io.IOException) {
                // Другие сетевые ошибки - добавляем в очередь
                android.util.Log.w("CallListenerService", "Network error, queuing call info: ${e.message}")
                queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            }
        } catch (e: Exception) {
            android.util.Log.e("CallListenerService", "Error preparing call info: ${e.message}")
        }
    }

    /**
     * Лёгкий heartbeat: раз в несколько циклов отправляем код последнего опроса и время.
     * Не влияет на основную логику, ошибки игнорируются, но при сетевых ошибках добавляем в очередь.
     */
    private fun sendHeartbeat(
        baseUrl: String,
        token: String,
        deviceId: String,
        lastPollCode: Int,
        lastPollAt: Long
    ) {
        try {
            val url = "$baseUrl/api/phone/devices/heartbeat/"
            val iso = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US).apply {
                timeZone = java.util.TimeZone.getTimeZone("UTC")
            }.format(java.util.Date(lastPollAt))

            // Проверяем, используется ли шифрование
            val encryptionEnabled = MainActivity.isEncryptionEnabled(this)
            
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("device_name", android.os.Build.MODEL ?: "Android")
                put("app_version", BuildConfig.VERSION_NAME)
                put("last_poll_code", lastPollCode)
                put("last_poll_at", iso)
                put("encryption_enabled", encryptionEnabled) // Отправляем статус шифрования в CRM
            }.toString()

            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()

            try {
                http.newCall(req).execute().use { res ->
                    if (!res.isSuccessful) {
                        android.util.Log.w("CallListenerService", "Heartbeat failed: HTTP ${res.code}")
                        // При ошибке сервера - добавляем в очередь
                        if (res.code in 500..599) {
                            queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
                        }
                    }
                }
            } catch (e: java.net.UnknownHostException) {
                // Нет интернета - добавляем в очередь
                queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
            } catch (e: java.net.SocketTimeoutException) {
                // Таймаут - добавляем в очередь
                queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
            } catch (e: java.io.IOException) {
                // Другие сетевые ошибки - добавляем в очередь
                queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
            }
        } catch (e: Exception) {
            android.util.Log.w("CallListenerService", "Heartbeat error: ${e.message}")
        }
    }

    /**
     * Отправка простой телеметрии по latency/кодам ответа для /api/phone/calls/pull/.
     * Используем batch-формат с одним элементом.
     * При сетевых ошибках добавляем в очередь.
     */
    private fun sendTelemetryLatency(
        baseUrl: String,
        access: String,
        deviceId: String,
        endpointPath: String,
        httpCode: Int?,
        valueMs: Long
    ) {
        try {
            val url = "$baseUrl/api/phone/telemetry/"
            val item = JSONObject().apply {
                put("type", "latency")
                put("endpoint", endpointPath)
                if (httpCode != null) put("http_code", httpCode)
                put("value_ms", valueMs.toInt())
            }
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("items", org.json.JSONArray().put(item))
            }.toString()

            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $access")
                .build()

            try {
                http.newCall(req).execute().use { res ->
                    if (!res.isSuccessful) {
                        android.util.Log.w("CallListenerService", "Telemetry latency failed: HTTP ${res.code}")
                        // При ошибке сервера - добавляем в очередь
                        if (res.code in 500..599) {
                            queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
                        }
                    }
                }
            } catch (e: java.net.UnknownHostException) {
                // Нет интернета - добавляем в очередь
                queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
            } catch (e: java.net.SocketTimeoutException) {
                // Таймаут - добавляем в очередь
                queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
            } catch (e: java.io.IOException) {
                // Другие сетевые ошибки - добавляем в очередь
                queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
            }
        } catch (e: Exception) {
            android.util.Log.w("CallListenerService", "Telemetry latency error: ${e.message}")
        }
    }

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
        private const val KEY_TOKEN = "token"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_DEVICE_ID = "device_id"
        const val KEY_LAST_POLL_AT = "last_poll_at"
        const val KEY_LAST_POLL_CODE = "last_poll_code"

        const val EXTRA_TOKEN = "token"
        const val EXTRA_REFRESH = "refresh"
        const val EXTRA_DEVICE_ID = "device_id"

        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"

        private const val BASE_URL = "https://crm.groupprofi.ru"
    }
}


