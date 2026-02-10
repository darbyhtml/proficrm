package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.Menu
import android.view.MenuItem
import android.widget.Button
import android.widget.EditText
import android.widget.ScrollView
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import ru.groupprofi.crmprofi.dialer.BuildConfig
import java.text.SimpleDateFormat
import java.util.*

/**
 * Экран логов приложения.
 * Показывает последние логи приложения, статус очереди и статистику.
 * Поддерживает фильтрацию, поиск и экспорт.
 * 
 * ВРЕМЕННО: доступ для всех залогиненных пользователей (для дебага).
 * TODO: В будущем ограничить доступ только администраторам через AppLogger.canViewLogs().
 */
class LogsActivity : AppCompatActivity() {
    private var tokenManager: TokenManager? = null
    private lateinit var logsText: TextView
    private lateinit var statusText: TextView
    private lateinit var refreshBtn: Button
    private lateinit var sendLogsBtn: Button
    private lateinit var searchEditText: EditText
    private lateinit var filterLevelBtn: Button
    
    private val dateFormat = SimpleDateFormat("HH:mm:ss", Locale.getDefault())
    private var currentFilterLevel: String? = null // null = все уровни, "E", "W", "I", "D"
    private var currentSearchQuery: String = ""
    private var allLogs: List<ru.groupprofi.crmprofi.dialer.logs.AppLogger.LogEntry> = emptyList()
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_logs)

        // Не падаем, если TokenManager ещё не успел инициализироваться — экрана логов это не блокирует.
        val tm = TokenManager.getInstanceOrNull()
        if (tm == null) {
            android.util.Log.w("LogsActivity", "TokenManager not ready, some stats may be unavailable")
        }
        tokenManager = tm
        
        // ВРЕМЕННО: доступ для всех пользователей (для дебага)
        // Убрана проверка роли - все залогиненные пользователи могут видеть логи
        // TODO: В будущем вернуть проверку через AppLogger.canViewLogs() когда определимся с ролями
        
        logsText = findViewById(R.id.logsText)
        statusText = findViewById(R.id.logsStatusText)
        refreshBtn = findViewById(R.id.refreshLogsBtn)
        sendLogsBtn = findViewById(R.id.sendLogsBtn)
        searchEditText = findViewById(R.id.searchEditText)
        filterLevelBtn = findViewById(R.id.filterLevelBtn)
        
        refreshBtn.setOnClickListener {
            refreshLogs()
        }
        
        sendLogsBtn.setOnClickListener {
            sendLogsToServer()
        }
        
        filterLevelBtn.setOnClickListener {
            showLevelFilterDialog()
        }
        
        // Поиск в реальном времени
        searchEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                currentSearchQuery = s?.toString()?.trim() ?: ""
                filterAndDisplayLogs()
            }
        })
        
        // Автоматически обновляем логи при открытии
        refreshLogs()
    }
    
    override fun onCreateOptionsMenu(menu: Menu): Boolean {
        menuInflater.inflate(R.menu.logs_menu, menu)
        return true
    }
    
    override fun onOptionsItemSelected(item: MenuItem): Boolean {
        return when (item.itemId) {
            R.id.menu_export_logs -> {
                exportLogs()
                true
            }
            R.id.menu_clear_logs -> {
                clearLogs()
                true
            }
            else -> super.onOptionsItemSelected(item)
        }
    }
    
    private fun refreshLogs() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                // Получаем логи из AppLogger (единый источник)
                allLogs = withContext(Dispatchers.IO) { 
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.getRecentLogs(3000)
                }
                
                // Получаем статистику очереди (с обработкой ошибок)
                val queueStats = try {
                    val queueManager = QueueManager(this@LogsActivity)
                    withContext(Dispatchers.IO) { queueManager.getStats() }
                } catch (e: Exception) {
                    // Если база данных не инициализирована (например, Room не сгенерировал классы),
                    // возвращаем пустую статистику
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("LogsActivity", "Failed to get queue stats: ${e.message}", e)
                    ru.groupprofi.crmprofi.dialer.queue.QueueManager.QueueStats(
                        total = 0,
                        callUpdate = 0,
                        heartbeat = 0,
                        telemetry = 0,
                        logBundle = 0
                    )
                }
                
                // Получаем статус последнего polling (если TokenManager уже инициализирован)
                val tmLocal = tokenManager
                val lastPollCode = tmLocal?.getLastPollCode() ?: -1
                val lastPollAt = tmLocal?.getLastPollAt()
                val deviceId = tmLocal?.getDeviceId() ?: "unknown"
                val maskedDeviceId = if (deviceId.length > 8) {
                    "${deviceId.take(4)}***${deviceId.takeLast(4)}"
                } else {
                    "***"
                }
                
                // Формируем текст статуса
                val statusBuilder = StringBuilder()
                statusBuilder.append("📊 Статус приложения\n\n")
                statusBuilder.append("Версия: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})\n")
                statusBuilder.append("Device ID: $maskedDeviceId\n")
                statusBuilder.append("\nПоследний опрос: ")
                if (lastPollAt.isNullOrBlank()) {
                    statusBuilder.append("нет данных\n")
                } else {
                    statusBuilder.append("$lastPollAt (код: $lastPollCode)\n")
                }
                statusBuilder.append("\n📦 Очередь:\n")
                if (queueStats.total == 0 && allLogs.isEmpty()) {
                    statusBuilder.append("База данных не инициализирована\n")
                    statusBuilder.append("(Room классы не сгенерированы)\n")
                    statusBuilder.append("Выполните: ./gradlew clean build\n")
                } else {
                    statusBuilder.append("Всего: ${queueStats.total}\n")
                    statusBuilder.append("Call updates: ${queueStats.callUpdate}\n")
                    statusBuilder.append("Heartbeat: ${queueStats.heartbeat}\n")
                    statusBuilder.append("Telemetry: ${queueStats.telemetry}\n")
                    statusBuilder.append("Logs: ${queueStats.logBundle}\n")
                }
                statusBuilder.append("\n📋 Логи в буфере: ${allLogs.size}\n")
                
                runOnUiThread {
                    statusText.text = statusBuilder.toString()
                    filterAndDisplayLogs()
                    
                    // Прокручиваем вниз
                    findViewById<ScrollView>(R.id.logsScrollView)?.post {
                        findViewById<ScrollView>(R.id.logsScrollView)?.fullScroll(android.view.View.FOCUS_DOWN)
                    }
                }
            } catch (e: Exception) {
                android.util.Log.e("LogsActivity", "Error refreshing logs: ${e.message}", e)
                runOnUiThread {
                    statusText.text = "Ошибка загрузки: ${e.message}"
                    logsText.text = "Ошибка: ${e.message}\n${e.stackTraceToString()}"
                }
            }
        }
    }
    
    private fun filterAndDisplayLogs() {
        val filtered = allLogs.filter { entry ->
            // Фильтр по уровню
            val levelMatch = currentFilterLevel == null || entry.level == currentFilterLevel
            // Фильтр по поисковому запросу
            val searchMatch = currentSearchQuery.isEmpty() || 
                entry.tag.contains(currentSearchQuery, ignoreCase = true) ||
                entry.message.contains(currentSearchQuery, ignoreCase = true)
            
            levelMatch && searchMatch
        }
        
        // Формируем текст логов
        val logsBuilder = StringBuilder()
        logsBuilder.append("📋 Логи (${filtered.size} из ${allLogs.size}):\n\n")
        
        if (filtered.isEmpty()) {
            logsBuilder.append("Логи отсутствуют")
            if (currentFilterLevel != null || currentSearchQuery.isNotEmpty()) {
                logsBuilder.append(" (применены фильтры)")
            }
            logsBuilder.append("\n")
        } else {
            for (entry in filtered) {
                val timeStr = dateFormat.format(Date(entry.timestamp))
                val levelIcon = when (entry.level) {
                    "E" -> "❌"
                    "W" -> "⚠️"
                    "I" -> "ℹ️"
                    "D" -> "🔍"
                    "V" -> "🔎"
                    else -> "•"
                }
                logsBuilder.append("$timeStr $levelIcon [${entry.tag}] ${entry.message}\n")
            }
        }
        
        logsText.text = logsBuilder.toString()
        
        // Обновляем текст кнопки фильтра
        val filterText = when (currentFilterLevel) {
            "E" -> "❌ Только ошибки"
            "W" -> "⚠️ Только предупреждения"
            "I" -> "ℹ️ Только информация"
            "D" -> "🔍 Только отладка"
            else -> "🔍 Все уровни"
        }
        filterLevelBtn.text = filterText
    }
    
    private fun showLevelFilterDialog() {
        val levels = arrayOf("Все уровни", "❌ Ошибки (E)", "⚠️ Предупреждения (W)", "ℹ️ Информация (I)", "🔍 Отладка (D)")
        val levelValues = arrayOf<String?>(null, "E", "W", "I", "D")
        
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("Фильтр по уровню")
            .setItems(levels) { _, which ->
                currentFilterLevel = levelValues[which]
                filterAndDisplayLogs()
            }
            .show()
    }
    
    private fun exportLogs() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val logs = withContext(Dispatchers.IO) { 
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.getAllLogs()
                }
                
                if (logs.isEmpty()) {
                    runOnUiThread {
                        android.widget.Toast.makeText(this@LogsActivity, "Нет логов для экспорта", android.widget.Toast.LENGTH_SHORT).show()
                    }
                    return@launch
                }
                
                val fullDateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
                val exportText = StringBuilder()
                exportText.append("=== Логи приложения CRM Dialer ===\n")
                exportText.append("Экспортировано: ${fullDateFormat.format(Date())}\n")
                exportText.append("Всего записей: ${logs.size}\n\n")
                
                for (entry in logs) {
                    val timeStr = fullDateFormat.format(Date(entry.timestamp))
                    exportText.append("$timeStr ${entry.level}/${entry.tag}: ${entry.message}\n")
                }
                
                // Экспортируем через Share Intent
                val shareIntent = Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_TEXT, exportText.toString())
                    putExtra(Intent.EXTRA_SUBJECT, "Логи CRM Dialer")
                }
                
                runOnUiThread {
                    startActivity(Intent.createChooser(shareIntent, "Экспортировать логи"))
                }
            } catch (e: Exception) {
                android.util.Log.e("LogsActivity", "Error exporting logs: ${e.message}", e)
                runOnUiThread {
                    android.widget.Toast.makeText(this@LogsActivity, "Ошибка экспорта: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }
    
    private fun clearLogs() {
        androidx.appcompat.app.AlertDialog.Builder(this)
            .setTitle("Очистить логи")
            .setMessage("Очистить буфер логов? Это действие нельзя отменить.")
            .setPositiveButton("Очистить") { _, _ ->
                CoroutineScope(Dispatchers.IO).launch {
                    try {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.clearLogs()
                        
                        runOnUiThread {
                            android.widget.Toast.makeText(this@LogsActivity, "Логи очищены", android.widget.Toast.LENGTH_SHORT).show()
                            refreshLogs()
                        }
                    } catch (e: Exception) {
                        android.util.Log.e("LogsActivity", "Error clearing logs: ${e.message}", e)
                        runOnUiThread {
                            android.widget.Toast.makeText(this@LogsActivity, "Ошибка очистки: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
                        }
                    }
                }
            }
            .setNegativeButton("Отмена", null)
            .show()
    }
    
    private fun sendLogsToServer() {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val logCollector = try {
                    (application as? CRMApplication)?.logCollector
                } catch (e: Exception) {
                    android.util.Log.w("LogsActivity", "Cannot get LogCollector: ${e.message}")
                    null
                }
                if (logCollector == null) {
                    runOnUiThread {
                        android.widget.Toast.makeText(this@LogsActivity, "LogCollector недоступен", android.widget.Toast.LENGTH_SHORT).show()
                    }
                    return@launch
                }
                
                val bundle = withContext(Dispatchers.IO) { logCollector.takeLogs(maxEntries = 500) }
                if (bundle == null) {
                    runOnUiThread {
                        android.widget.Toast.makeText(this@LogsActivity, "Нет логов для отправки", android.widget.Toast.LENGTH_SHORT).show()
                    }
                    return@launch
                }
                
                val apiClient = ru.groupprofi.crmprofi.dialer.network.ApiClient.getInstance(this@LogsActivity)
                val deviceId = tokenManager?.getDeviceId() ?: "unknown"
                
                // Формируем параметры для sendLogBundle
                val now = java.util.Date()
                val tsFormat = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", java.util.Locale.US)
                tsFormat.timeZone = java.util.TimeZone.getTimeZone("UTC")
                val ts = tsFormat.format(now)
                
                val result = apiClient.sendLogBundle(
                    deviceId = deviceId,
                    ts = ts,
                    levelSummary = bundle.levelSummary,
                    source = bundle.source,
                    payload = bundle.payload
                )
                
                runOnUiThread {
                    when (result) {
                        is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success -> {
                            android.widget.Toast.makeText(this@LogsActivity, "Логи отправлены (${bundle.entryCount} записей)", android.widget.Toast.LENGTH_SHORT).show()
                            refreshLogs()
                        }
                        is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Error -> {
                            android.widget.Toast.makeText(this@LogsActivity, "Ошибка отправки: ${result.message}", android.widget.Toast.LENGTH_LONG).show()
                        }
                    }
                }
            } catch (e: Exception) {
                android.util.Log.e("LogsActivity", "Error sending logs: ${e.message}", e)
                runOnUiThread {
                    android.widget.Toast.makeText(this@LogsActivity, "Ошибка: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
                }
            }
        }
    }
}
