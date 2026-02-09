package ru.groupprofi.crmprofi.dialer.ui.settings

import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.fragment.app.Fragment
import com.google.android.material.button.MaterialButton
import ru.groupprofi.crmprofi.dialer.BuildConfig
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.config.AppFeatures
import ru.groupprofi.crmprofi.dialer.config.TelemetryMode
import ru.groupprofi.crmprofi.dialer.network.PullCallMetrics
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsPanel
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.isActive
import kotlinx.coroutines.cancel
import android.widget.Toast

/**
 * Фрагмент вкладки "Настройки" - статус авторизации, настройки батареи, версия.
 */
class SettingsFragment : Fragment() {
    private lateinit var authStatus: TextView
    private lateinit var batteryButton: MaterialButton
    private lateinit var oemHelpButton: MaterialButton
    private lateinit var diagnosticsCopyButton: MaterialButton
    private lateinit var diagnosticsShareButton: MaterialButton
    private lateinit var connectionCheckButton: MaterialButton
    private lateinit var versionText: TextView
    private lateinit var telemetryModeText: TextView
    private lateinit var pullCallModeText: TextView
    private val updateScope = CoroutineScope(SupervisorJob() + Dispatchers.Main)
    private var devModeTapCount = 0
    private var devModeLastTapTime = 0L
    private var devModeEnabled = false
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_settings, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        authStatus = view.findViewById(R.id.authStatus)
        batteryButton = view.findViewById(R.id.batteryButton)
        oemHelpButton = view.findViewById(R.id.oemHelpButton)
        diagnosticsCopyButton = view.findViewById(R.id.diagnosticsCopyButton)
        diagnosticsShareButton = view.findViewById(R.id.diagnosticsShareButton)
        connectionCheckButton = view.findViewById(R.id.connectionCheckButton)
        versionText = view.findViewById(R.id.versionText)
        telemetryModeText = view.findViewById(R.id.telemetryModeText)
        pullCallModeText = view.findViewById(R.id.pullCallModeText)
        
        updateAuthStatus()
        setupBatteryButton()
        setupOemHelpButton()
        setupDiagnosticsButtons()
        setupConnectionCheckButton()
        updateVersion()
        updateTelemetryMode()
        startPullCallModeUpdates()
        
