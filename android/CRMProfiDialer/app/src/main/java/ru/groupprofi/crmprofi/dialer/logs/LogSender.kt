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
     * Отправить лог-бандл в CRM.
     * При сетевых ошибках добавляет в оффлайн-очередь.
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
                
                val bodyJson = JSONObject().apply {
                    put("device_id", deviceId)
                    put("ts", isoTime)
                    put("level_summary", bundle.levelSummary)
                    put("source", bundle.source)
                    put("payload", bundle.payload)
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
