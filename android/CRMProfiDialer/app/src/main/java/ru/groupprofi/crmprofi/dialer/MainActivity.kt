package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.net.Uri
import android.os.PowerManager
import android.provider.Settings
import android.view.View
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import com.google.android.material.bottomnavigation.BottomNavigationView
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import android.os.Trace
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.network.ApiClient
import ru.groupprofi.crmprofi.dialer.ui.CallsHistoryActivity
import ru.groupprofi.crmprofi.dialer.ui.onboarding.OnboardingActivity
import ru.groupprofi.crmprofi.dialer.ui.home.HomeFragment
import ru.groupprofi.crmprofi.dialer.ui.dialer.DialerFragment
import ru.groupprofi.crmprofi.dialer.ui.history.HistoryFragment
import ru.groupprofi.crmprofi.dialer.ui.settings.SettingsFragment
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessProvider
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore

/**
 * Главный экран приложения с Bottom Navigation.
 * Single-Activity подход с фрагментами для каждой вкладки.
 */
class MainActivity : AppCompatActivity() {
    private lateinit var bottomNavigation: BottomNavigationView
    // Инфраструктура
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient
    private lateinit var readinessProvider: AppReadinessProvider
    
    // AutoRecoveryManager через AppContainer
    private val autoRecoveryManager: ru.groupprofi.crmprofi.dialer.recovery.AutoRecoveryManager
        get() = ru.groupprofi.crmprofi.dialer.core.AppContainer.autoRecoveryManager
    
    private var pendingStartListening = false
    private lateinit var onboardingLauncher: ActivityResultLauncher<Intent>
    
    private val deviceId: String by lazy {
        Settings.Secure.getString(contentResolver, Settings.Secure.ANDROID_ID) ?: "unknown"
    }
    
    companion object {
        private const val REQ_CALL_PERMS = 200
        private const val REQ_NOTIF_PERMS = 100
        
        /** Маскирует device_id для логов (первые 4 + *** + последние 4 символа). */
        private fun maskDeviceId(deviceId: String): String {
            if (deviceId.length <= 8) return "***"
            return "${deviceId.take(4)}***${deviceId.takeLast(4)}"
        }
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Регистрация Activity Result launcher ОБЯЗАТЕЛЬНО в onCreate до STARTED
        onboardingLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            // Onboarding завершен - фрагменты обновятся через реактивные подписки
        }
        
        // В debug режиме логируем время старта
        val startTime = if (BuildConfig.DEBUG) android.os.SystemClock.elapsedRealtime() else 0L
        
