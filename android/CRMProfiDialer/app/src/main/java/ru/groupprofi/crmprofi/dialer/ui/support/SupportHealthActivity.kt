package ru.groupprofi.crmprofi.dialer.ui.support

import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import android.view.View
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import kotlinx.coroutines.repeatOnLifecycle
import androidx.lifecycle.Lifecycle
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.recovery.SafeModeManager
import ru.groupprofi.crmprofi.dialer.support.CrashLogStore
import ru.groupprofi.crmprofi.dialer.support.SupportReportBuilder
import ru.groupprofi.crmprofi.dialer.ui.onboarding.OnboardingActivity
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import java.text.SimpleDateFormat
import java.util.*

/**
 * Экран диагностики для поддержки.
 * Показывает состояние приложения и позволяет поделиться диагностикой.
 */
class SupportHealthActivity : AppCompatActivity() {
    
    private lateinit var statusIcon: TextView
    private lateinit var statusText: TextView
    private lateinit var checksContainer: LinearLayout
    private lateinit var fixButton: Button
    private lateinit var restartButton: Button
    private lateinit var shareButton: Button
    private lateinit var progressText: TextView
    
    private val readinessProvider = AppContainer.readinessProvider
    private val callHistoryStore = AppContainer.callHistoryStore
    private val pendingCallStore = AppContainer.pendingCallStore
    private val statsUseCase = CallStatsUseCase()
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_support_health)
        
        initViews()
        setupToolbar()
        setupButtons()
        updateHealthStatus()
        
        // Подписываемся на изменения для автоматического обновления
        setupReactiveSubscriptions()
    }
    
    private fun initViews() {
        statusIcon = findViewById(R.id.healthStatusIcon)
        statusText = findViewById(R.id.healthStatusText)
        checksContainer = findViewById(R.id.healthChecksContainer)
        fixButton = findViewById(R.id.healthFixButton)
        restartButton = findViewById(R.id.healthRestartButton)
        shareButton = findViewById(R.id.healthShareButton)
        progressText = findViewById(R.id.healthProgressText)
    }
    
    private fun setupToolbar() {
        val toolbar: Toolbar = findViewById(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { finish() }
    }
    
    private fun setupButtons() {
        fixButton.text = getString(R.string.button_fix)
        fixButton.setOnClickListener {
            handleFixAction()
        }
        
        restartButton.text = getString(R.string.diagnostics_restart_work)
        restartButton.setOnClickListener {
            showRestartConfirmDialog()
        }
        
        shareButton.text = getString(R.string.diagnostics_share)
        shareButton.setOnClickListener {
            shareDiagnostics()
        }
    }
    
    /**
     * Настроить реактивные подписки для автоматического обновления.
     */
    private fun setupReactiveSubscriptions() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                // Обновляем при изменении истории звонков
                callHistoryStore.callsFlow.collectLatest {
                    updateHealthStatus()
                }
                
                // Обновляем при изменении ожидаемых звонков
                pendingCallStore.hasActivePendingCallsFlow.collectLatest {
                    updateHealthStatus()
                }
            }
        }
    }
    
    /**
     * Обновить статус здоровья приложения.
     */
    private fun updateHealthStatus() {
        val state = readinessProvider.getState()
        val uiModel = readinessProvider.getUiModel()
        
        // Обновляем главный статус
        if (state == AppReadinessChecker.ReadyState.READY) {
            statusIcon.text = "🟢"
            statusText.text = getString(R.string.diagnostics_status_ready)
        } else {
            statusIcon.text = "🔴"
            statusText.text = getString(R.string.diagnostics_status_not_ready)
        }
        
        // Очищаем контейнер чеков
        checksContainer.removeAllViews()
        
        // Добавляем чек-листы
        addCheckItem(getString(R.string.diagnostics_check_permissions), checkPermissionsStatus())
        addCheckItem(getString(R.string.diagnostics_check_notifications), checkNotificationsStatus())
        addCheckItem(getString(R.string.diagnostics_check_network), checkNetworkStatus())
        addCheckItem(getString(R.string.diagnostics_check_auth), checkAuthStatus())
        addCheckItem(getString(R.string.diagnostics_check_queue), checkQueueStatus())
        addCheckItem(getString(R.string.diagnostics_check_pending_calls), checkPendingCallsStatus())
        addCheckItem(getString(R.string.diagnostics_check_history), checkHistoryStatus())
        addCheckItem(getString(R.string.diagnostics_check_version), getAppVersion())
        addCheckItem(getString(R.string.diagnostics_check_android), getAndroidInfo())
        addCheckItem("Safe mode", checkSafeModeStatus())
        addCheckItem("Последний сбой", checkLastCrashStatus())
        
        // Чек перед релизом
        addCheckItem("Build type", getBuildType())
        addCheckItem("Минификация", getMinifyStatus())
    }
    
    /**
     * Добавить элемент чек-листа.
     */
    private fun addCheckItem(label: String, status: String) {
        val itemView = layoutInflater.inflate(R.layout.item_health_check, checksContainer, false)
        val labelView: TextView = itemView.findViewById(R.id.checkLabel)
        val statusView: TextView = itemView.findViewById(R.id.checkStatus)
        
        labelView.text = label
        statusView.text = status
        
        checksContainer.addView(itemView)
    }
    
    /**
     * Проверить статус разрешений.
     */
    private fun checkPermissionsStatus(): String {
        val hasCallLog = ContextCompat.checkSelfPermission(
            this, android.Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        
        val hasPhoneState = ContextCompat.checkSelfPermission(
            this, android.Manifest.permission.READ_PHONE_STATE
        ) == PackageManager.PERMISSION_GRANTED
        
        return when {
            hasCallLog && hasPhoneState -> getString(R.string.diagnostics_status_ok)
            !hasCallLog && !hasPhoneState -> "Нужны READ_CALL_LOG, READ_PHONE_STATE"
            !hasCallLog -> "Нужен READ_CALL_LOG"
            else -> "Нужен READ_PHONE_STATE"
        }
    }
    
    /**
     * Проверить статус уведомлений.
     */
    private fun checkNotificationsStatus(): String {
        val enabled = NotificationManagerCompat.from(this).areNotificationsEnabled()
        return if (enabled) getString(R.string.diagnostics_status_ok) else "Выключены"
    }
    
    /**
     * Проверить статус сети.
     */
    private fun checkNetworkStatus(): String {
        val connectivityManager = getSystemService(android.net.ConnectivityManager::class.java)
        val network = connectivityManager?.activeNetwork
        val capabilities = connectivityManager?.getNetworkCapabilities(network)
        val hasNetwork = capabilities != null && (
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_WIFI) ||
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_CELLULAR) ||
            capabilities.hasTransport(android.net.NetworkCapabilities.TRANSPORT_ETHERNET)
        )
        return if (hasNetwork) getString(R.string.diagnostics_status_ok) else "Нет сети"
    }
    
    /**
     * Проверить статус авторизации.
     */
    private fun checkAuthStatus(): String {
        val state = readinessProvider.getState()
        return when (state) {
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> "Нужно войти"
            AppReadinessChecker.ReadyState.READY -> getString(R.string.diagnostics_status_ok)
            else -> {
                val tokenManager = AppContainer.tokenManager
                if (tokenManager.hasTokens()) getString(R.string.diagnostics_status_ok) else getString(R.string.error_unknown)
            }
        }
    }
    
    /**
     * Проверить статус очереди.
     */
    private fun checkQueueStatus(): String {
        return try {
            val queueManager = ru.groupprofi.crmprofi.dialer.queue.QueueManager(this)
            val stats = queueManager.getStats()
            "${stats.pendingCount} элементов (ожидают отправки)"
        } catch (e: Exception) {
            getString(R.string.error_failed_to_check)
        }
    }
    
    /**
     * Проверить статус ожидаемых звонков.
     */
    private fun checkPendingCallsStatus(): String {
        // Используем текущее значение Flow для синхронного получения
        val hasActive = pendingCallStore.hasActivePendingCallsFlow.value
        return if (hasActive) {
            // Пытаемся получить количество (асинхронно, но показываем что есть активные)
            "Есть активные"
        } else {
            "Нет"
        }
    }
    
    /**
     * Проверить статус истории звонков.
     */
    private fun checkHistoryStatus(): String {
        val allCalls = callHistoryStore.callsFlow.value
        val todayStats = statsUseCase.calculate(allCalls, CallStatsUseCase.Period.TODAY)
        return "Всего: ${allCalls.size}, Сегодня: ${todayStats.total}"
    }
    
    /**
     * Получить версию приложения.
     */
    private fun getAppVersion(): String {
        return try {
            val pm = packageManager
            val pkgInfo = pm.getPackageInfo(packageName, 0)
            "${pkgInfo.versionName} (${pkgInfo.longVersionCode})"
        } catch (e: Exception) {
            getString(R.string.error_unknown)
        }
    }
    
    /**
     * Получить информацию об Android.
     */
    private fun getAndroidInfo(): String {
        val sdk = Build.VERSION.SDK_INT
        val model = Build.MODEL
        val manufacturer = Build.MANUFACTURER
        // Маскируем модель устройства (оставляем только первые символы)
        val maskedModel = if (model.length > 8) {
            "${model.take(4)}***"
        } else {
            model
        }
        return "SDK $sdk, $manufacturer $maskedModel"
    }
    
    /**
     * Проверить статус Safe Mode.
     */
    private fun checkSafeModeStatus(): String {
        val lastRestart = SafeModeManager.getLastRestartFormatted(this)
        return if (lastRestart != null) {
            "Последний перезапуск: $lastRestart"
        } else {
            "Доступен"
        }
    }
    
    /**
     * Проверить статус последнего сбоя.
     */
    private fun checkLastCrashStatus(): String {
        val lastCrashTime = CrashLogStore.getLastCrashTime(this)
        return if (lastCrashTime != null) {
            val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())
            "Был: ${dateFormat.format(Date(lastCrashTime))}"
        } else {
            "Не было"
        }
    }
    
    /**
     * Получить build type.
     */
    private fun getBuildType(): String {
        return try {
            if (ru.groupprofi.crmprofi.dialer.BuildConfig.DEBUG) {
                "debug"
            } else {
                // В release или staging (точное определение сложно без дополнительных BuildConfig полей)
                "release/staging"
            }
        } catch (e: Exception) {
            "unknown"
        }
    }
    
    /**
     * Получить статус минификации.
     */
    private fun getMinifyStatus(): String {
        return try {
            if (ru.groupprofi.crmprofi.dialer.BuildConfig.DEBUG) {
                "нет"
            } else {
                // Для staging: minifyEnabled=true, для release: minifyEnabled=false
                // Точную проверку сложно сделать без дополнительных BuildConfig полей
                "да (staging) / нет (release)"
            }
        } catch (e: Exception) {
            "неизвестно"
        }
    }
    
    /**
     * Обработать действие "Исправить".
     */
    private fun handleFixAction() {
        val state = readinessProvider.getState()
        val uiModel = readinessProvider.getUiModel()
        
        when (uiModel.fixActionType) {
            AppReadinessChecker.FixActionType.REQUEST_PERMISSIONS -> {
                val intent = Intent(this, OnboardingActivity::class.java).apply {
                    putExtra(OnboardingActivity.EXTRA_START_STEP, ru.groupprofi.crmprofi.dialer.ui.onboarding.OnboardingActivity.OnboardingStep.PERMISSIONS.name)
                }
                startActivity(intent)
            }
            AppReadinessChecker.FixActionType.OPEN_NOTIFICATION_SETTINGS -> {
                val intent = Intent(this, OnboardingActivity::class.java).apply {
                    putExtra(OnboardingActivity.EXTRA_START_STEP, ru.groupprofi.crmprofi.dialer.ui.onboarding.OnboardingActivity.OnboardingStep.NOTIFICATIONS.name)
                }
                startActivity(intent)
            }
            AppReadinessChecker.FixActionType.SHOW_LOGIN -> {
                // Возвращаемся на главный экран, где есть форма входа
                finish()
            }
            else -> {
                // По умолчанию открываем onboarding
                val intent = Intent(this, OnboardingActivity::class.java)
                startActivity(intent)
            }
        }
    }
    
    /**
     * Поделиться диагностикой.
     */
    private fun shareDiagnostics() {
        val report = SupportReportBuilder.build(this)
        
        val shareIntent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, report)
            putExtra(Intent.EXTRA_SUBJECT, getString(R.string.diagnostics_share_subject))
        }
        
        startActivity(Intent.createChooser(shareIntent, getString(R.string.diagnostics_share)))
    }
    
    /**
     * Показать диалог подтверждения перезапуска.
     */
    private fun showRestartConfirmDialog() {
        AlertDialog.Builder(this)
            .setTitle(getString(R.string.diagnostics_restart_confirm_title))
            .setMessage(getString(R.string.diagnostics_restart_confirm_message))
            .setPositiveButton(getString(R.string.diagnostics_restart_confirm_button)) { _, _ ->
                performRestart()
            }
            .setNegativeButton(getString(android.R.string.cancel), null)
            .show()
    }
    
    /**
     * Выполнить перезапуск работы приложения.
     */
    private fun performRestart() {
        // Показываем прогресс
        progressText.visibility = View.VISIBLE
        progressText.text = getString(R.string.diagnostics_restart_progress)
        fixButton.isEnabled = false
        restartButton.isEnabled = false
        shareButton.isEnabled = false
        
        // Выполняем перезапуск в корутине
        lifecycleScope.launch {
            try {
                val result = SafeModeManager.restartAppWork(this@SupportHealthActivity)
                
                // Показываем результат
                when (result) {
                    is SafeModeManager.SafeModeResult.Success -> {
                        Toast.makeText(
                            this@SupportHealthActivity,
                            getString(R.string.diagnostics_restart_done),
                            Toast.LENGTH_SHORT
                        ).show()
                    }
                    is SafeModeManager.SafeModeResult.PartialSuccess -> {
                        Toast.makeText(
                            this@SupportHealthActivity,
                            "${getString(R.string.diagnostics_restart_done)} (${result.message})",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                    is SafeModeManager.SafeModeResult.Failed -> {
                        Toast.makeText(
                            this@SupportHealthActivity,
                            "Ошибка: ${result.reason}",
                            Toast.LENGTH_LONG
                        ).show()
                    }
                }
                
                // Обновляем чек-лист (он реактивный, обновится автоматически)
                updateHealthStatus()
                
                // Небольшая задержка перед обновлением UI
                kotlinx.coroutines.delay(1000)
            } catch (e: Exception) {
                Toast.makeText(
                    this@SupportHealthActivity,
                    "Ошибка перезапуска: ${e.message}",
                    Toast.LENGTH_LONG
                ).show()
            } finally {
                // Скрываем прогресс и восстанавливаем кнопки
                progressText.visibility = View.GONE
                fixButton.isEnabled = true
                restartButton.isEnabled = true
                shareButton.isEnabled = true
            }
        }
    }
    
    /**
     * Тестовая кнопка для проверки crash handler (только в debug).
     */
    private fun addTestCrashButtonIfDebug() {
        if (ru.groupprofi.crmprofi.dialer.BuildConfig.DEBUG) {
            // В debug режиме можно добавить тестовую кнопку для проверки crash handler
            // Но не добавляем её в UI автоматически, чтобы не путать пользователей
            // Можно добавить через long-press или скрытую кнопку, если нужно
        }
    }
}
