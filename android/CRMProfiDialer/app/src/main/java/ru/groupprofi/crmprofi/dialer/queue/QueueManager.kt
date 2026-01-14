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
    
    // Защита от спама алертов: отправляем не чаще чем раз в 5 минут
    private val prefs = context.getSharedPreferences("queue_manager", Context.MODE_PRIVATE)
    private val KEY_LAST_STUCK_ALERT_TIME = "last_stuck_alert_time"
    private val STUCK_ALERT_INTERVAL_MS = 5 * 60 * 1000L  // 5 минут
    
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
        
        val stuckItems = mutableListOf<QueueItem>()
        
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
                    // Проверяем, достиг ли элемент max retries (3) после incrementRetry
                    val updatedItem = dao.getById(item.id)
                    if (updatedItem != null && updatedItem.retryCount >= 3) {
                        stuckItems.add(updatedItem)
                        Log.w("QueueManager", "Item reached max retries: type=${item.type}, id=${item.id}, retryCount=${updatedItem.retryCount}")
                    }
                }
            } catch (e: Exception) {
                Log.w("QueueManager", "Error sending queued item ${item.id}: ${e.message}")
                dao.incrementRetry(item.id)
                failedCount++
                // Проверяем, достиг ли элемент max retries
                val updatedItem = dao.getById(item.id)
                if (updatedItem != null && updatedItem.retryCount >= 3) {
                    stuckItems.add(updatedItem)
                    Log.w("QueueManager", "Item reached max retries after exception: type=${item.type}, id=${item.id}, retryCount=${updatedItem.retryCount}")
                }
            }
        }
        
        // Отправляем алерт в CRM для элементов, достигших max retries
        if (stuckItems.isNotEmpty()) {
            try {
                sendQueueStuckAlert(baseUrl, accessToken, stuckItems, httpClient)
            } catch (e: Exception) {
                Log.e("QueueManager", "Failed to send queue stuck alert: ${e.message}")
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
    
    /**
     * Получить метрики застрявших элементов (достигших max retries).
     * Возвращает null если нет застрявших элементов.
     */
    suspend fun getStuckMetrics(): StuckMetrics? {
        val now = System.currentTimeMillis()
        // Получаем все элементы с retryCount >= 3
        val allItems = dao.getAll()
        val stuckItems = allItems.filter { it.retryCount >= 3 }
        
        if (stuckItems.isEmpty()) {
            return null
        }
        
        val oldestStuckAgeSec = ((now - stuckItems.minOfOrNull { it.createdAt } ?: now) / 1000).toInt()
        val stuckByType = stuckItems.groupBy { it.type }.mapValues { it.value.size }
        
        return StuckMetrics(
            stuckCount = stuckItems.size,
            oldestStuckAgeSec = oldestStuckAgeSec,
            stuckByType = stuckByType
        )
    }
    
    data class StuckMetrics(
        val stuckCount: Int,
        val oldestStuckAgeSec: Int,
        val stuckByType: Map<String, Int>
    )
    
    data class QueueStats(
        val total: Int,
        val callUpdate: Int,
        val heartbeat: Int,
        val telemetry: Int,
        val logBundle: Int
    )
    
    /**
     * Отправить алерт в CRM о застрявших элементах очереди (достигших max retries).
     */
    private suspend fun sendQueueStuckAlert(
        baseUrl: String,
        accessToken: String,
        stuckItems: List<QueueItem>,
        httpClient: OkHttpClient
    ) {
        try {
            val url = "$baseUrl/api/phone/devices/heartbeat/"
            val jsonMedia = "application/json; charset=utf-8".toMediaType()
            
            // Формируем список застрявших элементов для алерта
            val stuckInfo = stuckItems.map { item ->
                JSONObject().apply {
                    put("type", item.type)
                    put("endpoint", item.endpoint)
                    put("retryCount", item.retryCount)
                    put("createdAt", item.createdAt)
                }
            }
            
            val bodyJson = JSONObject().apply {
                put("queue_stuck", true)
                put("stuck_items", org.json.JSONArray(stuckInfo))
                put("stuck_count", stuckItems.size)
            }.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $accessToken")
                .build()
            
            val response = httpClient.newCall(req).execute()
            if (response.isSuccessful) {
                Log.i("QueueManager", "Queue stuck alert sent: ${stuckItems.size} items")
            } else {
                Log.w("QueueManager", "Queue stuck alert failed: HTTP ${response.code}")
            }
            response.close()
        } catch (e: Exception) {
            Log.e("QueueManager", "Error sending queue stuck alert: ${e.message}")
        }
    }
}
