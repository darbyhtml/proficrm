package ru.groupprofi.crmprofi.dialer.queue

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
import java.io.IOException

/**
 * Менеджер оффлайн-очереди: добавление элементов и периодическая отправка.
 */
class QueueManager(private val context: Context) {
    private val db = AppDatabase.getDatabase(context)
    private val dao = db.queueDao()
    private val scope = CoroutineScope(Dispatchers.IO)
    
    /**
     * Добавить элемент в очередь (синхронно, для использования из сервиса).
     */
    fun enqueue(
        type: String,
        endpoint: String,
        payload: String,
        method: String = "POST"
    ) {
        scope.launch {
            try {
                val item = QueueItem(
                    type = type,
                    endpoint = endpoint,
                    payload = payload,
                    method = method
                )
                dao.insert(item)
                Log.i("QueueManager", "Enqueued: type=$type, endpoint=$endpoint")
            } catch (e: Exception) {
                Log.e("QueueManager", "Failed to enqueue: ${e.message}")
            }
        }
    }
    
    /**
     * Попытаться отправить накопленные элементы из очереди.
     * Вызывается периодически из сервиса или при восстановлении сети.
     */
    suspend fun flushQueue(
        baseUrl: String,
        accessToken: String,
        httpClient: OkHttpClient
    ): Int {
        val pending = dao.getPending(limit = 50)
        if (pending.isEmpty()) return 0
        
        var sentCount = 0
        var failedCount = 0
        
        for (item in pending) {
            try {
                val success = sendItem(baseUrl, accessToken, item, httpClient)
                if (success) {
                    dao.delete(item.id)
                    sentCount++
                    Log.i("QueueManager", "Sent queued item: type=${item.type}, id=${item.id}")
                } else {
                    dao.incrementRetry(item.id)
                    failedCount++
                }
            } catch (e: Exception) {
                Log.w("QueueManager", "Error sending queued item ${item.id}: ${e.message}")
                dao.incrementRetry(item.id)
                failedCount++
            }
        }
        
        // Очистка старых неудачных элементов (старше 7 дней)
        val cutoffTime = System.currentTimeMillis() - (7 * 24 * 60 * 60 * 1000L)
        dao.deleteOldFailed(cutoffTime)
        
        Log.i("QueueManager", "Flush complete: sent=$sentCount, failed=$failedCount, total=${pending.size}")
        return sentCount
    }
    
    /**
     * Отправить один элемент очереди.
     */
    private suspend fun sendItem(
        baseUrl: String,
        accessToken: String,
        item: QueueItem,
        httpClient: OkHttpClient
    ): Boolean {
        return try {
            val url = "$baseUrl${item.endpoint}"
            val jsonMedia = "application/json; charset=utf-8".toMediaType()
            val body = item.payload.toRequestBody(jsonMedia)
            
            val request = Request.Builder()
                .url(url)
                .addHeader("Authorization", "Bearer $accessToken")
                .method(item.method, body)
                .build()
            
            val response = httpClient.newCall(request).execute()
            val success = response.isSuccessful
            
            if (!success) {
                Log.w("QueueManager", "Queue item failed: HTTP ${response.code}, endpoint=${item.endpoint}")
            }
            
            response.close()
            success
        } catch (e: IOException) {
            // Сетевая ошибка - не критично, попробуем позже
            Log.w("QueueManager", "Network error sending queue item: ${e.message}")
            false
        } catch (e: Exception) {
            Log.e("QueueManager", "Unexpected error sending queue item: ${e.message}")
            false
        }
    }
    
    /**
     * Получить статистику очереди (для отладки/мониторинга).
     */
    suspend fun getStats(): QueueStats {
        val total = dao.count()
        val callUpdate = dao.countByType("call_update")
        val heartbeat = dao.countByType("heartbeat")
        val telemetry = dao.countByType("telemetry")
        val logBundle = dao.countByType("log_bundle")
        
        return QueueStats(
            total = total,
            callUpdate = callUpdate,
            heartbeat = heartbeat,
            telemetry = telemetry,
            logBundle = logBundle
        )
    }
    
    data class QueueStats(
        val total: Int,
        val callUpdate: Int,
        val heartbeat: Int,
        val telemetry: Int,
        val logBundle: Int
    )
}
