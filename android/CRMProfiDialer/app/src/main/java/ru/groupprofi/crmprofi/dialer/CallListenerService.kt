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
                        val phone = pullCallWithRefresh(baseUrl, latestToken, latestRefresh, deviceId)
                        if (!phone.isNullOrBlank()) {
                            // 1) Всегда показываем уведомление с действием (работает и в фоне).
                            try {
                                showCallNotification(phone)
                            } catch (_: Throwable) {
                                // ignore
                            }
                            // 2) Если приложение на экране — открываем звонилку сразу.
                            if (AppState.isForeground) {
                                try {
                                    val uri = Uri.parse("tel:$phone")
                                    val dial = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                    // запуск activity делаем на main thread, чтобы не словить странные краши на прошивках
                                    Handler(Looper.getMainLooper()).post {
                                        try {
                                            startActivity(dial)
                                        } catch (_: Throwable) {
                                            // ignore — уведомление уже показали
                                        }
                                    }
                                } catch (_: Throwable) {
                                    // ignore — уведомление уже показали
                                }
                            }
                        }
                    } catch (_: Exception) {
                        // silent for MVP
                    }
                    delay(1500)
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

    private fun pullCallWithRefresh(baseUrl: String, token: String, refresh: String, deviceId: String): String? {
        val url = "$baseUrl/api/phone/calls/pull/?device_id=$deviceId"
        fun doPull(access: String): Pair<Int, String> {
            val req = Request.Builder()
                .url(url)
                .get()
                .addHeader("Authorization", "Bearer $access")
                .build()
            http.newCall(req).execute().use { res ->
                return Pair(res.code, res.body?.string() ?: "")
            }
        }

        // 1) try with current access
        val (code1, body1) = doPull(token)
        if (code1 == 204) return null
        if (code1 == 401) {
            // 2) refresh + retry once
            val newAccess = refreshAccess(baseUrl, refresh) ?: return null
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().putString(KEY_TOKEN, newAccess).apply()
            val (code2, body2) = doPull(newAccess)
            if (code2 == 204) return null
            if (code2 != 200) return null
            val obj2 = JSONObject(body2)
            val phone2 = obj2.optString("phone", "")
            return phone2.ifBlank { null }
        }
        if (code1 != 200) return null
        val obj = JSONObject(body1)
        val phone = obj.optString("phone", "")
        return phone.ifBlank { null }
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

    private fun showCallNotification(phone: String) {
        val uri = Uri.parse("tel:$phone")
        val dialIntent = Intent(Intent.ACTION_DIAL, uri)
        val pi = PendingIntent.getActivity(
            this,
            2,
            dialIntent,
            PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
        )
        val n = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.sym_action_call)
            .setContentTitle("Открыть номер")
            .setContentText(phone)
            .setPriority(NotificationCompat.PRIORITY_HIGH)
            .setAutoCancel(true)
            .setContentIntent(pi)
            .addAction(android.R.drawable.sym_action_call, "Открыть", pi)
            .build()
        val nm = getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        nm.notify(NOTIF_CALL_ID, n)
    }

    companion object {
        private const val CHANNEL_ID = "crmprofi_calls"
        private const val NOTIF_ID = 1001
        private const val NOTIF_CALL_ID = 1002

        private const val PREFS = "crmprofi_dialer"
        private const val KEY_TOKEN = "token"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_DEVICE_ID = "device_id"

        const val EXTRA_TOKEN = "token"
        const val EXTRA_REFRESH = "refresh"
        const val EXTRA_DEVICE_ID = "device_id"

        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"

        private const val BASE_URL = "https://crm.groupprofi.ru"
    }
}