        // Инициализация через AppContainer (используем интерфейсы)
        // ВАЖНО: AppContainer должен быть уже инициализирован в Application.onCreate (на фоне)
        // Если нет - ждем инициализации (fallback для edge cases)
        if (!ru.groupprofi.crmprofi.dialer.core.AppContainer.isInitialized()) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "AppContainer not initialized, initializing synchronously (should not happen)")
            ru.groupprofi.crmprofi.dialer.core.AppContainer.init(applicationContext)
        }
        
        readinessProvider = AppContainer.readinessProvider
        tokenManager = AppContainer.tokenManager
        apiClient = AppContainer.apiClient
        
        // Сначала проверяем авторизацию
        if (!tokenManager.hasTokens()) {
            if (BuildConfig.DEBUG) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("MainActivity", "No tokens, redirecting to LoginActivity")
            }
            startLoginActivity()
            return
        }
        
        // Проверяем, нужно ли показывать onboarding (откладываем чтение SharedPreferences на фоновый поток)
        // Используем launch + withContext вместо runBlocking для неблокирующей проверки
        lifecycleScope.launch {
            val needsOnboarding = withContext(Dispatchers.IO) {
                shouldShowOnboarding()
            }
            if (needsOnboarding) {
                if (BuildConfig.DEBUG) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("MainActivity", "Onboarding not completed, redirecting to OnboardingActivity")
                }
                startOnboarding()
                return@launch
            }
            
            // Продолжаем onCreate только если onboarding не нужен
            // Переключаемся на main thread для UI операций
            withContext(Dispatchers.Main) {
                continueOnCreateAfterOnboardingCheck(startTime, savedInstanceState)
            }
        }
    }
    
    /**
     * Продолжение onCreate после проверки onboarding (вызывается из корутины на main thread).
     */
    private fun continueOnCreateAfterOnboardingCheck(startTime: Long, savedInstanceState: Bundle?) {
        // Логируем время старта в debug режиме
        if (BuildConfig.DEBUG && startTime > 0) {
            val elapsed = android.os.SystemClock.elapsedRealtime() - startTime
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("MainActivity", "onCreate completed in ${elapsed}ms")
        }
        // onboardingLauncher уже зарегистрирован в onCreate()

        try {
            Trace.beginSection("MainActivity.onCreate")
            
            setContentView(R.layout.activity_main_with_nav)
            
            // Инициализируем Bottom Navigation
            bottomNavigation = findViewById(R.id.bottomNavigation)
            setupBottomNavigation()
            
            // Сохраняем device_id если еще не сохранен
            if (tokenManager.getDeviceId().isNullOrBlank()) {
                lifecycleScope.launch(Dispatchers.IO) {
                    tokenManager.saveDeviceId(deviceId)
                }
            }
            
            // Показываем HomeFragment по умолчанию
            if (savedInstanceState == null) {
                supportFragmentManager.beginTransaction()
                    .replace(R.id.fragmentContainer, HomeFragment())
                    .commit()
            }
            
            Trace.endSection()
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("MainActivity", "Ошибка в onCreate: ${e.message}", e)
            android.widget.Toast.makeText(this, "Ошибка запуска: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
            finish()
        }
    }
    
    /**
     * Настроить Bottom Navigation.
     */
    private fun setupBottomNavigation() {
        bottomNavigation.setOnItemSelectedListener { item ->
            when (item.itemId) {
                R.id.nav_home -> {
                    replaceFragment(HomeFragment())
                    true
                }
                R.id.nav_dialer -> {
                    replaceFragment(DialerFragment())
                    true
                }
                R.id.nav_history -> {
                    replaceFragment(HistoryFragment())
                    true
                }
                R.id.nav_settings -> {
                    replaceFragment(SettingsFragment())
                    true
                }
                else -> false
            }
        }
    }
    
    /**
     * Заменить текущий фрагмент с лёгкой анимацией (crossfade + translateY ≤180ms).
     */
    private fun replaceFragment(fragment: Fragment) {
        supportFragmentManager.beginTransaction()
            .setCustomAnimations(
                R.anim.fragment_open_enter,
                R.anim.fragment_open_exit,
                R.anim.fragment_close_enter,
                R.anim.fragment_close_exit
            )
            .replace(R.id.fragmentContainer, fragment)
            .commit()
    }
    
    /**
     * Показать диалог подтверждения выхода.
     * Доступен и из нижнего меню (ранее), и из экрана настроек.
     */
    fun showLogoutConfirmation() {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.settings_logout_confirm_title))
            .setMessage(getString(R.string.settings_logout_confirm_message))
            .setPositiveButton(getString(R.string.settings_logout_confirm_yes)) { _, _ ->
                handleLogout()
            }
            .setNegativeButton(getString(R.string.settings_logout_confirm_no), null)
            .show()
    }
    
    /**
     * Запустить экран входа (очищаем стек, чтобы не было дублирования Activity).
     */
    private fun startLoginActivity() {
        val intent = Intent(this, ru.groupprofi.crmprofi.dialer.ui.login.LoginActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        startActivity(intent)
        finish()
    }
    
    override fun onResume() {
        super.onResume()
        AppState.isForeground = true
        
        // Запускаем автоматическое восстановление
        autoRecoveryManager.start()
        
        // Если готово - автоматически запускаем сервис
        val state = readinessProvider.getState()
        if (state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.READY) {
            startListeningServiceAuto()
        }
        
        // Если есть pending start - запускаем сервис
        if (pendingStartListening) {
            pendingStartListening = false
            startListeningServiceAuto()
        }
        
        // Уведомляем CallListenerService об открытии приложения (для burst window с debounce)
        notifyAppOpened()
    }
    
    /**
     * Уведомить сервис об открытии приложения (для активации burst window с debounce).
     */
    private fun notifyAppOpened() {
        // Отправляем broadcast для пробуждения burst window (с debounce в сервисе)
        try {
            val intent = android.content.Intent("ru.groupprofi.crmprofi.dialer.APP_OPENED")
                .setPackage(packageName)
            sendBroadcast(intent)
        } catch (e: Exception) {
            // Игнорируем ошибки broadcast
        }
    }
    
    override fun onPause() {
        AppState.isForeground = false
        // НЕ останавливаем AutoRecoveryManager - он должен работать в фоне
        super.onPause()
    }
    
    override fun onDestroy() {
        // Останавливаем AutoRecoveryManager только при уничтожении Activity
        autoRecoveryManager.stop()
        super.onDestroy()
    }
    
    
    /**
     * Проверить, нужно ли показывать onboarding.
     */
    private fun shouldShowOnboarding(): Boolean {
        val prefs = getSharedPreferences(OnboardingActivity.PREFS_NAME, MODE_PRIVATE)
        val completed = prefs.getBoolean(OnboardingActivity.KEY_COMPLETED, false)
        return !completed
    }
    
    /**
     * Запустить onboarding.
     */
    private fun startOnboarding() {
        val intent = Intent(this, OnboardingActivity::class.java)
        startActivity(intent)
        finish()
    }
    
    /**
     * Обработать действие кнопки "Исправить" (вызывается из HomeFragment).
     */
    fun handleFixAction(action: AppReadinessChecker.FixActionType) {
        when (action) {
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.REQUEST_PERMISSIONS -> {
                // Если нужна последовательная настройка - открываем onboarding
                val state = readinessProvider.getState()
                if (state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS) {
                    val intent = Intent(this, OnboardingActivity::class.java).apply {
                        putExtra(OnboardingActivity.EXTRA_START_STEP, "PERMISSIONS")
                    }
                    onboardingLauncher.launch(intent)
                } else {
                    requestCallLogPermissions()
                }
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.OPEN_NOTIFICATION_SETTINGS -> {
                // Если нужна последовательная настройка - открываем onboarding
                val state = readinessProvider.getState()
                if (state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS) {
                    val intent = Intent(this, OnboardingActivity::class.java).apply {
                        putExtra(OnboardingActivity.EXTRA_START_STEP, "NOTIFICATIONS")
                    }
                    onboardingLauncher.launch(intent)
                } else {
                    openNotificationSettings()
                }
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.SHOW_LOGIN -> {
                // Перенаправляем на экран входа
                startLoginActivity()
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.OPEN_BATTERY_SETTINGS -> {
                openBatteryOptimizationSettings()
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.OPEN_NETWORK_SETTINGS -> {
                try {
                    val intent = Intent(Settings.ACTION_WIRELESS_SETTINGS)
                    startActivity(intent)
                } catch (e: Exception) {
                    android.widget.Toast.makeText(this, "Откройте настройки сети вручную", android.widget.Toast.LENGTH_LONG).show()
                }
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.RESTART_SERVICE -> {
                restartService()
            }
            
            ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.NONE -> {
                // Ничего не делаем
            }
        }
    }

    /**
     * Открыть настройки батареи (публичный метод для SettingsFragment).
     */
    fun openBatteryOptimizationSettings() {
        // Улучшенная версия с диагностикой и fallback для Android 12+
        var intentOpened = false
        
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val pm = getSystemService(PowerManager::class.java)
                val ignoring = pm?.isIgnoringBatteryOptimizations(packageName) == true
                
                if (!ignoring) {
                    // Пробуем открыть диалог для конкретного приложения
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                        data = Uri.parse("package:$packageName")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    
                    try {
                        startActivity(intent)
                        intentOpened = true
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("MainActivity", "Battery optimization dialog opened")
                        
                        // Проверяем через 1 секунду, открылся ли экран (Android 12+ может блокировать)
                        Handler(Looper.getMainLooper()).postDelayed({
                            val stillIgnoring = pm?.isIgnoringBatteryOptimizations(packageName) == true
                            if (!stillIgnoring && !intentOpened) {
                                // Диалог не открылся - показываем инструкцию
                                showBatteryOptimizationInstructions()
                            }
                        }, 1000)
                        
                        return
                    } catch (e: android.content.ActivityNotFoundException) {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS not available: ${e.message}")
                    } catch (e: SecurityException) {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "SecurityException opening battery settings: ${e.message}")
                    } catch (e: Exception) {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "Error opening battery optimization dialog: ${e.message}")
                    }
                } else {
                    // Уже разрешено - показываем сообщение
                    android.widget.Toast.makeText(this, "Работа в фоне уже разрешена", android.widget.Toast.LENGTH_SHORT).show()
                    return
                }
            }
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "Error checking battery optimization: ${e.message}")
        }
        
        // Fallback 1: открываем общий список настроек батареи
        if (!intentOpened) {
            try {
                val fallbackIntent = Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS).apply {
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                startActivity(fallbackIntent)
                intentOpened = true
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("MainActivity", "Battery optimization settings opened (fallback)")
            } catch (e: android.content.ActivityNotFoundException) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS not available: ${e.message}")
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "Error opening battery settings (fallback): ${e.message}")
            }
        }
        
        // Fallback 2: открываем настройки приложения (пользователь может зайти в «Батарея» и отключить оптимизацию)
        if (!intentOpened) {
            try {
                val appDetailsIntent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:$packageName")
                    addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                }
                startActivity(appDetailsIntent)
                android.widget.Toast.makeText(this, "Откройте «Батарея» → «Не ограничивать» или «Не оптимизировать»", android.widget.Toast.LENGTH_LONG).show()
                intentOpened = true
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("MainActivity", "Error opening app details: ${e.message}")
            }
        }
        
        // Fallback 3: если ничего не открылось — показываем инструкцию
        if (!intentOpened) {
            showBatteryOptimizationInstructions()
        }
    }
    
    /**
     * Показать диалог с инструкцией по настройке батареи.
     */
    private fun showBatteryOptimizationInstructions() {
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("Настройка работы в фоне")
            .setMessage("Для надежной работы приложения в фоне:\n\n" +
                    "1. Откройте Настройки → Батарея\n" +
                    "2. Найдите \"Оптимизация батареи\" или \"Неограниченное использование батареи\"\n" +
                    "3. Найдите \"CRM ПРОФИ\" в списке\n" +
                    "4. Выберите \"Не оптимизировать\" или \"Разрешить\"\n\n" +
                    "На некоторых устройствах этот пункт может находиться в Настройки → Приложения → CRM ПРОФИ → Батарея")
            .setPositiveButton("Открыть настройки приложения") { _, _ ->
                try {
                    val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                        data = Uri.parse("package:$packageName")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                    startActivity(intent)
                } catch (e: Exception) {
                    android.widget.Toast.makeText(this, "Откройте настройки приложения вручную", android.widget.Toast.LENGTH_LONG).show()
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }
    
    /**
     * Запросить разрешения на CallLog.
     */
    private fun requestCallLogPermissions() {
        val needed = mutableListOf<String>()
        val callPerm = android.Manifest.permission.READ_CALL_LOG
        val phoneStatePerm = android.Manifest.permission.READ_PHONE_STATE
        
        if (ContextCompat.checkSelfPermission(this, callPerm) != PackageManager.PERMISSION_GRANTED) {
            needed += callPerm
        }
        if (ContextCompat.checkSelfPermission(this, phoneStatePerm) != PackageManager.PERMISSION_GRANTED) {
            needed += phoneStatePerm
        }
        
        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(this, needed.toTypedArray(), REQ_CALL_PERMS)
        }
        // Разрешения запрошены - фрагменты обновятся через реактивные подписки
    }
    
    /**
     * Открыть настройки уведомлений.
     */
    private fun openNotificationSettings() {
        try {
            val intent = Intent().apply {
                action = Settings.ACTION_APP_NOTIFICATION_SETTINGS
                putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
        } catch (e: Exception) {
            android.widget.Toast.makeText(this, "Откройте настройки уведомлений вручную", android.widget.Toast.LENGTH_LONG).show()
        }
    }
    
    /**
     * Перезапустить сервис.
     */
    private fun restartService() {
        // Останавливаем старый сервис
        stopService(Intent(this, CallListenerService::class.java))
        
        // Запускаем новый сервис
        CoroutineScope(Dispatchers.IO).launch {
            delay(1000) // Даём время на остановку
            runOnUiThread {
                startListeningServiceAuto()
            }
        }
    }
    
    /**
     * Обработать выход.
     * Перед остановкой сервиса запускаем форсированную отправку телеметрии в фоне.
     */
    private fun handleLogout() {
        lifecycleScope.launch(Dispatchers.IO) {
            try { apiClient.flushTelemetry() } catch (_: Exception) { }
        }
        tokenManager.clearAll()
        stopService(Intent(this, CallListenerService::class.java))
        startLoginActivity()
    }
    
    /**
     * Автоматически запустить сервис прослушивания.
     */
    private fun startListeningServiceAuto() {
        if (!tokenManager.hasTokens()) {
            return
        }
        
        val token = tokenManager.getAccessToken()
        val refresh = tokenManager.getRefreshToken()
        if (token.isNullOrBlank() || refresh.isNullOrBlank()) {
            return
        }
        
        // Проверка уведомлений
        if (!androidx.core.app.NotificationManagerCompat.from(this).areNotificationsEnabled()) {
            pendingStartListening = true
            return
        }
        
        // Проверка разрешения на уведомления (Android 13+)
        if (Build.VERSION.SDK_INT >= 33) {
            val perm = android.Manifest.permission.POST_NOTIFICATIONS
            if (ContextCompat.checkSelfPermission(this, perm) != PackageManager.PERMISSION_GRANTED) {
                pendingStartListening = true
                ActivityCompat.requestPermissions(this, arrayOf(perm), REQ_NOTIF_PERMS)
                return
            }
        }
        
        val intent = Intent(this, CallListenerService::class.java)
            .putExtra(CallListenerService.EXTRA_TOKEN, token)
            .putExtra(CallListenerService.EXTRA_REFRESH, refresh)
            .putExtra(CallListenerService.EXTRA_DEVICE_ID, deviceId)
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("MainActivity", "Запуск CallListenerService: deviceId=${maskDeviceId(deviceId)}")
        if (Build.VERSION.SDK_INT >= 26) {
            startForegroundService(intent)
        } else {
            startService(intent)
        }
    }
    
    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<out String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        
        when (requestCode) {
            REQ_NOTIF_PERMS -> {
                val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
                if (granted && pendingStartListening) {
                    pendingStartListening = false
                    startListeningServiceAuto()
                }
                // Разрешения обработаны - фрагменты обновятся через реактивные подписки
            }
            
            REQ_CALL_PERMS -> {
                val allGranted = grantResults.all { it == PackageManager.PERMISSION_GRANTED }
                if (!allGranted) {
                    // Если отказано - показываем сообщение
                    android.widget.Toast.makeText(this, "Разрешения необходимы для работы приложения", android.widget.Toast.LENGTH_LONG).show()
                }
                // Разрешения обработаны - фрагменты обновятся через реактивные подписки
            }
        }
    }
}
