package ru.groupprofi.crmprofi.dialer.queue

import androidx.room.Entity
import androidx.room.PrimaryKey

/**
 * Элемент оффлайн-очереди для отправки данных в CRM.
 * Универсальная таблица для всех типов запросов: call update, heartbeat, telemetry, logs.
 */
@Entity(tableName = "queue_items")
data class QueueItem(
    @PrimaryKey(autoGenerate = true)
    val id: Long = 0,
    
    /**
     * Тип запроса: "call_update", "heartbeat", "telemetry", "log_bundle"
     */
    val type: String,
    
    /**
     * JSON-тело запроса (готовый для отправки)
     */
    val payload: String,
    
    /**
     * URL эндпоинта (например, "/api/phone/calls/update/")
     */
    val endpoint: String,
    
    /**
     * HTTP метод: "POST", "PUT", "PATCH"
     */
    val method: String = "POST",
    
    /**
     * Количество попыток отправки (для ограничения ретраев)
     */
    val retryCount: Int = 0,
    
    /**
     * Время создания (миллисекунды с эпохи)
     */
    val createdAt: Long = System.currentTimeMillis(),
    
    /**
     * Время последней попытки отправки
     */
    val lastRetryAt: Long? = null
)
