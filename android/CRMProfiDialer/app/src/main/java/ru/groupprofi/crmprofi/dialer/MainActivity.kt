package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import com.google.android.material.materialswitch.MaterialSwitch
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject

class MainActivity : AppCompatActivity() {
    private val http = OkHttpClient()
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()

    private lateinit var accountStatusEl: TextView
    private lateinit var usernameEl: EditText
    private lateinit var passwordEl: EditText
    private lateinit var loginBtn: Button
    private lateinit var notifBtn: Button
    private lateinit var listenSwitch: MaterialSwitch
    private lateinit var statusEl: TextView

    private var accessToken: String? = null
    private var refreshToken: String? = null
    private var pendingStartListening: Boolean = false

    private val deviceId: String by lazy {
        // стабильный id устройства для привязки (используем ANDROID_ID как простой MVP)
        Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: "unknown"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        accountStatusEl = findViewById(R.id.accountStatus)
        usernameEl = findViewById(R.id.username)
        passwordEl = findViewById(R.id.password)
        loginBtn = findViewById(R.id.loginBtn)
        notifBtn = findViewById(R.id.notifBtn)
        listenSwitch = findViewById(R.id.listenSwitch)
        statusEl = findViewById(R.id.status)

        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        accessToken = prefs.getString(KEY_ACCESS, null)
        refreshToken = prefs.getString(KEY_REFRESH, null)
        val savedUsername = prefs.getString(KEY_USERNAME, null)
        if (!savedUsername.isNullOrBlank() && !refreshToken.isNullOrBlank()) {
            accountStatusEl.text = "Аккаунт: $savedUsername (вход выполнен)"
            statusEl.text = "Статус: готово. device_id=$deviceId"
        } else {
            accountStatusEl.text = "Аккаунт: не выполнен вход"
            statusEl.text = "Статус: не подключено"
        }

        listenSwitch.isEnabled = false
        // Prevent state-restore crash loops: we don't auto-start listening on launch.
        listenSwitch.isSaveEnabled = false
        listenSwitch.setOnCheckedChangeListener(null)
        listenSwitch.isChecked = false

        notifBtn.setOnClickListener { openNotificationSettings() }

        loginBtn.setOnClickListener {
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    val username = usernameEl.text.toString().trim()
                    val password = passwordEl.text.toString()
                    if (username.isEmpty() || password.isEmpty()) {
                        setStatus("Статус: заполните логин/пароль")
                        return@launch
                    }

                    setStatus("Статус: логинюсь…")
                    val tokens = apiLogin(BASE_URL, username, password)
                    accessToken = tokens.first
                    refreshToken = tokens.second

                    prefs.edit()
                        .putString(KEY_USERNAME, username)
                        .putString(KEY_ACCESS, accessToken)
                        .putString(KEY_REFRESH, refreshToken)
                        .putString(KEY_DEVICE_ID, deviceId)
                        .apply()

                    apiRegisterDevice(BASE_URL, accessToken!!, deviceId, android.os.Build.MODEL ?: "Android")

                    runOnUiThread {
                        listenSwitch.isEnabled = true
                        listenSwitch.isChecked = false
                        accountStatusEl.text = "Аккаунт: $username (вход выполнен)"
                    }
                    setStatus("Статус: подключено. device_id=$deviceId")
                } catch (e: Exception) {
                    accessToken = null
                    refreshToken = null
                    prefs.edit().remove(KEY_ACCESS).remove(KEY_REFRESH).apply()
                    runOnUiThread {
                        listenSwitch.isEnabled = false
                        listenSwitch.isChecked = false
                        accountStatusEl.text = "Аккаунт: не выполнен вход"
                    }
                    setStatus("Ошибка: ${e.message}")
                }
            }
        }

        listenSwitch.setOnCheckedChangeListener { _, isChecked ->
            if (isChecked) startListeningService() else stopListeningService()
        }
    }

    override fun onResume() {
        super.onResume()
        AppState.isForeground = true

        // Лаконичный индикатор "доходит ли телефон до сервера"
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val at = prefs.getString(CallListenerService.KEY_LAST_POLL_AT, null)
        val code = prefs.getInt(CallListenerService.KEY_LAST_POLL_CODE, -1)
        if (!at.isNullOrBlank() && code != -1) {
            statusEl.text = "Статус: опрос $code · $at"
        }
    }

    override fun onPause() {
        AppState.isForeground = false
        super.onPause()
    }

    private fun startListeningService() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val token = accessToken ?: prefs.getString(KEY_ACCESS, null)
        val refresh = refreshToken ?: prefs.getString(KEY_REFRESH, null)
        if (token.isNullOrBlank() || refresh.isNullOrBlank()) {
            listenSwitch.isChecked = false
            setStatus("Статус: сначала войдите")
            return
        }

        // На Android 8+ foreground-service обязан показывать уведомление.
        // Если уведомления для приложения отключены, фон «не виден» и может работать нестабильно.
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) {
            listenSwitch.isChecked = false
            setStatus("Статус: включите уведомления для приложения (иначе фон не работает)")
            openNotificationSettings()
            return
        }

        // Android 13+ требует разрешение на уведомления. Запускаем сервис ТОЛЬКО после выдачи.
        if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            if (ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED) {
                pendingStartListening = true
                ActivityCompat.requestPermissions(this, arrayOf(perm), 100)
                setStatus("Статус: нужно разрешение на уведомления (Android 13+)")
                return
            }
        }

        val i = Intent(this, CallListenerService::class.java)
            .putExtra(CallListenerService.EXTRA_TOKEN, token)
            .putExtra(CallListenerService.EXTRA_REFRESH, refresh)
            .putExtra(CallListenerService.EXTRA_DEVICE_ID, deviceId)

        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(i)
        } else {
            startService(i)
        }
        setStatus("Статус: слушаю команды (в фоне тоже)…")
    }

    private fun stopListeningService() {
        stopService(Intent(this, CallListenerService::class.java))
        setStatus("Статус: подключено (слушание выключено)")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == 100) {
            val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
            if (granted && pendingStartListening) {
                pendingStartListening = false
                startListeningService()
                return
            }
            pendingStartListening = false
            listenSwitch.setOnCheckedChangeListener(null)
            listenSwitch.isChecked = false
            listenSwitch.setOnCheckedChangeListener { _, isChecked ->
                if (isChecked) startListeningService() else stopListeningService()
            }
            setStatus("Статус: без уведомлений фон не работает (разрешение отклонено)")
        }
    }

    private fun setStatus(text: String) {
        runOnUiThread {
            statusEl.text = text
        }
    }

    private fun apiLogin(baseUrl: String, username: String, password: String): Pair<String, String> {
        val url = "$baseUrl/api/token/"
        val bodyJson = JSONObject()
            .put("username", username)
            .put("password", password)
            .toString()
        val req = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()
        try {
            http.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (!res.isSuccessful) {
                    val errorMsg = try {
                        val errorObj = JSONObject(raw)
                        errorObj.optString("detail", "Ошибка входа")
                    } catch (_: Exception) {
                        "Ошибка входа: HTTP ${res.code}"
                    }
                    throw RuntimeException(errorMsg)
                }
                val obj = JSONObject(raw)
                val access = obj.optString("access", "")
                val refresh = obj.optString("refresh", "")
                if (access.isBlank() || refresh.isBlank()) {
                    throw RuntimeException("Неверный формат ответа сервера")
                }
                return Pair(access, refresh)
            }
        } catch (e: java.net.UnknownHostException) {
            throw RuntimeException("Нет подключения к интернету")
        } catch (e: java.net.SocketTimeoutException) {
            throw RuntimeException("Превышено время ожидания ответа")
        } catch (e: Exception) {
            if (e is RuntimeException) throw e
            throw RuntimeException("Ошибка сети: ${e.message}")
        }
    }

    private fun apiRegisterDevice(baseUrl: String, token: String, deviceId: String, deviceName: String) {
        val url = "$baseUrl/api/phone/devices/register/"
        val bodyJson = JSONObject()
            .put("device_id", deviceId)
            .put("device_name", deviceName)
            .toString()
        val req = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .addHeader("Authorization", "Bearer $token")
            .build()
        try {
            http.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (!res.isSuccessful) {
                    // Регистрация устройства не критична, логируем но не падаем
                    android.util.Log.w("MainActivity", "Register device failed: HTTP ${res.code} $raw")
                }
            }
        } catch (e: Exception) {
            // Регистрация устройства не критична, логируем но не падаем
            android.util.Log.w("MainActivity", "Register device error: ${e.message}")
        }
    }

    private fun openNotificationSettings() {
        try {
            val i = Intent().apply {
                action = Settings.ACTION_APP_NOTIFICATION_SETTINGS
                putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(i)
        } catch (_: Exception) {
            // ignore
        }
    }

    // Polling реализован в ForegroundService (CallListenerService).

    companion object {
        private const val BASE_URL = "https://crm.groupprofi.ru"
        private const val PREFS = "crmprofi_dialer"
        private const val KEY_USERNAME = "username"
        private const val KEY_ACCESS = "access"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_DEVICE_ID = "device_id"
    }
}


