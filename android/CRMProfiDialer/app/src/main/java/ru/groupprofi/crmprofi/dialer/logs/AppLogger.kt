package ru.groupprofi.crmprofi.dialer.logs

import android.content.Context
import android.util.Log
import ru.groupprofi.crmprofi.dialer.BuildConfig
import java.io.File
import java.io.FileWriter
import java.io.PrintWriter
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.ConcurrentLinkedQueue
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch

/**
 * Единый централизованный логгер для всего приложения.
 * 
 * Особенности:
 * - Thread-safe (ConcurrentLinkedQueue + Mutex)
 * - Маскирует чувствительные данные при записи
 * - Хранит логи в памяти (кольцевой буфер) и опционально в файл
 * - Доступен из Activity и Service через singleton
 * 
 * TODO: В будущем можно ограничить доступ к логам только администраторам через canViewLogs()
 */
object AppLogger {
    private const val MAX_BUFFER_SIZE = 3000 // Увеличенный буфер для дебага
    private const val LOG_FILE_NAME = "app_logs.txt"
    private const val MAX_LOG_FILE_SIZE = 5 * 1024 * 1024 // 5 MB
    
    private val logBuffer = ConcurrentLinkedQueue<LogEntry>()
    private val mutex = Mutex()
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
    
    // Coroutine scope для асинхронной записи в файл
    private val fileWriteScope = CoroutineScope(Dispatchers.IO + SupervisorJob())
    
    @Volatile
    private var context: Context? = null
    
    @Volatile
    private var fileLoggingEnabled = false
    
    data class LogEntry(
        val timestamp: Long,
        val level: String,
        val tag: String,
        val message: String
    )
    
    /**
     * Инициализация логгера (вызывается из Application.onCreate).
     */
    fun initialize(appContext: Context, enableFileLogging: Boolean = true) {
        context = appContext.applicationContext
        fileLoggingEnabled = enableFileLogging
        
        // Очищаем старый файл асинхронно на фоновом потоке (не блокируем main thread)
        if (fileLoggingEnabled) {
            fileWriteScope.launch {
                cleanupOldLogFile()
            }
        }
        
        // Подключаем к существующему LogCollector для совместимости
        val logCollector = try {
            (appContext as? ru.groupprofi.crmprofi.dialer.CRMApplication)?.logCollector
        } catch (e: Exception) {
            null
        }
        if (logCollector != null) {
            LogInterceptor.setCollector(logCollector)
        }
    }
    
    /**
     * Проверка, может ли пользователь просматривать логи.
     * Сейчас всегда true (для дебага), позже будет зависеть от is_admin.
     * 
     * TODO: В будущем заменить на проверку роли:
     *   val tokenManager = TokenManager.getInstance(context ?: return false)
     *   return tokenManager.isAdmin()
     */
    fun canViewLogs(): Boolean {
        // ВРЕМЕННО: доступ для всех пользователей (для дебага)
        return true
        
        // TODO: Раскомментировать после завершения дебага:
        // val ctx = context ?: return false
        // val tokenManager = ru.groupprofi.crmprofi.dialer.auth.TokenManager.getInstance(ctx)
        // return tokenManager.isAdmin()
    }
    
    /**
     * Debug лог.
     */
    fun d(tag: String, message: String) {
        log(Log.DEBUG, tag, message)
    }
    
    /**
     * Info лог.
     */
    fun i(tag: String, message: String) {
        log(Log.INFO, tag, message)
    }
    
    /**
     * Warning лог.
     */
    fun w(tag: String, message: String) {
        log(Log.WARN, tag, message)
    }
    
    /**
     * Error лог.
     */
    fun e(tag: String, message: String) {
        log(Log.ERROR, tag, message)
    }
    
    /**
     * Error лог с исключением.
     */
    fun e(tag: String, message: String, throwable: Throwable) {
        log(Log.ERROR, tag, "$message\n${throwable.stackTraceToString()}")
    }
    
    /**
     * Основной метод логирования.
     * Маскирует чувствительные данные перед записью.
     * В release режиме пропускает DEBUG логи для уменьшения спама.
     */
    private fun log(level: Int, tag: String, message: String) {
        // В release режиме пропускаем DEBUG логи
        if (!BuildConfig.DEBUG && level == Log.DEBUG) {
            return
        }
        
        // Всегда пишем в системный log (кроме DEBUG в release)
        when (level) {
            Log.DEBUG -> Log.d(tag, message)
            Log.INFO -> Log.i(tag, message)
            Log.WARN -> Log.w(tag, message)
            Log.ERROR -> Log.e(tag, message)
        }
        
        // Маскируем чувствительные данные перед записью в буфер
        val maskedMessage = maskSensitiveData(message)
        
        val levelStr = when (level) {
            Log.VERBOSE -> "V"
            Log.DEBUG -> "D"
            Log.INFO -> "I"
            Log.WARN -> "W"
            Log.ERROR -> "E"
            Log.ASSERT -> "A"
            else -> "?"
        }
        
        val entry = LogEntry(
            timestamp = System.currentTimeMillis(),
            level = levelStr,
            tag = tag,
            message = maskedMessage
        )
        
        // Добавляем в буфер
        logBuffer.offer(entry)
        while (logBuffer.size > MAX_BUFFER_SIZE) {
            logBuffer.poll()
        }
        
        // Пишем в файл асинхронно на фоновом потоке (не блокируем main thread)
        if (fileLoggingEnabled) {
            fileWriteScope.launch {
                writeToFile(entry)
            }
        }
        
        // Также пишем в существующий LogCollector для совместимости
        LogInterceptor.addLog(level, tag, maskedMessage)
    }
    
