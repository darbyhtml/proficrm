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
import android.view.Choreographer
import android.view.View
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import com.google.android.material.card.MaterialCardView
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
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
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessProvider
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore

/**
 * Главный экран приложения - экран уверенности.
 * Показывает статус готовности простым языком без технических терминов.
 */
class MainActivity : AppCompatActivity() {
    // Используем интерфейсы из domain (не знаем о реализациях)
    private lateinit var callHistoryStore: CallHistoryStore
    private lateinit var pendingCallStore: PendingCallStore
    private lateinit var readinessProvider: AppReadinessProvider
    private val statsUseCase = CallStatsUseCase()
    
    // Инфраструктура (для совместимости со старым кодом)
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient
    // AutoRecoveryManager через AppContainer
    private val autoRecoveryManager: ru.groupprofi.crmprofi.dialer.recovery.AutoRecoveryManager
        get() = ru.groupprofi.crmprofi.dialer.core.AppContainer.autoRecoveryManager
    
    // UI элементы статистики
    private lateinit var todayTotal: TextView
    private lateinit var todaySuccess: TextView
    private lateinit var todayNoAnswer: TextView
    private lateinit var todayDropped: TextView
    private lateinit var todayPendingCrm: TextView
    
    // UI элементы из нового layout
    private lateinit var statusCard: MaterialCardView
    private lateinit var statusIcon: TextView
    private lateinit var statusText: TextView
    private lateinit var statusExplanation: TextView
    private lateinit var fixButton: Button
    private lateinit var callsHistoryCard: MaterialCardView
    private lateinit var callsCount: TextView
    private lateinit var logoutBtn: Button
    
    // Скрытый режим поддержки
    private var supportModeEnabled = false
    private var longPressStartTime = 0L
    private val longPressDuration = 5000L // 5 секунд
    
    private var pendingStartListening = false
    private var currentFixAction: ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType = 
        ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.FixActionType.NONE

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
        
        // Регистрация Activity Result launcher ОБЯЗАТЕЛЬНО в onCreate до STARTED (нельзя в корутине после onResume).
        onboardingLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            updateReadinessStatus()
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
        
        callHistoryStore = AppContainer.callHistoryStore
        pendingCallStore = AppContainer.pendingCallStore
        readinessProvider = AppContainer.readinessProvider
        
