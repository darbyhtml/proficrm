package ru.groupprofi.crmprofi.dialer.queue

import androidx.room.*
import kotlinx.coroutines.flow.Flow

/**
 * DAO для работы с оффлайн-очередью.
 */
@Dao
interface QueueDao {
    /**
     * Вставить новый элемент в очередь.
     */
    @Insert
    suspend fun insert(item: QueueItem): Long
    
    /**
     * Получить все элементы очереди, отсортированные по времени создания (старые первыми).
     */
    @Query("SELECT * FROM queue_items ORDER BY createdAt ASC")
    suspend fun getAll(): List<QueueItem>
    
    /**
     * Получить элементы для отправки (с ограничением по ретраям, максимум 3 попытки).
     */
    @Query("SELECT * FROM queue_items WHERE retryCount < 3 ORDER BY createdAt ASC LIMIT :limit")
    suspend fun getPending(limit: Int = 50): List<QueueItem>
    
    /**
     * Увеличить счетчик попыток и обновить время последней попытки.
     */
    @Query("UPDATE queue_items SET retryCount = retryCount + 1, lastRetryAt = :now WHERE id = :id")
    suspend fun incrementRetry(id: Long, now: Long = System.currentTimeMillis())
    
    /**
     * Удалить элемент из очереди (после успешной отправки).
     */
    @Query("DELETE FROM queue_items WHERE id = :id")
    suspend fun delete(id: Long)
    
    /**
     * Удалить старые элементы (старше 7 дней) с большим количеством попыток.
     */
    @Query("DELETE FROM queue_items WHERE createdAt < :cutoffTime AND retryCount >= 3")
    suspend fun deleteOldFailed(cutoffTime: Long)
    
    /**
     * Получить количество элементов в очереди (для мониторинга).
     */
    @Query("SELECT COUNT(*) FROM queue_items")
    suspend fun count(): Int
    
    /**
     * Получить количество элементов по типу (для статистики).
     */
    @Query("SELECT COUNT(*) FROM queue_items WHERE type = :type")
    suspend fun countByType(type: String): Int
    
    /**
     * Получить элемент по ID (для проверки retryCount после incrementRetry).
     */
    @Query("SELECT * FROM queue_items WHERE id = :id")
    suspend fun getById(id: Long): QueueItem?
}
