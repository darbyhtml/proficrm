package ru.groupprofi.crmprofi.dialer.logs

import android.content.Context
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import java.text.SimpleDateFormat
import java.util.*

/**
 * Отправка лог-бандлов в CRM.
 * Использует оффлайн-очередь при сетевых ошибках.
 */
class LogSender(
    private val context: Context,
    private val httpClient: OkHttpClient,
    private val queueManager: QueueManager
) {
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val scope = CoroutineScope(Dispatchers.IO)
    private val dateFormat = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
        timeZone = TimeZone.getTimeZone("UTC")
    }
    
    /**
     * Маскирует чувствительные данные в логах (токены, пароли, полные URL с query, номера телефонов).
     */
    private fun maskSensitiveData(text: String): String {
        var masked = text
        // Маскируем токены (Bearer token, access token, refresh token)
        masked = masked.replace(Regex("""Bearer\s+[A-Za-z0-9\-_\.]+"""), "Bearer ***")
        masked = masked.replace(Regex("""(access|refresh|token)["\s:=]+([A-Za-z0-9\-_\.]{20,})"""), "$1=\"***\"")
        // Маскируем пароли
        masked = masked.replace(Regex("""(password|passwd|pwd)["\s:=]+([^\s"']+)""", RegexOption.IGNORE_CASE), "$1=\"***\"")
        // Маскируем полные URL с query параметрами (оставляем только путь)
        masked = masked.replace(Regex("""https?://[^\s"']+(\?[^\s"']+)"""), "***")
        // Маскируем номера телефонов (оставляем последние 4 цифры)
        masked = masked.replace(Regex("""(\+?[0-9]{1,3}[\s\-]?)?([0-9]{3,4}[\s\-]?[0-9]{2,3}[\s\-]?)([0-9]{4})""")) {
            val last4 = it.groupValues[3]
            "***$last4"
        }
        // Маскируем device_id в логах (оставляем первые 4 и последние 4 символа)
        masked = masked.replace(Regex("""device[_\s]?id["\s:=]+([A-Za-z0-9]{8,})""", RegexOption.IGNORE_CASE)) {
            val id = it.groupValues[1]
            if (id.length > 8) {
                "device_id=\"${id.take(4)}***${id.takeLast(4)}\""
            } else {
                "device_id=\"***\""
            }
        }
        return masked
    }
    
    /**
     * Отправить лог-бандл в CRM.
     * При сетевых ошибках добавляет в оффлайн-очередь.
     * ВАЖНО: маскирует чувствительные данные перед отправкой.
     */
    fun sendLogBundle(
        baseUrl: String,
        accessToken: String,
        deviceId: String,
        bundle: LogCollector.LogBundle
    ) {
        scope.launch {
            try {
                val url = "$baseUrl/api/phone/logs/"
                val now = Date()
                val isoTime = dateFormat.format(now)
                
                // Маскируем чувствительные данные в payload перед отправкой
                val maskedPayload = maskSensitiveData(bundle.payload)
                
                val bodyJson = JSONObject().apply {
                    put("device_id", deviceId)
                    put("ts", isoTime)
                    put("level_summary", bundle.levelSummary)
                    put("source", bundle.source)
                    put("payload", maskedPayload)
                }.toString()
                
                val req = Request.Builder()
                    .url(url)
                    .post(bodyJson.toRequestBody(jsonMedia))
                    .addHeader("Authorization", "Bearer $accessToken")
                    .build()
                
                try {
                    httpClient.newCall(req).execute().use { res ->
                        if (res.isSuccessful) {
                            Log.i("LogSender", "Log bundle sent successfully: ${bundle.entryCount} entries")
                        } else {
                            Log.w("LogSender", "Log bundle failed: HTTP ${res.code}")
                            // При ошибке сервера - добавляем в очередь
                            if (res.code in 500..599) {
                                queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
                            }
                        }
                    }
                } catch (e: java.net.UnknownHostException) {
                    // Нет интернета - добавляем в очередь
                    Log.w("LogSender", "No internet, queuing log bundle")
                    queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
                } catch (e: java.net.SocketTimeoutException) {
                    // Таймаут - добавляем в очередь
                    Log.w("LogSender", "Timeout, queuing log bundle")
                    queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
                } catch (e: java.io.IOException) {
                    // Другие сетевые ошибки - добавляем в очередь
                    Log.w("LogSender", "Network error, queuing log bundle: ${e.message}")
                    queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
                }
            } catch (e: Exception) {
                Log.e("LogSender", "Error preparing log bundle: ${e.message}")
            }
        }
    }
}
