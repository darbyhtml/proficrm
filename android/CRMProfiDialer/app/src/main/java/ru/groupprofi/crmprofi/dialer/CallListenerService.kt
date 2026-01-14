package ru.groupprofi.crmprofi.dialer

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
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

class CallListenerService : Service() {
    private val http = OkHttpClient()
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loopJob: Job? = null
    private var heartbeatCounter: Int = 0
    private val timeFmt = SimpleDateFormat("HH:mm:ss", Locale.getDefault())

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP -> {
                stopSelf()
                return START_NOT_STICKY
            }
        }

        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
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
                        
                        // Обработка ошибок авторизации - останавливаем сервис
                        if (code == 401) {
                            updateListeningNotification("Ошибка: требуется повторный вход")
                            delay(5000) // Даем время увидеть сообщение
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
                                                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                                    .putString("pending_call_$phone", callRequestId)
                                                    .putLong("pending_call_time_$phone", System.currentTimeMillis())
                                                    .apply()
                                                // Проверяем CallLog через 5 секунд
                                                val currentToken = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_TOKEN, null) ?: token
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
                                    getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                                        .putString("pending_call_$phone", callRequestId)
                                        .putLong("pending_call_time_$phone", System.currentTimeMillis())
                                        .apply()
                                    val currentToken = getSharedPreferences(PREFS, MODE_PRIVATE).getString(KEY_TOKEN, null) ?: token
                                    checkCallLogAndSend(BASE_URL, currentToken, phone)
                                }
                            }
                        } else {
                            android.util.Log.d("CallListenerService", "Phone is blank, skipping")
                        }
                    } catch (_: Exception) {
                        // silent for MVP
                    }
                    // В рабочие часы реагируем максимально быстро, вне их — чуть реже
                    val delayMs = if (isWorkingHours()) 1500L else 5000L
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
            val newAccess = refreshAccess(baseUrl, refresh)
            if (newAccess == null) {
                // Refresh token истек - нужно перелогиниться
                // Очищаем токены, чтобы пользователь перелогинился
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
                    .remove(KEY_TOKEN)
                    .remove(KEY_REFRESH)
                    .apply()
                return Pair(401, null)
            }
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_TOKEN, newAccess).apply()
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
                getSharedPreferences(PREFS, MODE_PRIVATE).edit()
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

    private fun refreshAccess(baseUrl: String, refresh: String): String? {
        val url = "$baseUrl/api/token/refresh/"
        val bodyJson = JSONObject().put("refresh", refresh).toString()
        val req = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()
        http.newCall(req).execute().use { res ->
            val raw = res.body?.string() ?: ""
            if (!res.isSuccessful) return null
            return JSONObject(raw).optString("access", "").ifBlank { null }
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
                val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
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
            
            http.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (res.isSuccessful) {
                    android.util.Log.i("CallListenerService", "Call info sent successfully to CRM")
                } else {
                    android.util.Log.w("CallListenerService", "Failed to send call info: HTTP ${res.code}, $raw")
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("CallListenerService", "Error sending call info: ${e.message}")
        }
    }

    /**
     * Лёгкий heartbeat: раз в несколько циклов отправляем код последнего опроса и время.
     * Не влияет на основную логику, ошибки игнорируются.
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

            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("device_name", android.os.Build.MODEL ?: "Android")
                put("app_version", BuildConfig.VERSION_NAME)
                put("last_poll_code", lastPollCode)
                put("last_poll_at", iso)
            }.toString()

            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()

            http.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    android.util.Log.w("CallListenerService", "Heartbeat failed: HTTP ${res.code}")
                }
            }
        } catch (e: Exception) {
            android.util.Log.w("CallListenerService", "Heartbeat error: ${e.message}")
        }
    }

    /**
     * Отправка простой телеметрии по latency/кодам ответа для /api/phone/calls/pull/.
     * Используем batch-формат с одним элементом.
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

            http.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    android.util.Log.w("CallListenerService", "Telemetry latency failed: HTTP ${res.code}")
                }
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


