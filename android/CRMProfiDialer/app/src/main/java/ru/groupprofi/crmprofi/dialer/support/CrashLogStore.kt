package ru.groupprofi.crmprofi.dialer.support

import android.content.Context
import android.content.SharedPreferences
import ru.groupprofi.crmprofi.dialer.logs.AppLogger

/**
 * Хранилище информации о последнем сбое приложения.
 * Используется только в режиме поддержки для диагностики.
 */
object CrashLogStore {
    
    private const val PREFS_NAME = "crash_log_prefs"
    private const val KEY_LAST_CRASH_TIME = "last_crash_time_millis"
    private const val KEY_LAST_CRASH_SUMMARY = "last_crash_summary"
    private const val MAX_SUMMARY_LENGTH = 8192 // 8KB максимум
    
    /**
     * Сохранить информацию о сбое.
     */
    fun saveCrash(context: Context, exceptionClass: String, message: String?, stacktrace: String) {
        try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            
            // Собираем краткую сводку (первые 30 строк stacktrace)
            val stacktraceLines = stacktrace.lines().take(30)
            val shortStacktrace = stacktraceLines.joinToString("\n")
            
            // Маскируем потенциальные номера телефонов в тексте
            val maskedMessage = message?.let { maskPhoneNumbers(it) } ?: ""
            val maskedStacktrace = maskPhoneNumbers(shortStacktrace)
            
            val summary = buildString {
                append("Exception: $exceptionClass\n")
                if (maskedMessage.isNotEmpty()) {
                    append("Message: $maskedMessage\n")
                }
                append("Stacktrace:\n$maskedStacktrace")
            }
            
            // Ограничиваем размер
            val finalSummary = if (summary.length > MAX_SUMMARY_LENGTH) {
                summary.take(MAX_SUMMARY_LENGTH) + "\n... (обрезано)"
            } else {
                summary
            }
            
            prefs.edit()
                .putLong(KEY_LAST_CRASH_TIME, System.currentTimeMillis())
                .putString(KEY_LAST_CRASH_SUMMARY, finalSummary)
                .apply()
            
            AppLogger.w("CrashLogStore", "Сохранена информация о сбое: $exceptionClass")
        } catch (e: Exception) {
            // Не логируем через AppLogger, чтобы не создать рекурсию
            android.util.Log.e("CrashLogStore", "Ошибка сохранения сбоя: ${e.message}")
        }
    }
    
    /**
     * Получить timestamp последнего сбоя.
     */
    fun getLastCrashTime(context: Context): Long? {
        return try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            val timestamp = prefs.getLong(KEY_LAST_CRASH_TIME, 0L)
            if (timestamp > 0) timestamp else null
        } catch (e: Exception) {
            null
        }
    }
    
    /**
     * Получить сводку последнего сбоя.
     */
    fun getLastCrashSummary(context: Context): String? {
        return try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            prefs.getString(KEY_LAST_CRASH_SUMMARY, null)
        } catch (e: Exception) {
            null
        }
    }
    
    /**
     * Очистить информацию о сбоях.
     */
    fun clear(context: Context) {
        try {
            val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
            prefs.edit()
                .remove(KEY_LAST_CRASH_TIME)
                .remove(KEY_LAST_CRASH_SUMMARY)
                .apply()
        } catch (e: Exception) {
            android.util.Log.e("CrashLogStore", "Ошибка очистки: ${e.message}")
        }
    }
    
    /**
     * Маскировать номера телефонов в тексте.
     * Простая regex для поиска паттернов типа +7XXXXXXXXXX, 8XXXXXXXXXX и т.д.
     */
    private fun maskPhoneNumbers(text: String): String {
        // Паттерн для номеров телефонов (российские форматы)
        val phonePattern = Regex("""(\+?7|8)?[\s\-\(\)]?(\d{3})[\s\-\(\)]?(\d{3})[\s\-\(\)]?(\d{2})[\s\-\(\)]?(\d{2})""")
        
        return phonePattern.replace(text) { matchResult ->
            // Оставляем первые 3 цифры и последние 2, остальное маскируем
            val fullNumber = matchResult.value.replace(Regex("""[\s\-\(\)]"""), "")
            if (fullNumber.length >= 5) {
                val prefix = fullNumber.take(3)
                val suffix = fullNumber.takeLast(2)
                "$prefix***$suffix"
            } else {
                "***"
            }
        }
    }
}