        // Инфраструктура (для совместимости)
        tokenManager = AppContainer.tokenManager
        apiClient = AppContainer.apiClient
        // autoRecoveryManager теперь через getter, не нужно присваивать
        
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
                continueOnCreateAfterOnboardingCheck(startTime)
            }
        }
    }
    
    /**
     * Продолжение onCreate после проверки onboarding (вызывается из корутины на main thread).
     */
    private fun continueOnCreateAfterOnboardingCheck(startTime: Long) {
        // Логируем время старта в debug режиме
        if (BuildConfig.DEBUG && startTime > 0) {
            val elapsed = android.os.SystemClock.elapsedRealtime() - startTime
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("MainActivity", "onCreate completed in ${elapsed}ms")
        }
        // onboardingLauncher уже зарегистрирован в onCreate()

        try {
            Trace.beginSection("MainActivity.onCreate")
            
            setContentView(R.layout.activity_main)
            
            // Находим UI элементы (быстрая операция)
            initViews()
            
            // Настраиваем обработчики (быстрая операция)
            setupClickListeners()
            
            // Настраиваем long-press для режима поддержки (быстрая операция)
            setupSupportMode()
            
            // Настраиваем реактивные подписки (быстрая операция)
            setupReactiveSubscriptions()
            
            // Сохраняем device_id если еще не сохранен (может быть тяжело - откладываем)
            if (tokenManager.getDeviceId().isNullOrBlank()) {
                lifecycleScope.launch(Dispatchers.IO) {
                    tokenManager.saveDeviceId(deviceId)
                }
            }
            
            // Обновляем UI после первого кадра (откладываем тяжелые проверки)
            Choreographer.getInstance().postFrameCallback {
                Trace.beginSection("MainActivity.updateReadinessStatus")
                updateReadinessStatus()
                Trace.endSection()
            }
            
            Trace.endSection()
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("MainActivity", "Ошибка в onCreate: ${e.message}", e)
            android.widget.Toast.makeText(this, "Ошибка запуска: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
            finish()
        }
    }
    
    private fun initViews() {
        statusCard = findViewById(R.id.statusCard)
        statusIcon = findViewById(R.id.statusIcon)
        statusText = findViewById(R.id.statusText)
        statusExplanation = findViewById(R.id.statusExplanation)
        fixButton = findViewById(R.id.fixButton)
        callsHistoryCard = findViewById(R.id.callsHistoryCard)
        callsCount = findViewById(R.id.callsCount)
        logoutBtn = findViewById(R.id.logoutBtn)
        
        // Элементы статистики "Сегодня"
        todayTotal = findViewById(R.id.todayTotal)
        todaySuccess = findViewById(R.id.todaySuccess)
        todayNoAnswer = findViewById(R.id.todayNoAnswer)
        todayDropped = findViewById(R.id.todayDropped)
        todayPendingCrm = findViewById(R.id.todayPendingCrm)
    }
    
    private fun setupClickListeners() {
        // Кнопка "Исправить"
        fixButton.setOnClickListener {
            handleFixAction(currentFixAction)
        }
        
        // История звонков
        callsHistoryCard.setOnClickListener {
            val intent = Intent(this, CallsHistoryActivity::class.java)
            startActivity(intent)
        }
        
        // Выход
        logoutBtn.setOnClickListener {
            handleLogout()
        }
    }
    
    /**
     * Настройка long-press для режима поддержки (5 секунд на заголовок статуса).
     */
    private fun setupSupportMode() {
        statusText.setOnLongClickListener {
            longPressStartTime = System.currentTimeMillis()
            Handler(Looper.getMainLooper()).postDelayed({
                if (System.currentTimeMillis() - longPressStartTime >= longPressDuration) {
                    showSupportModeDialog()
                }
            }, longPressDuration)
            true
        }
    }
    
    /**
     * Показать диалог подтверждения входа в режим поддержки.
     */
    private fun showSupportModeDialog() {
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("Режим поддержки")
            .setMessage("Открыть диагностику?")
            .setPositiveButton("Открыть") { _, _ ->
                openDiagnostics()
            }
            .setNegativeButton("Отмена", null)
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
    
    /**
     * Открыть экран диагностики.
     */
    private fun openDiagnostics() {
        supportModeEnabled = true
        val intent = Intent(this, ru.groupprofi.crmprofi.dialer.ui.support.SupportHealthActivity::class.java)
        startActivity(intent)
    }
    
    override fun onResume() {
        super.onResume()
        AppState.isForeground = true
        
        // Запускаем автоматическое восстановление
        autoRecoveryManager.start()
        
        // Обновляем статус только если UI уже инициализирован (continueOnCreateAfterOnboardingCheck уже выполнился).
        // Иначе onResume может вызваться до завершения корутины проверки onboarding — statusIcon ещё не присвоен.
        if (::statusIcon.isInitialized) {
            updateReadinessStatus()
        }
        
        // Если есть pending start - запускаем сервис
        if (pendingStartListening) {
            pendingStartListening = false
            startListeningServiceAuto()
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
     * Настроить реактивные подписки на потоки данных.
     */
    private fun setupReactiveSubscriptions() {
        // Подписка на количество звонков в истории
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.countFlow.collectLatest { count ->
                    callsCount.text = count.toString()
                }
            }
        }
        
        // Подписка на историю звонков для статистики "Сегодня"
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.callsFlow.collectLatest { calls ->
                    updateTodayStats(calls)
                }
            }
        }
        
        // Подписка на активные ожидаемые звонки (для показа "Определяем результат...")
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                pendingCallStore.hasActivePendingCallsFlow.collectLatest { _ ->
                    updateReadinessStatus()
                }
            }
        }
    }
    
    /**
     * Обновить статистику "Сегодня".
     */
    private fun updateTodayStats(calls: List<ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem>) {
        val stats = statsUseCase.calculate(calls, CallStatsUseCase.Period.TODAY)
        
        todayTotal.text = stats.total.toString()
        todaySuccess.text = stats.success.toString()
        todayNoAnswer.text = stats.noAnswer.toString()
        todayDropped.text = stats.dropped.toString()
        
        // Показываем бейдж "Ожидает отправки" только если есть такие звонки
        if (stats.pendingCrm > 0) {
            todayPendingCrm.text = getString(R.string.stats_pending_crm, stats.pendingCrm)
            todayPendingCrm.visibility = View.VISIBLE
        } else {
            todayPendingCrm.visibility = View.GONE
        }
    }
    
    /**
     * Обновить статус готовности приложения с плавными анимациями.
     * Не вызывать до инициализации UI (continueOnCreateAfterOnboardingCheck).
     */
    private fun updateReadinessStatus() {
        if (!::statusIcon.isInitialized) return
        val state = readinessProvider.getState()
        val uiModel = readinessProvider.getUiModel()
        
        // Проверяем, есть ли активные ожидаемые звонки (используем текущее значение Flow)
        val hasResolvingCalls = pendingCallStore.hasActivePendingCallsFlow.value
        
        // Если есть активные ожидаемые звонки - показываем "Определяем результат..."
        if (hasResolvingCalls && state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.READY) {
            animateStatusChange(
                icon = "🟡",
                title = getString(R.string.status_resolving),
                explanation = getString(R.string.status_explanation_resolving),
                showFixButton = false
            )
        } else {
            // Обычный статус готовности
            animateStatusChange(
                icon = uiModel.iconEmoji,
                title = uiModel.title,
                explanation = uiModel.message,
                showFixButton = uiModel.showFixButton
            )
            currentFixAction = uiModel.fixActionType
            updateFixButtonStyle(currentFixAction)
        }
        
        // Показываем/скрываем кнопку выхода в зависимости от состояния
        if (state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.NEEDS_AUTH) {
            // Если нет авторизации - перенаправляем на экран входа
            startLoginActivity()
            return
        } else {
            if (tokenManager.hasTokens()) {
                logoutBtn.visibility = View.VISIBLE
            } else {
                logoutBtn.visibility = View.GONE
            }
        }
        
        // Если готово - автоматически запускаем сервис
        if (state == ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker.ReadyState.READY) {
            startListeningServiceAuto()
        }
    }

    private fun updateFixButtonStyle(action: AppReadinessChecker.FixActionType) {
        // Текст/цвет кнопки под разные сценарии: "критично не готово" vs "рекомендация по фону".
        when (action) {
            AppReadinessChecker.FixActionType.OPEN_BATTERY_SETTINGS -> {
                fixButton.text = getString(R.string.button_allow_background)
                ViewCompat.setBackgroundTintList(
                    fixButton,
                    android.content.res.ColorStateList.valueOf(android.graphics.Color.parseColor("#244B47"))
                )
            }
            else -> {
                fixButton.text = getString(R.string.button_fix)
                ViewCompat.setBackgroundTintList(
                    fixButton,
                    android.content.res.ColorStateList.valueOf(android.graphics.Color.parseColor("#EF4444"))
                )
            }
        }
    }
    
    /**
     * Анимировать изменение статуса с плавным fade-in/fade-out.
     */
    private fun animateStatusChange(
        icon: String,
        title: String,
        explanation: String,
        showFixButton: Boolean
    ) {
        // Анимация иконки и текста статуса (fade-out → изменение → fade-in)
        val duration = 200L // 200ms для плавности
        
        // Проверяем, нужно ли анимировать (если текст не изменился - не анимируем)
        val iconChanged = statusIcon.text != icon
        val titleChanged = statusText.text != title
        val explanationChanged = statusExplanation.text != explanation
        
        if (iconChanged) {
            statusIcon.animate()
                .alpha(0f)
                .setDuration(duration / 2)
                .withEndAction {
                    statusIcon.text = icon
                    statusIcon.animate()
                        .alpha(1f)
                        .setDuration(duration / 2)
                        .start()
                }
                .start()
        } else {
            statusIcon.text = icon
        }
        
        if (titleChanged) {
            statusText.animate()
                .alpha(0f)
                .setDuration(duration / 2)
                .withEndAction {
                    statusText.text = title
                    statusText.animate()
                        .alpha(1f)
                        .setDuration(duration / 2)
                        .start()
                }
                .start()
        } else {
            statusText.text = title
        }
        
        if (explanationChanged) {
            statusExplanation.animate()
                .alpha(0f)
                .setDuration(duration / 2)
                .withEndAction {
                    statusExplanation.text = explanation
                    statusExplanation.animate()
                        .alpha(1f)
                        .setDuration(duration / 2)
                        .start()
                }
                .start()
        } else {
            statusExplanation.text = explanation
        }
        
        // Анимация кнопки "Исправить" (scale + alpha)
        if (showFixButton && fixButton.visibility != View.VISIBLE) {
            // Появление
            fixButton.alpha = 0f
            fixButton.scaleX = 0.9f
            fixButton.scaleY = 0.9f
            fixButton.visibility = View.VISIBLE
            fixButton.animate()
                .alpha(1f)
                .scaleX(1f)
                .scaleY(1f)
                .setDuration(duration)
                .start()
        } else if (!showFixButton && fixButton.visibility == View.VISIBLE) {
            // Исчезновение
            fixButton.animate()
                .alpha(0f)
                .scaleX(0.9f)
                .scaleY(0.9f)
                .setDuration(duration)
                .withEndAction {
                    fixButton.visibility = View.GONE
                }
                .start()
        }
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
     * Обработать действие кнопки "Исправить".
     */
    private fun handleFixAction(action: AppReadinessChecker.FixActionType) {
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

    private fun openBatteryOptimizationSettings() {
        // Без принуждения: открываем системный диалог/настройки, объяснение уже в statusExplanation.
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val pm = getSystemService(PowerManager::class.java)
                val ignoring = pm?.isIgnoringBatteryOptimizations(packageName) == true
                if (!ignoring) {
                    // Просим разрешение для конкретного приложения
                    val intent = Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS).apply {
                        data = Uri.parse("package:$packageName")
                    }
                    startActivity(intent)
                    return
                }
            }
        } catch (_: Exception) {
            // fallback ниже
        }
        
        // Fallback: открываем общий список
        try {
            startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS))
        } catch (e: Exception) {
            android.widget.Toast.makeText(this, "Откройте настройки батареи вручную", android.widget.Toast.LENGTH_LONG).show()
        }
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
        } else {
            // Если разрешения уже есть, но всё равно показываем NEEDS_PERMISSIONS - возможно проблема в другом
            updateReadinessStatus()
        }
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
        
        // Показываем сообщение
        statusExplanation.text = "Пробую восстановить..."
        
        // Запускаем новый сервис
        CoroutineScope(Dispatchers.IO).launch {
            delay(1000) // Даём время на остановку
            runOnUiThread {
                startListeningServiceAuto()
                // Обновляем статус через 2 секунды
                Handler(Looper.getMainLooper()).postDelayed({
                    updateReadinessStatus()
                }, 2000)
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
                } else if (!granted) {
                    updateReadinessStatus()
                }
            }
            
            REQ_CALL_PERMS -> {
                val allGranted = grantResults.all { it == PackageManager.PERMISSION_GRANTED }
                if (allGranted) {
                    updateReadinessStatus()
                } else {
                    // Если отказано - показываем сообщение
                    android.widget.Toast.makeText(this, "Разрешения необходимы для работы приложения", android.widget.Toast.LENGTH_LONG).show()
                    updateReadinessStatus()
                }
            }
        }
    }
}
