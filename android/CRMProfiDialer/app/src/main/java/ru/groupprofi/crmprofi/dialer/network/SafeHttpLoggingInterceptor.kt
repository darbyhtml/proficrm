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
     */
    private fun maskSensitiveData(text: String): String {
        var masked = text
        
        // Маскируем Bearer токены
        masked = masked.replace(Regex("""Bearer\s+[A-Za-z0-9\-_\.]+"""), "Bearer ***")
        
        // Маскируем access/refresh токены в JSON
        masked = masked.replace(Regex("""(access|refresh|token)["\s:=]+([A-Za-z0-9\-_\.]{20,})"""), "$1=\"***\"")
        
        // Маскируем пароли
        masked = masked.replace(Regex("""(password|passwd|pwd)["\s:=]+([^\s"']+)""", RegexOption.IGNORE_CASE), "$1=\"***\"")
        
        // Маскируем device_id в JSON формате ("device_id":"value" -> "device_id":"masked")
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
        
        // Маскируем device_id в других форматах (device_id=value, device_id: value)
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
        
        // Маскируем полные URL с query параметрами (оставляем только путь)
        masked = masked.replace(Regex("""https?://[^\s"']+(\?[^\s"']+)"""), "***")
        
        return masked
    }
}
