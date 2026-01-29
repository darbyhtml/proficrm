package ru.groupprofi.crmprofi.dialer.network

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.Interceptor
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor

/**
 * Безопасный HTTP logging interceptor с маскированием чувствительных данных.
 * Включается только в debug сборках.
 * 
 * ВАЖНО: Маскирование выполняется на фоновом потоке, чтобы не блокировать main thread.
 */
class SafeHttpLoggingInterceptor : Interceptor {
    private val loggingScope = CoroutineScope(Dispatchers.Default)
    
    private val delegate = HttpLoggingInterceptor(object : HttpLoggingInterceptor.Logger {
        override fun log(message: String) {
            // Маскируем чувствительные данные на фоновом потоке (regex/replace операции могут быть тяжелыми)
            loggingScope.launch {
                try {
                    val masked = maskSensitiveData(message)
                    Log.d("OkHttp", masked)
                } catch (e: Exception) {
                    // Если маскирование упало - логируем оригинальное сообщение без маскирования
                    Log.d("OkHttp", message)
                    Log.w("SafeHttpLoggingInterceptor", "Failed to mask sensitive data: ${e.message}")
                }
            }
        }
    }).apply {
        level = HttpLoggingInterceptor.Level.BODY
    }
    
    override fun intercept(chain: Interceptor.Chain): Response {
        return delegate.intercept(chain)
    }
    
    /**
     * Маскирует чувствительные данные в тексте.
     * Важно: сохраняет корректный JSON формат при маскировании.
     * Стратегия: сначала обрабатываем JSON поля (они имеют четкий паттерн "key":"value"),
     * затем обрабатываем query параметры и другие форматы, избегая повторной обработки JSON.
     */
    private fun maskSensitiveData(text: String): String {
        var masked = text
        
        // Маскируем Bearer токены в заголовках
        masked = masked.replace(Regex("""Bearer\s+[A-Za-z0-9\-_\.]+"""), "Bearer ***")
        
        // ШАГ 1: Маскируем JSON поля (строгий паттерн "key":"value")
        // Это гарантирует, что JSON формат не будет испорчен
        
        // Маскируем access/refresh токены в JSON формате ("access":"value" -> "access":"masked")
        masked = masked.replace(Regex("""("(?:access|refresh|token)"\s*:\s*")([A-Za-z0-9\-_\.]{20,})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            "${matchResult.groupValues[1]}masked${matchResult.groupValues[3]}"
        }
        
        // Маскируем пароли в JSON формате ("password":"value" -> "password":"masked")
        masked = masked.replace(Regex("""("(?:password|passwd|pwd)"\s*:\s*")([^"]+)(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            "${matchResult.groupValues[1]}masked${matchResult.groupValues[3]}"
        }
        
        // Маскируем device_id в JSON формате ("device_id":"value" -> "device_id":"9982***6682")
        // КРИТИЧНО: сохраняем строгий JSON формат с двойными кавычками
        masked = masked.replace(Regex("""("device_id"\s*:\s*")([A-Za-z0-9]{8,})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            val id = matchResult.groupValues[2]
            val prefix = matchResult.groupValues[1] // "device_id":"
            val suffix = matchResult.groupValues[3] // "
            if (id.length > 8) {
                "$prefix${id.take(4)}***${id.takeLast(4)}$suffix"
            } else {
                "$prefix***$suffix"
            }
        }
        
        // Маскируем номера телефонов в JSON формате ("phone":"+79991234567" -> "phone":"***4567")
        masked = masked.replace(Regex("""("(?:phone|number)"\s*:\s*")(\+?[0-9\s\-\(\)]{7,})([0-9]{4})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            val last4 = matchResult.groupValues[3]
            "${matchResult.groupValues[1]}***$last4${matchResult.groupValues[4]}"
        }
        
        // ШАГ 2: Маскируем query параметры и другие не-JSON форматы
        // Важно: применяем только к паттернам, которые НЕ являются частью JSON строки
        // Используем более строгую проверку: ищем паттерны вне JSON контекста
        
        // Маскируем device_id в query параметрах (device_id=value -> device_id=9982***6682)
        // НЕ добавляем кавычки - это query параметр, не JSON
        // ВАЖНО: } в lookahead должна быть экранирована или в charclass, иначе PatternSyntaxException на некоторых Android версиях
        masked = masked.replace(Regex("""device[_\s]?id[=:]([A-Za-z0-9]{8,})(?=$|[\s&}])""", RegexOption.IGNORE_CASE)) { matchResult ->
            val beforeMatch = masked.substring(0, matchResult.range.first)
            // Проверяем контекст: если перед match есть нечетное количество кавычек - мы внутри JSON строки
            // Также проверяем, что это действительно query параметр (есть = или : без кавычек вокруг)
            val quotesBefore = beforeMatch.count { it == '"' }
            val isInJsonString = quotesBefore % 2 != 0
            
            // Дополнительная проверка: если перед match есть "device_id": - это уже обработанный JSON
            val contextBefore = beforeMatch.takeLast(20)
            val isAlreadyMaskedJson = contextBefore.contains("\"device_id\"")
            
            if (!isInJsonString && !isAlreadyMaskedJson) {
                // Это query параметр - маскируем без кавычек
                val id = matchResult.groupValues[1]
                val prefix = matchResult.value.substring(0, matchResult.value.indexOf(id))
                if (id.length > 8) {
                    "${prefix}${id.take(4)}***${id.takeLast(4)}"
                } else {
                    "${prefix}***"
                }
            } else {
                // Уже обработано как JSON или внутри JSON строки - не трогаем
                matchResult.value
            }
        }
        
        // Маскируем полные URL с query параметрами (оставляем только путь)
        masked = masked.replace(Regex("""https?://[^\s"']+(\?[^\s"']+)"""), "***")
        
        return masked
    }
}
