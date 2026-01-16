package ru.groupprofi.crmprofi.dialer.logs

import android.util.Log
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.ConcurrentLinkedQueue
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Простой сборщик логов для отправки в CRM.
 * Собирает логи в память и периодически формирует бандлы для отправки.
 */
class LogCollector {
    private val logBuffer = ConcurrentLinkedQueue<LogEntry>()
    private val mutex = Mutex()
    private val maxBufferSize = 1000 // Максимум логов в буфере
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss.SSS", Locale.US)
    
    data class LogEntry(
        val timestamp: Long,
        val level: String,
        val tag: String,
        val message: String
    )
    
    /**
     * Добавить лог в буфер.
     */
    fun addLog(level: Int, tag: String, message: String) {
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
            message = message
        )
        
        logBuffer.offer(entry)
        
        // Ограничиваем размер буфера
        while (logBuffer.size > maxBufferSize) {
            logBuffer.poll()
        }
    }
    
    /**
     * Получить и очистить накопленные логи (формирует бандл).
     * Возвращает null, если логов нет.
     */
    suspend fun takeLogs(maxEntries: Int = 500): LogBundle? {
        return mutex.withLock {
            if (logBuffer.isEmpty()) {
                return null
            }
            
            val entries = mutableListOf<LogEntry>()
            var count = 0
            val iterator = logBuffer.iterator()
            
            while (iterator.hasNext() && count < maxEntries) {
                entries.add(iterator.next())
                iterator.remove()
                count++
            }
            
            if (entries.isEmpty()) {
                return null
            }
            
            // Формируем строковое представление логов
            val payload = StringBuilder()
            var errorCount = 0
            var warnCount = 0
            
            for (entry in entries) {
                val timeStr = dateFormat.format(Date(entry.timestamp))
                payload.append("$timeStr ${entry.level}/${entry.tag}: ${entry.message}\n")
                
                if (entry.level == "E") errorCount++
                if (entry.level == "W") warnCount++
            }
            
            // Формируем summary уровня
            val levelSummary = when {
                errorCount > 0 -> "ERROR($errorCount)"
                warnCount > 0 -> "WARN($warnCount)"
                else -> "INFO"
            }
            
            LogBundle(
                levelSummary = levelSummary,
                source = "logcat",
                payload = payload.toString(),
                entryCount = entries.size
            )
        }
    }
    
    /**
     * Получить количество логов в буфере (без блокировки).
     */
    fun getBufferSize(): Int = logBuffer.size
    
    /**
     * Получить последние логи БЕЗ очистки буфера (для просмотра).
     * Возвращает список последних N записей.
     */
    suspend fun getRecentLogs(maxEntries: Int = 1000): List<LogEntry> {
        return mutex.withLock {
            val entries = mutableListOf<LogEntry>()
            var count = 0
            // Берем последние записи (они в порядке добавления)
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
    
    data class LogBundle(
        val levelSummary: String,
        val source: String,
        val payload: String,
        val entryCount: Int
    )
}