        // В DEBUG режиме добавляем long press на versionText для открытия диагностики
        if (BuildConfig.DEBUG) {
            versionText.setOnLongClickListener {
                showDiagnosticsDialog()
                true
            }
        } else {
            // В release режиме - 7 тапов для dev mode
            setupDevModeAccess()
        }
    }

    override fun onResume() {
        super.onResume()
        // После возврата из настроек системы (батарея, приложение) обновляем состояние экрана
        updateAuthStatus()
    }
    
    override fun onDestroyView() {
        super.onDestroyView()
        updateScope.cancel()
    }
    
    private fun updateAuthStatus() {
        val tokenManager = TokenManager.getInstance()
        val hasTokens = tokenManager.hasTokens()
        
        if (hasTokens) {
            val username = tokenManager.getUsername() ?: "Пользователь"
            authStatus.text = "Авторизован: $username"
            authStatus.setTextColor(ContextCompat.getColor(requireContext(), R.color.accent))
        } else {
            authStatus.text = "Не авторизован"
            authStatus.setTextColor(ContextCompat.getColor(requireContext(), R.color.error))
        }
    }
    
    private fun setupBatteryButton() {
        batteryButton.setOnClickListener {
            (activity as? ru.groupprofi.crmprofi.dialer.MainActivity)?.openBatteryOptimizationSettings()
        }
    }
    
    private fun setupOemHelpButton() {
        oemHelpButton.setOnClickListener {
            showOemHelpDialog()
        }
    }

    private fun setupDiagnosticsButtons() {
        diagnosticsCopyButton.setOnClickListener {
            val report = DiagnosticsPanel.generateReport(requireContext())
            DiagnosticsPanel.copyToClipboard(requireContext(), report)
            Toast.makeText(requireContext(), "Отчёт скопирован. Вставьте в письмо поддержке.", Toast.LENGTH_SHORT).show()
        }
        diagnosticsShareButton.setOnClickListener {
            val report = DiagnosticsPanel.generateReport(requireContext())
            DiagnosticsPanel.shareReport(requireContext(), report)
        }
    }

    private fun setupConnectionCheckButton() {
        connectionCheckButton.setOnClickListener {
            val mode = PullCallMetrics.currentMode
            val reason = PullCallMetrics.degradationReason
            val lastCmd = PullCallMetrics.getSecondsSinceLastCommand()
            val msg = "Режим: $mode${if (reason.name != "NONE") " ($reason)" else ""}. " +
                (lastCmd?.let { "Последняя команда: ${it}с назад" } ?: "Команд не было.")
            Toast.makeText(requireContext(), msg, Toast.LENGTH_LONG).show()
        }
    }
    
    private fun showOemHelpDialog() {
        val manufacturer = android.os.Build.MANUFACTURER.lowercase()
        val instructions = when {
            manufacturer.contains("xiaomi") || manufacturer.contains("redmi") -> getXiaomiInstructions()
            manufacturer.contains("huawei") || manufacturer.contains("honor") -> getHuaweiInstructions()
            manufacturer.contains("samsung") -> getSamsungInstructions()
            else -> getGenericInstructions()
        }
        
        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle("Настройка работы в фоне")
            .setMessage(instructions)
            .setPositiveButton("Понятно", null)
            .show()
    }
    
    private fun getXiaomiInstructions(): String {
        return "Настройка для Xiaomi/MIUI:\n\n" +
                "1. Настройки → Приложения → Управление разрешениями\n" +
                "2. Найдите \"CRM ПРОФИ\" → Автозапуск → Включить\n" +
                "3. Настройки → Батарея → Оптимизация батареи\n" +
                "4. Найдите \"CRM ПРОФИ\" → Не оптимизировать\n" +
                "5. Настройки → Батарея → Ограничение фоновой активности\n" +
                "6. Найдите \"CRM ПРОФИ\" → Без ограничений"
    }
    
    private fun getHuaweiInstructions(): String {
        return "Настройка для Huawei/Honor:\n\n" +
                "1. Настройки → Приложения → Запуск приложений\n" +
                "2. Найдите \"CRM ПРОФИ\" → Управление вручную → Включить \"Автозапуск\"\n" +
                "3. Настройки → Батарея → Запуск приложений\n" +
                "4. Найдите \"CRM ПРОФИ\" → Включить \"Автозапуск\" и \"Фоновые действия\"\n" +
                "5. Настройки → Батарея → Защищенные приложения\n" +
                "6. Добавьте \"CRM ПРОФИ\" в список защищенных"
    }
    
    private fun getSamsungInstructions(): String {
        return "Настройка для Samsung:\n\n" +
                "1. Настройки → Приложения → CRM ПРОФИ → Батарея → Не оптимизировать\n" +
                "2. Настройки → Батарея → Фоновые ограничения\n" +
                "3. Найдите \"CRM ПРОФИ\" → Не ограничивать"
    }
    
    private fun getGenericInstructions(): String {
        return "Настройка работы в фоне:\n\n" +
                "1. Настройки → Батарея → Оптимизация батареи\n" +
                "2. Найдите \"CRM ПРОФИ\" → Не оптимизировать\n\n" +
                "Альтернативно:\n" +
                "Настройки → Приложения → CRM ПРОФИ → Батарея → Неограниченное использование"
    }
    
    private fun updateVersion() {
        versionText.text = "Версия: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})\nAndroid: ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})"
    }
    
    private fun updateTelemetryMode() {
        val mode = AppFeatures.TELEMETRY_MODE
        val modeText = when (mode) {
            TelemetryMode.LOCAL_ONLY -> "Локальный"
            TelemetryMode.FULL -> "Полный (CRM)"
        }
        telemetryModeText.text = "Режим аналитики: $modeText"
    }
    
    private fun startPullCallModeUpdates() {
        updateScope.launch {
            while (isActive) {
                updatePullCallMode()
                delay(2000) // Обновляем каждые 2 секунды
            }
        }
    }
    
    private fun updatePullCallMode() {
        val mode = PullCallMetrics.currentMode
        val degradationReason = PullCallMetrics.degradationReason
        val lastCommandSeconds = PullCallMetrics.getSecondsSinceLastCommand()
        val avgDeliveryLatency = PullCallMetrics.getAverageDeliveryLatencyMs()
        val count429 = PullCallMetrics.get429CountLastHour()
        val timeInBackoff = PullCallMetrics.getTimeSpentInBackoffMs()
        
        val modeText = when (mode) {
            PullCallMetrics.PullMode.LONG_POLL -> "Long-Poll"
            PullCallMetrics.PullMode.BURST -> "Burst"
            PullCallMetrics.PullMode.SLOW -> "Медленный"
        }
        
        val reasonText = when (degradationReason) {
            PullCallMetrics.DegradationReason.NONE -> ""
            PullCallMetrics.DegradationReason.RATE_LIMIT -> " (Rate Limit)"
            PullCallMetrics.DegradationReason.NETWORK_ERROR -> " (Сеть)"
            PullCallMetrics.DegradationReason.SERVER_ERROR -> " (Сервер)"
        }
        
        val lastCommandText = lastCommandSeconds?.let { ", последняя команда: ${it}с назад" } ?: ", команд не было"
        val avgLatencyText = avgDeliveryLatency?.let { ", средняя доставка: ${it}мс" } ?: ""
        val metricsText = if (BuildConfig.DEBUG && (count429 > 0 || timeInBackoff > 0)) {
            ", 429/час: $count429, backoff: ${timeInBackoff / 1000}с"
        } else {
            ""
        }
        
        pullCallModeText.text = "Режим получения команд: $modeText$reasonText$lastCommandText$avgLatencyText$metricsText"
    }
    
    /**
     * Показать диалог диагностики (DEBUG режим).
     */
    private fun showDiagnosticsDialog() {
        val report = DiagnosticsPanel.generateReport(requireContext())
        
        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle("Диагностика (DEBUG)")
            .setMessage(report.take(2000) + if (report.length > 2000) "\n\n... (отчет обрезан, используйте кнопки ниже)" else "")
            .setPositiveButton("Копировать") { _, _ ->
                DiagnosticsPanel.copyToClipboard(requireContext(), report)
                Toast.makeText(requireContext(), "Отчет скопирован в буфер обмена", Toast.LENGTH_SHORT).show()
            }
            .setNeutralButton("Поделиться") { _, _ ->
                DiagnosticsPanel.shareReport(requireContext(), report)
            }
            .setNegativeButton("Закрыть", null)
            .show()
    }
    
    /**
     * Настроить доступ к dev mode через 7 тапов (release-safe).
     */
    private fun setupDevModeAccess() {
        versionText.setOnClickListener {
            val currentTime = System.currentTimeMillis()
            
            // Сбрасываем счетчик, если прошло больше 2 секунд с последнего тапа
            if (currentTime - devModeLastTapTime > 2000) {
                devModeTapCount = 0
            }
            
            devModeTapCount++
            devModeLastTapTime = currentTime
            
            // Если 7 тапов - включаем dev mode
            if (devModeTapCount >= 7) {
                devModeEnabled = true
                devModeTapCount = 0
                Toast.makeText(requireContext(), "Dev mode enabled", Toast.LENGTH_SHORT).show()
                
                // Добавляем long press для диагностики
                versionText.setOnLongClickListener {
                    showDiagnosticsDialog()
                    true
                }
            }
        }
    }
}
