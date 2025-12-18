package ru.groupprofi.crmprofi.dialer

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import okhttp3.OkHttpClient
import okhttp3.Request
import org.json.JSONObject

class CallListenerService : Service() {
    private val http = OkHttpClient()
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
        val baseUrl = (intent?.getStringExtra(EXTRA_BASE_URL) ?: prefs.getString(KEY_BASE_URL, "") ?: "").trim().trimEnd('/')
        val token = intent?.getStringExtra(EXTRA_TOKEN) ?: prefs.getString(KEY_TOKEN, null)
        val deviceId = (intent?.getStringExtra(EXTRA_DEVICE_ID) ?: prefs.getString(KEY_DEVICE_ID, "") ?: "").trim()

        if (baseUrl.isBlank() || token.isNullOrBlank() || deviceId.isBlank()) {
            stopSelf()
            return START_NOT_STICKY
        }

        // Android 13+ (targetSdk 33+) may crash/startForeground fail if notifications are not allowed.
        if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            val granted = ContextCompat.checkSelfPermission(this, perm) == android.content.pm.PackageManager.PERMISSION_GRANTED
            if (!granted) {
                prefs.edit().putString(KEY_LAST_STATUS, "Разрешите уведомления для CRM ПРОФИ (иначе фон не работает).").apply()
                stopSelf()
                return START_NOT_STICKY
            }
        }

        prefs.edit()
            .putString(KEY_BASE_URL, baseUrl)
            .putString(KEY_TOKEN, token)
            .putString(KEY_DEVICE_ID, deviceId)
            .putString(KEY_LAST_STATUS, "Слушаю команды (в фоне)…")
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
                        val phone = pullCall(baseUrl, token, deviceId)
                        if (!phone.isNullOrBlank()) {
                            // 1) Всегда показываем уведомление с действием (работает и в фоне).
                            try {
                                showCallNotification(phone)
                            } catch (_: Throwable) {
                                // ignore
                            }
                            // 2) Если приложение на экране — открываем звонилку сразу.
                            if (AppState.isForeground) {
                                val uri = Uri.parse("tel:$phone")
                                val dial = Intent(Intent.ACTION_DIAL, uri).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                                startActivity(dial)
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

    private fun pullCall(baseUrl: String, token: String, deviceId: String): String? {
        val url = "$baseUrl/api/phone/calls/pull/?device_id=$deviceId"
        val req = Request.Builder()
            .url(url)
            .get()
            .addHeader("Authorization", "Bearer $token")
            .build()
        http.newCall(req).execute().use { res ->
            if (res.code == 204) return null
            val raw = res.body?.string() ?: ""
            if (!res.isSuccessful) return null
            val obj = JSONObject(raw)
            val phone = obj.optString("phone", "")
            return phone.ifBlank { null }
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
        private const val KEY_BASE_URL = "base_url"
        private const val KEY_TOKEN = "token"
        private const val KEY_DEVICE_ID = "device_id"
        const val KEY_LAST_STATUS = "last_status"

        const val EXTRA_BASE_URL = "base_url"
        const val EXTRA_TOKEN = "token"
        const val EXTRA_DEVICE_ID = "device_id"

        const val ACTION_STOP = "ru.groupprofi.crmprofi.dialer.STOP"
    }
}


