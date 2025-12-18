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

    private lateinit var baseUrlEl: EditText
    private lateinit var usernameEl: EditText
    private lateinit var passwordEl: EditText
    private lateinit var loginBtn: Button
    private lateinit var listenSwitch: MaterialSwitch
    private lateinit var statusEl: TextView

    private var accessToken: String? = null

    private val deviceId: String by lazy {
        // стабильный id устройства для привязки (используем ANDROID_ID как простой MVP)
        Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: "unknown"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        baseUrlEl = findViewById(R.id.baseUrl)
        usernameEl = findViewById(R.id.username)
        passwordEl = findViewById(R.id.password)
        loginBtn = findViewById(R.id.loginBtn)
        listenSwitch = findViewById(R.id.listenSwitch)
        statusEl = findViewById(R.id.status)

        listenSwitch.isEnabled = false

        loginBtn.setOnClickListener {
            CoroutineScope(Dispatchers.IO).launch {
                try {
                    val baseUrl = baseUrlEl.text.toString().trim().trimEnd('/')
                    val username = usernameEl.text.toString().trim()
                    val password = passwordEl.text.toString()
                    if (baseUrl.isEmpty() || username.isEmpty() || password.isEmpty()) {
                        setStatus("Статус: заполните baseUrl/логин/пароль")
                        return@launch
                    }

                    setStatus("Статус: логинюсь…")
                    val token = apiLogin(baseUrl, username, password)
                    accessToken = token
                    apiRegisterDevice(baseUrl, token, deviceId, android.os.Build.MODEL ?: "Android")

                    runOnUiThread {
                        listenSwitch.isEnabled = true
                        listenSwitch.isChecked = false
                    }
                    setStatus("Статус: подключено. device_id=$deviceId")
                } catch (e: Exception) {
                    accessToken = null
                    runOnUiThread {
                        listenSwitch.isEnabled = false
                        listenSwitch.isChecked = false
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
    }

    override fun onPause() {
        AppState.isForeground = false
        super.onPause()
    }

    private fun startListeningService() {
        val baseUrl = baseUrlEl.text.toString().trim().trimEnd('/')
        val token = accessToken
        if (token.isNullOrBlank() || baseUrl.isEmpty()) {
            listenSwitch.isChecked = false
            setStatus("Статус: сначала войдите")
            return
        }

        // Android 13+ требует разрешение на уведомления, иначе фоновые уведомления могут не отображаться.
        if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            if (ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this, arrayOf(perm), 100)
            }
        }

        val i = Intent(this, CallListenerService::class.java)
            .putExtra(CallListenerService.EXTRA_BASE_URL, baseUrl)
            .putExtra(CallListenerService.EXTRA_TOKEN, token)
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

    private fun setStatus(text: String) {
        runOnUiThread {
            statusEl.text = text
        }
    }

    private fun apiLogin(baseUrl: String, username: String, password: String): String {
        val url = "$baseUrl/api/token/"
        val bodyJson = JSONObject()
            .put("username", username)
            .put("password", password)
            .toString()
        val req = Request.Builder()
            .url(url)
            .post(bodyJson.toRequestBody(jsonMedia))
            .build()
        http.newCall(req).execute().use { res ->
            val raw = res.body?.string() ?: ""
            if (!res.isSuccessful) throw RuntimeException("Login failed: HTTP ${res.code} $raw")
            val obj = JSONObject(raw)
            return obj.getString("access")
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
        http.newCall(req).execute().use { res ->
            val raw = res.body?.string() ?: ""
            if (!res.isSuccessful) throw RuntimeException("Register device failed: HTTP ${res.code} $raw")
        }
    }

    // Polling реализован в ForegroundService (CallListenerService).
}


