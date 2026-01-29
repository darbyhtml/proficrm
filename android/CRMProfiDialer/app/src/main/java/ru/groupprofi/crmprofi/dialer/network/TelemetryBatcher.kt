package ru.groupprofi.crmprofi.dialer.network

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.ConcurrentLinkedQueue

/**
 * Батчинг телеметрии для снижения нагрузки на сервер.
 * Накапливает метрики и отправляет их батчами раз в 45 секунд или при накоплении 20 элементов.
 */
class TelemetryBatcher(
    private val deviceId: String,
    private val sendBatchFn: suspend (String, List<ApiClient.TelemetryItem>) -> ru.groupprofi.crmprofi.dialer.network.ApiClient.Result<Unit>
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val telemetryQueue = ConcurrentLinkedQueue<ApiClient.TelemetryItem>()
    private val mutex = Mutex()
    private var flushJob: Job? = null
    
    companion object {
        private const val BATCH_SIZE_THRESHOLD = 20 // Отправлять при накоплении 20 элементов
        private const val BATCH_INTERVAL_MS = 45_000L // Отправлять раз в 45 секунд
        private const val MAX_BATCH_SIZE = 100 // Максимальный размер батча
    }
    
    /**
     * Причина отправки батча телеметрии.
     */
    private enum class FlushReason {
        SIZE,   // Достигнут порог размера батча (>=20 элементов)
        TIMER,  // Истек таймер (45 секунд)
        FORCED  // Принудительная отправка (бизнес-события: COMMAND_RECEIVED, RATE_LIMIT_ENTER, CALL_RESOLVED)
    }
    
    /**
     * Добавить элемент телеметрии в очередь для батчинга.
     */
    fun addTelemetry(item: ApiClient.TelemetryItem) {
        telemetryQueue.offer(item)
        
        // Запускаем flush при достижении порога размера
        if (telemetryQueue.size >= BATCH_SIZE_THRESHOLD) {
            // Отменяем запланированный таймерный flush, так как делаем немедленный SIZE flush
            flushJob?.cancel()
            scope.launch {
                flushBatch(FlushReason.SIZE)
            }
        } else if (flushJob == null || flushJob?.isCompleted == true) {
            // Запускаем периодический flush если его еще нет
            flushJob = scope.launch {
                delay(BATCH_INTERVAL_MS)
                flushBatch(FlushReason.TIMER)
            }
        }
    }
    
    /**
     * Принудительно отправить накопленную телеметрию (например, при получении команды или важном событии).
     */
    suspend fun flushNow() {
        flushBatch(FlushReason.FORCED)
    }
    
    /**
     * Отправить батч телеметрии.
     */
    private suspend fun flushBatch(reason: FlushReason) = mutex.withLock {
        if (telemetryQueue.isEmpty()) return@withLock
        
        val items = mutableListOf<ApiClient.TelemetryItem>()
        var count = 0
        
        // Извлекаем элементы из очереди (до MAX_BATCH_SIZE)
        while (count < MAX_BATCH_SIZE && telemetryQueue.isNotEmpty()) {
            telemetryQueue.poll()?.let { items.add(it) }
            count++
        }
        
        if (items.isEmpty()) return@withLock
        
        try {
            // Отправляем батч напрямую через ApiClient
            val sendResult: ru.groupprofi.crmprofi.dialer.network.ApiClient.Result<Unit> = sendBatchFn(deviceId, items)
            
            if (sendResult is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                    "TelemetryBatcher",
                    "TelemetryBatcher flush: nItems=${items.size}, reason=${reason.name}"
                )
            } else {
                val err = sendResult as ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Error
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w(
                    "TelemetryBatcher",
                    "TelemetryBatcher flush failed: nItems=${items.size}, reason=${reason.name}, error=${err.message}"
                )
                // Возвращаем элементы обратно в очередь при ошибке
                items.forEach { telemetryQueue.offer(it) }
            }
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("TelemetryBatcher", "Error batching telemetry: ${e.message}")
            // Возвращаем элементы обратно в очередь при ошибке
            items.forEach { telemetryQueue.offer(it) }
        }
        
        // Если в очереди еще есть элементы и это был принудительный flush, запускаем следующий таймерный flush
        if (reason == FlushReason.FORCED && telemetryQueue.isNotEmpty()) {
            // Отменяем существующий таймерный flush перед планированием нового
            flushJob?.cancel()
            flushJob = scope.launch {
                delay(BATCH_INTERVAL_MS)
                flushBatch(FlushReason.TIMER)
            }
        }
    }
    
    /**
     * Получить текущий размер очереди (для диагностики).
     */
    fun getQueueSize(): Int = telemetryQueue.size
}
