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
    private lateinit var logoutBtn: Button
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
        logoutBtn = findViewById(R.id.logoutBtn)
        statusEl = findViewById(R.id.status)

        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        accessToken = prefs.getString(KEY_ACCESS, null)
        refreshToken = prefs.getString(KEY_REFRESH, null)
        val savedUsername = prefs.getString(KEY_USERNAME, null)
        if (!savedUsername.isNullOrBlank() && !refreshToken.isNullOrBlank()) {
            accountStatusEl.text = "Аккаунт: $savedUsername (вход выполнен)"
            statusEl.text = "Статус: готово. device_id=$deviceId"
            // Скрываем поля логина/пароля если уже вошли
            usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            usernameEl.visibility = android.view.View.GONE
            passwordEl.visibility = android.view.View.GONE
            loginBtn.visibility = android.view.View.GONE
            logoutBtn.visibility = android.view.View.VISIBLE
            // Автоматически запускаем сервис после входа
            ensureCallLogPermissions()
            startListeningServiceAuto()
        } else {
            accountStatusEl.text = "Аккаунт: не выполнен вход"
            statusEl.text = "Статус: не подключено"
            logoutBtn.visibility = android.view.View.GONE
        }

        notifBtn.setOnClickListener { openNotificationSettings() }
        
        logoutBtn.setOnClickListener {
            getSharedPreferences(PREFS, MODE_PRIVATE).edit().clear().apply()
            accessToken = null
            refreshToken = null
            stopService(Intent(this, CallListenerService::class.java))
            accountStatusEl.text = "Аккаунт: не выполнен вход"
            statusEl.text = "Статус: не подключено"
            usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
            passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
            usernameEl.visibility = android.view.View.VISIBLE
            passwordEl.visibility = android.view.View.VISIBLE
            loginBtn.visibility = android.view.View.VISIBLE
            logoutBtn.visibility = android.view.View.GONE
            usernameEl.text.clear()
            passwordEl.text.clear()
        }

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
                        accountStatusEl.text = "Аккаунт: $username (вход выполнен)"
                        // Скрываем поля логина/пароля после успешного входа
                        usernameEl.visibility = android.view.View.GONE
                        passwordEl.visibility = android.view.View.GONE
                        usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
                        passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
                        loginBtn.visibility = android.view.View.GONE
                        logoutBtn.visibility = android.view.View.VISIBLE
                    }
                    setStatus("Статус: подключено. device_id=$deviceId")
                    // После успешного входа: запрашиваем права на статистику звонков и запускаем сервис
                    ensureCallLogPermissions()
                    startListeningServiceAuto()
                } catch (e: Exception) {
                    accessToken = null
                    refreshToken = null
                    prefs.edit().remove(KEY_ACCESS).remove(KEY_REFRESH).apply()
                    runOnUiThread {
                        accountStatusEl.text = "Аккаунт: не выполнен вход"
                        // Показываем поля логина/пароля при ошибке
                        usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                        passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                        usernameEl.visibility = android.view.View.VISIBLE
                        passwordEl.visibility = android.view.View.VISIBLE
                        loginBtn.visibility = android.view.View.VISIBLE
                        logoutBtn.visibility = android.view.View.GONE
                    }
                    setStatus("Ошибка: ${e.message}")
                }
            }
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

    /**
     * Права для корректной аналитики по фактическим звонкам:
     * READ_CALL_LOG и READ_PHONE_STATE. Запрашиваем мягко, без блокировки работы.
     */
    private fun ensureCallLogPermissions() {
        val needed = mutableListOf<String>()
        val callPerm = android.Manifest.permission.READ_CALL_LOG
        val phoneStatePerm = android.Manifest.permission.READ_PHONE_STATE

        if (ContextCompat.checkSelfPermission(this, callPerm) != PackageManager.PERMISSION_GRANTED) {
            needed += callPerm
        }
        if (ContextCompat.checkSelfPermission(this, phoneStatePerm) != PackageManager.PERMISSION_GRANTED) {
            needed += phoneStatePerm
        }

        if (needed.isEmpty()) return

        ActivityCompat.requestPermissions(this, needed.toTypedArray(), REQ_CALL_PERMS)
    }

    private fun startListeningServiceAuto() {
        val prefs = getSharedPreferences(PREFS, MODE_PRIVATE)
        val token = accessToken ?: prefs.getString(KEY_ACCESS, null)
        val refresh = refreshToken ?: prefs.getString(KEY_REFRESH, null)
        if (token.isNullOrBlank() || refresh.isNullOrBlank()) {
            setStatus("Статус: сначала войдите")
            return
        }

        // На Android 8+ foreground-service обязан показывать уведомление.
        // Если уведомления для приложения отключены, фон «не виден» и может работать нестабильно.
        if (!NotificationManagerCompat.from(this).areNotificationsEnabled()) {
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
        setStatus("Статус: слушаю команды (работает в фоне)")
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        when (requestCode) {
            100 -> {
                val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
                if (granted && pendingStartListening) {
                    pendingStartListening = false
                    startListeningServiceAuto()
                    return
                }
                pendingStartListening = false
                setStatus("Статус: без уведомлений фон не работает (разрешение отклонено)")
            }
            REQ_CALL_PERMS -> {
                // Если пользователь отказал — просто пишем статус, приложение продолжит работать,
                // но отчёт по фактическим звонкам может быть неполным.
                var anyDenied = false
                for (res in grantResults) {
                    if (res != PackageManager.PERMISSION_GRANTED) {
                        anyDenied = true
                        break
                    }
                }
                if (anyDenied) {
                    setStatus("Статус: без доступа к журналу звонков аналитика будет неполной")
                }
            }
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

        private const val REQ_CALL_PERMS = 200
    }
}


