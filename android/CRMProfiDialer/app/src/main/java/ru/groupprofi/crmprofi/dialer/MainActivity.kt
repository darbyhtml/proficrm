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
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.network.ApiClient

class MainActivity : AppCompatActivity() {
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient

    private lateinit var accountStatusEl: TextView
    private lateinit var usernameEl: EditText
    private lateinit var passwordEl: EditText
    private lateinit var loginBtn: Button
    private lateinit var qrLoginBtn: Button
    private lateinit var notifBtn: Button
    private lateinit var logoutBtn: Button
    private lateinit var statusEl: TextView
    private lateinit var queueStatusEl: TextView

    private var pendingStartListening: Boolean = false

    private val deviceId: String by lazy {
        // стабильный id устройства для привязки (используем ANDROID_ID как простой MVP)
        Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: "unknown"
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        // Инициализируем TokenManager и ApiClient
        tokenManager = TokenManager.getInstance(this)
        apiClient = ApiClient.getInstance(this)
        
        // Сохраняем device_id в TokenManager (если еще не сохранен)
        val currentDeviceId = tokenManager.getDeviceId()
        if (currentDeviceId.isNullOrBlank()) {
            tokenManager.saveDeviceId(deviceId)
        }

        accountStatusEl = findViewById(R.id.accountStatus)
        usernameEl = findViewById(R.id.username)
        passwordEl = findViewById(R.id.password)
        loginBtn = findViewById(R.id.loginBtn)
        qrLoginBtn = findViewById(R.id.qrLoginBtn)
        notifBtn = findViewById(R.id.notifBtn)
        logoutBtn = findViewById(R.id.logoutBtn)
        statusEl = findViewById(R.id.status)
        queueStatusEl = findViewById(R.id.queueStatus)

        val savedUsername = tokenManager.getUsername()
        if (!savedUsername.isNullOrBlank() && tokenManager.hasTokens()) {
            accountStatusEl.text = "Аккаунт: $savedUsername (вход выполнен)"
            statusEl.text = "Статус: готово. device_id=$deviceId"
            // Скрываем поля логина/пароля если уже вошли
            usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            usernameEl.visibility = android.view.View.GONE
            passwordEl.visibility = android.view.View.GONE
            loginBtn.visibility = android.view.View.GONE
            qrLoginBtn.visibility = android.view.View.GONE
            logoutBtn.visibility = android.view.View.VISIBLE
            // Автоматически запускаем сервис после входа
            ensureCallLogPermissions()
            startListeningServiceAuto()
        } else {
            accountStatusEl.text = "Аккаунт: не выполнен вход"
            statusEl.text = "Статус: не подключено"
            logoutBtn.visibility = android.view.View.GONE
            qrLoginBtn.visibility = android.view.View.VISIBLE
        }

        notifBtn.setOnClickListener { openNotificationSettings() }
        
        qrLoginBtn.setOnClickListener {
            // Открываем QRLoginActivity для сканирования QR-кода
            val intent = Intent(this, QRLoginActivity::class.java)
            startActivity(intent)
        }
        
        logoutBtn.setOnClickListener {
            tokenManager.clearAll()
            stopService(Intent(this, CallListenerService::class.java))
            accountStatusEl.text = "Аккаунт: не выполнен вход"
            statusEl.text = "Статус: не подключено"
            usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
            passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
            usernameEl.visibility = android.view.View.VISIBLE
            passwordEl.visibility = android.view.View.VISIBLE
            loginBtn.visibility = android.view.View.VISIBLE
            qrLoginBtn.visibility = android.view.View.VISIBLE
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
                    val loginResult = apiClient.login(username, password)
                    
                    when (loginResult) {
                        is ApiClient.Result.Success -> {
                            val (access, refresh) = loginResult.data
                            tokenManager.saveTokens(access, refresh, username)
                            tokenManager.saveDeviceId(deviceId)
                            
                            // Регистрация устройства (не критична)
                            apiClient.registerDevice(deviceId, android.os.Build.MODEL ?: "Android")

                    runOnUiThread {
                        accountStatusEl.text = "Аккаунт: $username (вход выполнен)"
                        // Скрываем поля логина/пароля после успешного входа
                        usernameEl.visibility = android.view.View.GONE
                        passwordEl.visibility = android.view.View.GONE
                        usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
                        passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
                        loginBtn.visibility = android.view.View.GONE
                        qrLoginBtn.visibility = android.view.View.GONE
                        logoutBtn.visibility = android.view.View.VISIBLE
                    }
                    setStatus("Статус: подключено. device_id=$deviceId")
                    // После успешного входа: запрашиваем права на статистику звонков и запускаем сервис
                    ensureCallLogPermissions()
                    startListeningServiceAuto()
                        }
                        is ApiClient.Result.Error -> {
                            tokenManager.clearAll()
                            runOnUiThread {
                                accountStatusEl.text = "Аккаунт: не выполнен вход"
                                // Показываем поля логина/пароля при ошибке
                                usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                                passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                                usernameEl.visibility = android.view.View.VISIBLE
                                passwordEl.visibility = android.view.View.VISIBLE
                                loginBtn.visibility = android.view.View.VISIBLE
                                qrLoginBtn.visibility = android.view.View.VISIBLE
                                logoutBtn.visibility = android.view.View.GONE
                            }
                            setStatus("Ошибка: ${loginResult.message}")
                        }
                    }
                } catch (e: Exception) {
                    tokenManager.clearAll()
                    runOnUiThread {
                        accountStatusEl.text = "Аккаунт: не выполнен вход"
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

        // Проверяем, не вошел ли пользователь через QR (после возврата из QRLoginActivity)
        val savedUsername = tokenManager.getUsername()
        if (!savedUsername.isNullOrBlank() && tokenManager.hasTokens()) {
            // Обновляем UI если пользователь вошел
            accountStatusEl.text = "Аккаунт: $savedUsername (вход выполнен)"
            statusEl.text = "Статус: готово. device_id=$deviceId"
            usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.GONE }
            usernameEl.visibility = android.view.View.GONE
            passwordEl.visibility = android.view.View.GONE
            loginBtn.visibility = android.view.View.GONE
            qrLoginBtn.visibility = android.view.View.GONE
            logoutBtn.visibility = android.view.View.VISIBLE
            // Запускаем сервис если еще не запущен
            ensureCallLogPermissions()
            startListeningServiceAuto()
        }
        
        // Если пользователь вошел, проверяем статус сервиса
        if (tokenManager.hasTokens()) {
            val at = tokenManager.getLastPollAt()
            val code = tokenManager.getLastPollCode()
            
            // Проверяем, работает ли сервис (есть ли недавний опрос)
            if (!at.isNullOrBlank() && code != -1) {
                // Сервис работает, показываем последний статус
                val statusText = when {
                    code == 401 -> "Требуется повторный вход"
                    code == 0 -> "Нет подключения к интернету"
                    code == 204 -> "Ожидание команд · $at"
                    code == 200 -> "Работает · $at"
                    else -> "Опрос $code · $at"
                }
                statusEl.text = "Статус: $statusText"
                
                // Если 401 - предлагаем перезапустить сервис
                if (code == 401) {
                    // Токены могли быть очищены сервисом, проверяем
                    if (!tokenManager.hasTokens()) {
                        accountStatusEl.text = "Аккаунт: требуется повторный вход"
                        statusEl.text = "Статус: сессия истекла, войдите снова"
                        // Показываем форму входа
                        usernameEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                        passwordEl.parent?.let { (it as? android.view.ViewGroup)?.visibility = android.view.View.VISIBLE }
                        usernameEl.visibility = android.view.View.VISIBLE
                        passwordEl.visibility = android.view.View.VISIBLE
                        loginBtn.visibility = android.view.View.VISIBLE
                        qrLoginBtn.visibility = android.view.View.VISIBLE
                        logoutBtn.visibility = android.view.View.GONE
                    }
                }
            } else {
                // Нет данных об опросе - сервис не запущен или только что запустился
                statusEl.text = "Статус: запускаю сервис..."
                // Автоматически запускаем сервис если его нет
                startListeningServiceAuto()
            }
            
            // Показываем информацию об очереди (если пользователь вошел)
            updateQueueStatus()
        } else {
            // Пользователь не вошел
            statusEl.text = "Статус: не подключено"
            queueStatusEl.visibility = android.view.View.GONE
        }
    }
    
    /**
     * Обновить информацию об оффлайн-очереди.
     */
    private fun updateQueueStatus() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val queueManager = QueueManager(this@MainActivity)
                val stats = queueManager.getStats()
                
                runOnUiThread {
                    if (stats.total > 0) {
                        val queueText = buildString {
                            append("Очередь: ${stats.total} элементов")
                            if (stats.callUpdate > 0) append(" (звонки: ${stats.callUpdate})")
                            if (stats.heartbeat > 0) append(" (heartbeat: ${stats.heartbeat})")
                            if (stats.telemetry > 0) append(" (телеметрия: ${stats.telemetry})")
                        }
                        queueStatusEl.text = queueText
                        queueStatusEl.visibility = android.view.View.VISIBLE
                    } else {
                        queueStatusEl.visibility = android.view.View.GONE
                    }
                }
            } catch (e: Exception) {
                // Игнорируем ошибки при получении статистики очереди
            }
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
    
    companion object {
        /**
         * Проверяет, используется ли шифрование на устройстве.
         * @deprecated Используйте TokenManager.isEncryptionEnabled() напрямую
         */
        @Deprecated("Use TokenManager.isEncryptionEnabled() instead")
        fun isEncryptionEnabled(context: Context): Boolean {
            return TokenManager.getInstance(context).isEncryptionEnabled()
        }
    }

    private fun startListeningServiceAuto() {
        if (!tokenManager.hasTokens()) {
            setStatus("Статус: сначала войдите")
            return
        }
        
        val token = tokenManager.getAccessToken()
        val refresh = tokenManager.getRefreshToken()
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
        private const val REQ_CALL_PERMS = 200
    }
}


