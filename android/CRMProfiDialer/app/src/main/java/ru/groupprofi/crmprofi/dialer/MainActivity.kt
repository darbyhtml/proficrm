package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.EditText
import android.widget.Switch
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
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
    private lateinit var listenSwitch: Switch
    private lateinit var statusEl: TextView

    private var accessToken: String? = null
    private var pollJob: Job? = null

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
            if (isChecked) startPolling() else stopPolling()
        }
    }

    private fun startPolling() {
        val baseUrl = baseUrlEl.text.toString().trim().trimEnd('/')
        val token = accessToken
        if (token.isNullOrBlank() || baseUrl.isEmpty()) {
            listenSwitch.isChecked = false
            setStatus("Статус: сначала войдите")
            return
        }
        stopPolling()
        pollJob = CoroutineScope(Dispatchers.IO).launch {
            setStatus("Статус: слушаю команды…")
            while (true) {
                try {
                    val phone = apiPullCall(baseUrl, token, deviceId)
                    if (!phone.isNullOrBlank()) {
                        runOnUiThread {
                            val uri = Uri.parse("tel:$phone")
                            startActivity(Intent(Intent.ACTION_DIAL, uri))
                        }
                    }
                } catch (e: Exception) {
                    // не падаем, просто показываем статус и продолжаем
                    setStatus("Ошибка polling: ${e.message}")
                }
                delay(1500)
            }
        }
    }

    private fun stopPolling() {
        pollJob?.cancel()
        pollJob = null
        if (accessToken.isNullOrBlank()) {
            setStatus("Статус: не подключено")
        } else {
            setStatus("Статус: подключено (polling выключен)")
        }
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

    private fun apiPullCall(baseUrl: String, token: String, deviceId: String): String? {
        val url = "$baseUrl/api/phone/calls/pull/?device_id=$deviceId"
        val req = Request.Builder()
            .url(url)
            .get()
            .addHeader("Authorization", "Bearer $token")
            .build()
        http.newCall(req).execute().use { res ->
            if (res.code == 204) return null
            val raw = res.body?.string() ?: ""
            if (!res.isSuccessful) throw RuntimeException("Pull call failed: HTTP ${res.code} $raw")
            val obj = JSONObject(raw)
            // org.json optString требует defaultValue:String. Возвращаем null, если поля нет/пусто.
            val phone = obj.optString("phone", "")
            return phone.ifBlank { null }
        }
    }
}


