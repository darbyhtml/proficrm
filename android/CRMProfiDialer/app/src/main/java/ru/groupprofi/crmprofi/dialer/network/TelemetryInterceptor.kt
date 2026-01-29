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
 * Использует батчинг для снижения нагрузки на сервер.
 */
class TelemetryInterceptor(
    private val tokenManager: TokenManager,
    private val queueManager: kotlin.Lazy<QueueManager>,
    private val context: android.content.Context,
    private val telemetryBatcher: TelemetryBatcher
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
            
            // Добавляем телеметрию в батчер (асинхронно, не блокируем основной поток)
            scope.launch {
                try {
                    val telemetryItem = ApiClient.TelemetryItem(
                        type = "latency",
                        endpoint = endpoint,
                        httpCode = httpCode,
                        valueMs = duration.toInt(),
                        extra = error?.let { mapOf("error" to (it.message ?: "unknown")) }
                    )
                    
                    telemetryBatcher.addTelemetry(telemetryItem)
                } catch (e: Exception) {
                    // Игнорируем ошибки телеметрии (не критично)
                    Log.d("TelemetryInterceptor", "Telemetry collection error: ${e.message}")
                }
            }
        }
    }
}