    /**
     * Получить последние логи БЕЗ очистки буфера.
     */
    suspend fun getRecentLogs(maxEntries: Int = MAX_BUFFER_SIZE): List<LogEntry> {
        return mutex.withLock {
            val entries = mutableListOf<LogEntry>()
            var count = 0
            val iterator = logBuffer.iterator()
            
            while (iterator.hasNext() && count < maxEntries) {
                entries.add(iterator.next())
                count++
            }
            
            entries
        }
    }
    
    /**
     * Получить все логи БЕЗ очистки буфера (для экспорта).
     */
    suspend fun getAllLogs(): List<LogEntry> {
        return mutex.withLock {
            logBuffer.toList()
        }
    }
    
    /**
     * Очистить буфер логов.
     */
    suspend fun clearLogs() {
        mutex.withLock {
            logBuffer.clear()
        }
    }
    
    /**
     * Получить количество логов в буфере.
     */
    fun getBufferSize(): Int = logBuffer.size
    
    /**
     * Маскирует чувствительные данные в сообщении.
     * Использует ту же логику, что и LogSender.maskSensitiveData.
     */
    private fun maskSensitiveData(text: String): String {
        var masked = text
        
        // Маскируем Bearer токены
        masked = masked.replace(Regex("""Bearer\s+[A-Za-z0-9\-_\.]+"""), "Bearer ***")
        
        // Маскируем access/refresh токены в JSON
        masked = masked.replace(Regex("""(access|refresh|token)["\s:=]+([A-Za-z0-9\-_\.]{20,})"""), "$1=\"***\"")
        
        // Маскируем пароли
        masked = masked.replace(Regex("""(password|passwd|pwd)["\s:=]+([^\s"']+)""", RegexOption.IGNORE_CASE), "$1=\"***\"")
        
        // Маскируем device_id
        masked = masked.replace(Regex("""device[_\s]?id["\s:=]+([A-Za-z0-9]{8,})""", RegexOption.IGNORE_CASE)) { matchResult ->
            val id = matchResult.groupValues[1]
            if (id.length > 8) {
                "device_id=\"${id.take(4)}***${id.takeLast(4)}\""
            } else {
                "device_id=\"***\""
            }
        }
        
        // Маскируем номера телефонов (оставляем последние 4 цифры)
        masked = masked.replace(Regex("""(\+?[0-9]{1,3}[\s\-]?)?([0-9]{3,4}[\s\-]?[0-9]{2,3}[\s\-]?)([0-9]{4})""")) { matchResult ->
            val last4 = matchResult.groupValues[3]
            "***$last4"
        }
        
        // Маскируем Authorization header
        masked = masked.replace(Regex("""Authorization\s*:\s*Bearer\s+[A-Za-z0-9\-_\.]+""", RegexOption.IGNORE_CASE), "Authorization: Bearer ***")
        
        return masked
    }
    
    /**
     * Записать лог в файл (вызывается из фонового потока через fileWriteScope).
     */
    private suspend fun writeToFile(entry: LogEntry) {
        val ctx = context ?: return
        
        try {
            val logFile = File(ctx.filesDir, LOG_FILE_NAME)
            
            // Проверяем размер файла
            if (logFile.exists() && logFile.length() > MAX_LOG_FILE_SIZE) {
                // Переименовываем старый файл
                val backupFile = File(ctx.filesDir, "${LOG_FILE_NAME}.old")
                logFile.renameTo(backupFile)
            }
            
            // Пишем в файл (append mode)
            FileWriter(logFile, true).use { writer ->
                PrintWriter(writer).use { pw ->
                    val timeStr = dateFormat.format(Date(entry.timestamp))
                    pw.println("$timeStr ${entry.level}/${entry.tag}: ${entry.message}")
                }
            }
        } catch (e: Exception) {
            // Игнорируем ошибки записи в файл (не критично)
            Log.w("AppLogger", "Failed to write log to file: ${e.message}")
        }
    }
    
    /**
     * Очистить старый лог-файл, если он слишком большой.
     */
    private fun cleanupOldLogFile() {
        val ctx = context ?: return
        
        try {
            val logFile = File(ctx.filesDir, LOG_FILE_NAME)
            if (logFile.exists() && logFile.length() > MAX_LOG_FILE_SIZE) {
                logFile.delete()
            }
        } catch (e: Exception) {
            // Игнорируем ошибки
        }
    }
}
