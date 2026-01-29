package ru.groupprofi.crmprofi.dialer.network

import android.util.Log
import okhttp3.Interceptor
import okhttp3.Response
import okhttp3.logging.HttpLoggingInterceptor

/**
 * Безопасный HTTP logging interceptor с маскированием чувствительных данных.
 * Включается только в debug сборках.
 */
class SafeHttpLoggingInterceptor : Interceptor {
    private val delegate = HttpLoggingInterceptor(object : HttpLoggingInterceptor.Logger {
        override fun log(message: String) {
            // Маскируем чувствительные данные перед логированием
            val masked = maskSensitiveData(message)
            Log.d("OkHttp", masked)
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
     */
    private fun maskSensitiveData(text: String): String {
        var masked = text
        
        // Маскируем Bearer токены в заголовках
        masked = masked.replace(Regex("""Bearer\s+[A-Za-z0-9\-_\.]+"""), "Bearer ***")
        
        // Маскируем access/refresh токены в JSON формате ("access":"value" -> "access":"masked")
        masked = masked.replace(Regex("""("(?:access|refresh|token)"\s*:\s*")([A-Za-z0-9\-_\.]{20,})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            "${matchResult.groupValues[1]}masked${matchResult.groupValues[3]}"
        }
        
        // Маскируем пароли в JSON формате ("password":"value" -> "password":"masked")
        masked = masked.replace(Regex("""("(?:password|passwd|pwd)"\s*:\s*")([^"]+)(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            "${matchResult.groupValues[1]}masked${matchResult.groupValues[3]}"
        }
        
        // Маскируем device_id в JSON формате ("device_id":"value" -> "device_id":"masked")
        // Важно: сохраняем корректный JSON формат с двойными кавычками
        masked = masked.replace(Regex("""("device_id"\s*:\s*")([A-Za-z0-9]{8,})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            val id = matchResult.groupValues[2]
            val prefix = matchResult.groupValues[1]
            val suffix = matchResult.groupValues[3]
            if (id.length > 8) {
                "$prefix${id.take(4)}***${id.takeLast(4)}$suffix"
            } else {
                "$prefix***$suffix"
            }
        }
        
        // Маскируем device_id в других форматах (не JSON: device_id=value, device_id: value)
        // Только если это НЕ JSON формат (нет двойных кавычек вокруг значения)
        masked = masked.replace(Regex("""device[_\s]?id["\s:=]+([A-Za-z0-9]{8,})(?!")(?=\s|$|,|})""", RegexOption.IGNORE_CASE)) { matchResult ->
            val id = matchResult.groupValues[1]
            if (id.length > 8) {
                "device_id=\"${id.take(4)}***${id.takeLast(4)}\""
            } else {
                "device_id=\"***\""
            }
        }
        
        // Маскируем номера телефонов в JSON формате ("phone":"+79991234567" -> "phone":"***4567")
        masked = masked.replace(Regex("""("(?:phone|number)"\s*:\s*")(\+?[0-9\s\-\(\)]{7,})([0-9]{4})(")""", RegexOption.IGNORE_CASE)) { matchResult ->
            val last4 = matchResult.groupValues[3]
            "${matchResult.groupValues[1]}***$last4${matchResult.groupValues[4]}"
        }
        
        // Маскируем полные URL с query параметрами (оставляем только путь)
        masked = masked.replace(Regex("""https?://[^\s"']+(\?[^\s"']+)"""), "***")
        
        return masked
    }
}
