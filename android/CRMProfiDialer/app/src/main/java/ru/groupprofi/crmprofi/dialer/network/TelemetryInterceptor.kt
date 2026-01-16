package ru.groupprofi.crmprofi.dialer.network

import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.Interceptor
import okhttp3.Response
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import java.io.IOException

/**
 * Interceptor для сбора телеметрии (latency, HTTP коды).
 * Автоматически отправляет метрики в очередь для последующей отправки в CRM.
 */
class TelemetryInterceptor(
    private val tokenManager: TokenManager,
    private val queueManager: kotlin.Lazy<QueueManager>,
    private val context: android.content.Context
) : Interceptor {
    
    private val scope = CoroutineScope(Dispatchers.IO)
    
    override fun intercept(chain: Interceptor.Chain): Response {
        val request = chain.request()
        val endpoint = request.url.encodedPath
        
        // Собираем телеметрию только для /api/phone/* endpoints
        val shouldCollect = endpoint.startsWith("/api/phone/")
        if (!shouldCollect) {
            return chain.proceed(chain.request())
        }
        
        val startTime = System.currentTimeMillis()
        var httpCode: Int? = null
        var error: IOException? = null
        
        try {
            val response = chain.proceed(request)
            httpCode = response.code
            return response
        } catch (e: IOException) {
            error = e
            throw e
        } finally {
            val duration = System.currentTimeMillis() - startTime
            val deviceId = tokenManager.getDeviceId() ?: ""
            
            // Отправляем телеметрию асинхронно (не блокируем основной поток)
            scope.launch {
                try {
                    // Формируем JSON для телеметрии
                    val telemetryJson = org.json.JSONObject().apply {
                        put("type", "latency")
                        put("endpoint", endpoint)
                        if (httpCode != null) put("http_code", httpCode)
                        put("value_ms", duration.toInt())
                        if (error != null) {
                            put("extra", org.json.JSONObject().apply {
                                put("error", error.message ?: "unknown")
                            })
                        }
                    }
                    val batchJson = org.json.JSONObject().apply {
                        put("device_id", deviceId)
                        put("items", org.json.JSONArray().put(telemetryJson))
                    }
                    
                    // Добавляем в очередь для последующей отправки (ленивая инициализация)
                    queueManager.value.enqueue("telemetry", "/api/phone/telemetry/", batchJson.toString())
                } catch (e: Exception) {
                    // Игнорируем ошибки телеметрии (не критично)
                    Log.d("TelemetryInterceptor", "Telemetry collection error: ${e.message}")
                }
            }
        }
    }
}
