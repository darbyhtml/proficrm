package ru.groupprofi.crmprofi.dialer.data

import android.content.ContentResolver
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.CallLog
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.CallStatusApi
import java.text.SimpleDateFormat
import java.util.*

/**
 * Менеджер для отслеживания изменений в CallLog через ContentObserver.
 * Пытается найти совпадения с активными ожидаемыми звонками.
 */
class CallLogObserverManager(
    private val contentResolver: ContentResolver,
    private val pendingCallStore: PendingCallStore,
    private val callHistoryStore: CallHistoryStore,
    private val scope: CoroutineScope
) {
    private var observer: CallLogObserver? = null
    private val handler = Handler(Looper.getMainLooper())
    
    /**
     * ContentObserver для отслеживания изменений CallLog.
     */
    private inner class CallLogObserver(handler: Handler) : ContentObserver(handler) {
        override fun onChange(selfChange: Boolean, uri: Uri?) {
            super.onChange(selfChange, uri)
            // При изменении CallLog пытаемся найти совпадения
            scope.launch(Dispatchers.IO) {
                try {
                    checkForMatches()
                } catch (e: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка при проверке CallLog: ${e.message}", e)
                }
            }
        }
    }
    
    /**
     * Зарегистрировать наблюдатель.
     */
    fun register() {
        if (observer == null) {
            observer = CallLogObserver(handler)
            contentResolver.registerContentObserver(
                CallLog.Calls.CONTENT_URI,
                true,
                observer!!
            )
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallLogObserverManager", "ContentObserver зарегистрирован")
        }
    }
    
    /**
     * Отменить регистрацию наблюдателя.
     */
    fun unregister() {
        observer?.let {
            contentResolver.unregisterContentObserver(it)
            observer = null
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallLogObserverManager", "ContentObserver отменён")
        }
    }
    
    /**
     * Проверить CallLog на совпадения с активными ожидаемыми звонками.
     */
    suspend fun checkForMatches() {
        val activeCalls = pendingCallStore.getActivePendingCalls()
        if (activeCalls.isEmpty()) {
            return
        }
        
        for (pendingCall in activeCalls) {
            try {
                val callInfo = readCallLogForPhone(pendingCall.phoneNumber, pendingCall.startedAtMillis)
                if (callInfo != null) {
                    // Найдено совпадение - обрабатываем результат
                    handleCallResult(pendingCall, callInfo)
                }
            } catch (e: SecurityException) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "Нет разрешения на чтение CallLog: ${e.message}")
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка чтения CallLog: ${e.message}", e)
            }
        }
    }
    
    /**
     * Прочитать CallLog для конкретного номера в временном окне.
     */
    private fun readCallLogForPhone(
        phoneNumber: String,
        startedAtMillis: Long
    ): CallInfo? {
        val normalized = PendingCall.normalizePhone(phoneNumber)
        
        // Временное окно: ±5 минут от времени начала ожидания
        val windowStart = startedAtMillis - (5 * 60 * 1000)
        val windowEnd = startedAtMillis + (5 * 60 * 1000)
        
        try {
            val cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                arrayOf(
                    CallLog.Calls.NUMBER,
                    CallLog.Calls.TYPE,
                    CallLog.Calls.DURATION,
                    CallLog.Calls.DATE
                ),
                "${CallLog.Calls.DATE} >= ? AND ${CallLog.Calls.DATE} <= ?",
                arrayOf(windowStart.toString(), windowEnd.toString()),
                "${CallLog.Calls.DATE} DESC LIMIT 10"
            )
            
            cursor?.use {
                while (it.moveToNext()) {
                    val number = it.getString(it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)) ?: ""
                    val normalizedNumber = PendingCall.normalizePhone(number)
                    
                    // Проверяем совпадение номера (последние 7-10 цифр)
                    if (normalizedNumber.endsWith(normalized.takeLast(7)) || 
                        normalized.endsWith(normalizedNumber.takeLast(7))) {
                        val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                        val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                        val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                        
                        return CallInfo(type, duration, date)
                    }
                }
            }
        } catch (e: SecurityException) {
            throw e
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка чтения CallLog: ${e.message}", e)
        }
        
        return null
    }
    
    /**
     * Обработать найденный результат звонка.
     * ЭТАП 2: Добавлена сборка расширенных данных (direction, resolveMethod, endedAt, actionSource).
     */
    private suspend fun handleCallResult(
        pendingCall: PendingCall,
        callInfo: CallInfo
    ) {
        // Определяем человеческий статус
        val (status, statusText) = determineCallStatus(callInfo.type, callInfo.duration)
        
        // ЭТАП 2: Извлекаем дополнительные данные
        val direction = CallDirection.fromCallLogType(callInfo.type)
        val resolveMethod = ResolveMethod.OBSERVER // Результат найден через ContentObserver
        val endedAt = if (callInfo.duration > 0) {
            callInfo.date + (callInfo.duration * 1000) // endedAt = startedAt + duration (в миллисекундах)
        } else {
            null
        }
        
        // Обновляем состояние на RESOLVED
        pendingCallStore.updateCallState(pendingCall.callRequestId, PendingCall.PendingState.RESOLVED)
        
        // Сохраняем в историю с расширенными данными
        val historyItem = CallHistoryItem(
            id = pendingCall.callRequestId,
            phone = pendingCall.phoneNumber,
            phoneDisplayName = null, // Можно добавить получение имени из контактов позже
            status = status,
            // statusText теперь вычисляется через getStatusText()
            durationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
            startedAt = callInfo.date,
            sentToCrm = false, // Будет обновлено после успешной отправки
            sentToCrmAt = null,
            // Новые поля (ЭТАП 2)
            direction = direction,
            resolveMethod = resolveMethod,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = endedAt
        )
        
        callHistoryStore.addOrUpdate(historyItem)
        
        // Удаляем из ожидаемых
        pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallLogObserverManager", 
            "Результат звонка определён: ${maskPhone(pendingCall.phoneNumber)} -> $statusText (direction=$direction, resolveMethod=$resolveMethod)"
        )
    }
    
    /**
     * Определить человеческий статус звонка.
     */
    private fun determineCallStatus(type: Int, duration: Long): Pair<CallHistoryItem.CallStatus, String> {
        return when (type) {
            CallLog.Calls.OUTGOING_TYPE -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
            CallLog.Calls.MISSED_TYPE -> {
                Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
            }
            CallLog.Calls.INCOMING_TYPE -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
            5 -> { // REJECTED_TYPE (API 29+)
                Pair(CallHistoryItem.CallStatus.REJECTED, "Сброс")
            }
            else -> {
                if (duration > 0) {
                    Pair(CallHistoryItem.CallStatus.CONNECTED, "Разговор состоялся")
                } else {
                    Pair(CallHistoryItem.CallStatus.NO_ANSWER, "Не ответили")
                }
            }
        }
    }
    
    /**
     * Маскировать номер телефона для логов.
     */
    private fun maskPhone(phone: String): String {
        if (phone.length <= 4) return "***"
        return "${phone.take(3)}***${phone.takeLast(4)}"
    }
    
    /**
     * Информация о звонке из CallLog.
     */
    private data class CallInfo(
        val type: Int,      // Тип звонка (OUTGOING, MISSED, INCOMING, etc.)
        val duration: Long, // Длительность в секундах
        val date: Long      // Timestamp звонка
    )
}
