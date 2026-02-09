package ru.groupprofi.crmprofi.dialer.data

import android.content.ContentResolver
import android.database.ContentObserver
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.provider.CallLog
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.CallStatusApi
import ru.groupprofi.crmprofi.dialer.config.AppFeatures
import ru.groupprofi.crmprofi.dialer.data.CallLogCorrelator
import android.content.Context
import androidx.core.content.ContextCompat
import java.text.SimpleDateFormat
import java.util.*
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsMetricsBuffer

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
     * Улучшено: проверка разрешений перед регистрацией, graceful degradation при отзыве разрешений.
     */
    fun register(context: Context? = null) {
        // Проверяем разрешения перед регистрацией
        val ctx = context ?: try {
            // Пытаемся получить контекст из Application через AppContainer
            ru.groupprofi.crmprofi.dialer.core.AppContainer.getContext()
        } catch (e: Exception) {
            null
        }
        
        val hasPermission = ctx?.let {
            try {
                android.content.pm.PackageManager.PERMISSION_GRANTED == 
                    ContextCompat.checkSelfPermission(it, android.Manifest.permission.READ_CALL_LOG)
            } catch (e: Exception) {
                false
            }
        } ?: false
        
        if (!hasPermission) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "READ_CALL_LOG permission missing, observer not registered")
            return
        }
        
        if (observer == null) {
            try {
                observer = CallLogObserver(handler)
                contentResolver.registerContentObserver(
                    CallLog.Calls.CONTENT_URI,
                    true,
                    observer!!
                )
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("CallLogObserverManager", "ContentObserver зарегистрирован")
            } catch (e: SecurityException) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "SecurityException при регистрации observer: ${e.message}")
                observer = null
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка регистрации observer: ${e.message}", e)
                observer = null
            }
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
     * Улучшено: runtime permission check, graceful degradation при отзыве разрешений.
     */
    suspend fun checkForMatches() {
        val activeCalls = pendingCallStore.getActivePendingCalls()
        if (activeCalls.isEmpty()) {
            return
        }
        
        // Проверяем разрешения перед чтением CallLog
        val ctx = try {
            ru.groupprofi.crmprofi.dialer.core.AppContainer.getContext()
        } catch (e: Exception) {
            null
        }
        
        val hasPermission = ctx?.let {
            try {
                android.content.pm.PackageManager.PERMISSION_GRANTED == 
                    ContextCompat.checkSelfPermission(it, android.Manifest.permission.READ_CALL_LOG)
            } catch (e: Exception) {
                false
            }
        } ?: false
        
        if (!hasPermission) {
            DiagnosticsMetricsBuffer.addEvent(
                DiagnosticsMetricsBuffer.EventType.PERMISSION_CHANGED,
                "Нет доступа к CallLog",
                mapOf("missing" to "READ_CALL_LOG"),
                throttleKey = "READ_CALL_LOG"
            )
            // Разрешения нет — это нормальное состояние: отправляем unknown с причиной.
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "READ_CALL_LOG missing: will mark unknown for active calls")
            activeCalls.forEach { pendingCall ->
                try {
                    handleUnknown(pendingCall, resolveReason = "permission_missing", reasonIfUnknown = "READ_CALL_LOG not granted")
                } catch (ex: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка обработки permission_missing: ${ex.message}", ex)
                }
            }
            // Отменяем регистрацию observer, если разрешение отозвано
            unregister()
            return
        }
        
        try {
            for (pendingCall in activeCalls) {
                val callInfo = readCallLogForPhone(pendingCall.phoneNumber, pendingCall.startedAtMillis)
                if (callInfo != null) {
                    // Найдено совпадение - обрабатываем результат
                    handleCallResult(pendingCall, callInfo)
                }
            }
        } catch (e: SecurityException) {
            DiagnosticsMetricsBuffer.addEvent(
                DiagnosticsMetricsBuffer.EventType.PERMISSION_CHANGED,
                "Разрешение отозвано во время работы",
                mapOf("missing" to "READ_CALL_LOG", "reason" to "revoked"),
                throttleKey = "READ_CALL_LOG_revoked"
            )
            // Разрешения отозвано во время работы - graceful degradation
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "READ_CALL_LOG revoked during operation: will mark unknown for active calls")
            activeCalls.forEach { pendingCall ->
                try {
                    handleUnknown(pendingCall, resolveReason = "permission_revoked", reasonIfUnknown = "READ_CALL_LOG revoked during operation")
                } catch (ex: Exception) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка обработки permission_revoked: ${ex.message}", ex)
                }
            }
            // Отменяем регистрацию observer
            unregister()
        } catch (e: Exception) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("CallLogObserverManager", "Ошибка чтения CallLog: ${e.message}", e)
        }
    }
    
    /**
     * Прочитать CallLog для конкретного номера в временном окне.
     * Улучшено: использует CallLogCorrelator для корректной корреляции с защитой от дублей.
     */
    private fun readCallLogForPhone(
        phoneNumber: String,
        startedAtMillis: Long
    ): CallLogCorrelator.CallInfo? {
        val normalized = PhoneNumberNormalizer.normalize(phoneNumber)
        
        // Временное окно: от 2 минут до начала ожидания до 20 секунд после (расширено для задержек CallLog)
        val windowStart = startedAtMillis - (2 * 60 * 1000) // 2 минуты до открытия звонилки
        val windowEnd = startedAtMillis + (20 * 1000) // 20 секунд после (для задержек CallLog)
        
        try {
            // Пытаемся получить subscriptionId и phoneAccountId для dual SIM (если доступно)
            val columns = mutableListOf(
                CallLog.Calls.NUMBER,
                CallLog.Calls.TYPE,
                CallLog.Calls.DURATION,
                CallLog.Calls.DATE
            )
            
            // Добавляем поля для dual SIM (если доступно)
            if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.LOLLIPOP_MR1) {
                try {
                    columns.add("subscription_id")
                    columns.add("phone_account_id")
                } catch (e: Exception) {
                    // Игнорируем, если поля недоступны
                }
            }
            
            val cursor = contentResolver.query(
                CallLog.Calls.CONTENT_URI,
                columns.toTypedArray(),
                "${CallLog.Calls.DATE} >= ? AND ${CallLog.Calls.DATE} <= ?",
                arrayOf(windowStart.toString(), windowEnd.toString()),
                "${CallLog.Calls.DATE} DESC LIMIT 50"
            )
            
            cursor?.use {
                var checkedCount = 0
                var bestMatch: CallLogCorrelator.CorrelationResult? = null
                
                while (it.moveToNext()) {
                    checkedCount++
                    val number = it.getString(it.getColumnIndexOrThrow(CallLog.Calls.NUMBER)) ?: ""
                    val normalizedNumber = PhoneNumberNormalizer.normalize(number)
                    val type = it.getInt(it.getColumnIndexOrThrow(CallLog.Calls.TYPE))
                    val duration = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DURATION))
                    val date = it.getLong(it.getColumnIndexOrThrow(CallLog.Calls.DATE))
                    
                    // Пытаемся получить subscriptionId и phoneAccountId (если доступно)
                    var subscriptionId: Int? = null
                    var phoneAccountId: String? = null
                    try {
                        val subIdIndex = it.getColumnIndex("subscription_id")
                        if (subIdIndex >= 0) {
                            subscriptionId = it.getInt(subIdIndex).takeIf { it >= 0 }
                        }
                        val phoneAccIndex = it.getColumnIndex("phone_account_id")
                        if (phoneAccIndex >= 0) {
                            phoneAccountId = it.getString(phoneAccIndex)
                        }
                    } catch (e: Exception) {
                        // Игнорируем ошибки получения dual SIM полей
                    }
                    
                    // Используем CallLogCorrelator для корреляции
                    val correlation = CallLogCorrelator.correlate(
                        callLogNumber = normalizedNumber,
                        callLogDate = date,
                        callLogType = type,
                        callLogDuration = duration,
                        expectedNumber = normalized,
                        expectedStartTime = startedAtMillis,
                        windowStartMs = windowStart,
                        windowEndMs = windowEnd,
                        subscriptionId = subscriptionId,
                        phoneAccountId = phoneAccountId
                    )
                    
                    if (correlation.matched) {
                        // Выбираем лучшее совпадение (EXACT > HIGH > MEDIUM)
                        if (bestMatch == null || correlation.confidence.ordinal < bestMatch.confidence.ordinal) {
                            bestMatch = correlation
                        }
                        
                        if (checkedCount <= 3) {
                            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                                "CallLogObserverManager",
                                "Найдено совпадение #$checkedCount: confidence=${correlation.confidence}, reason=${correlation.reason}"
                            )
                        }
                    }
                }
                
                if (bestMatch != null && bestMatch.matched) {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                        "CallLogObserverManager",
                        "CallLog корреляция успешна: confidence=${bestMatch.confidence}, checked=$checkedCount entries"
                    )
                    return bestMatch.callInfo
                } else {
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                        "CallLogObserverManager",
                        "CallLog корреляция не найдена: checked=$checkedCount entries"
                    )
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
     * Улучшено: защита от дублей через idempotency key.
     */
    private suspend fun handleCallResult(
        pendingCall: PendingCall,
        callInfo: CallLogCorrelator.CallInfo
    ) {
        // Атомарно берём право на резолв для данного звонка.
        val canResolve = pendingCallStore.tryMarkResolving(pendingCall.callRequestId)
        if (!canResolve) {
            return
        }
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
        
        // Отправляем update в CRM (если режим FULL)
        // When TelemetryMode.FULL is enabled, events will be sent to CRM
        val crmStatus = CallStatusApi.fromCallHistoryStatus(status).apiValue
        val apiResult = AppContainer.apiClient.sendCallUpdate(
            callRequestId = pendingCall.callRequestId,
            callStatus = crmStatus,
            callStartedAt = callInfo.date,
            callDurationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
            direction = direction,
            resolveMethod = resolveMethod,
            resolveReason = null,
            reasonIfUnknown = null,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = endedAt
        )
        
        // Обновляем состояние на RESOLVED
        pendingCallStore.updateCallState(pendingCall.callRequestId, PendingCall.PendingState.RESOLVED)
        
        // Проверяем существующую запись по callRequestId (основной ключ)
        val existingCall = callHistoryStore.getCallById(pendingCall.callRequestId)
        if (existingCall != null && existingCall.status != CallHistoryItem.CallStatus.UNKNOWN) {
            // Уже есть запись с результатом - не создаем дубль, только обновляем если нужно
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                "CallLogObserverManager",
                "Запись уже существует для id=${pendingCall.callRequestId}, пропускаем дубль"
            )
            // Обновляем только если текущая запись лучше (например, была UNKNOWN, теперь CONNECTED)
            if (existingCall.status == CallHistoryItem.CallStatus.UNKNOWN && status != CallHistoryItem.CallStatus.UNKNOWN) {
                val updatedItem = existingCall.copy(
                    status = status,
                    durationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
                    startedAt = callInfo.date,
                    direction = direction,
                    resolveMethod = resolveMethod,
                    endedAt = endedAt
                )
                callHistoryStore.addOrUpdate(updatedItem)
            }
        } else {
            // Сохраняем в историю с расширенными данными
            val historyItem = CallHistoryItem(
                id = pendingCall.callRequestId,
                phone = pendingCall.phoneNumber,
                phoneDisplayName = null, // Можно добавить получение имени из контактов позже
                status = status,
                durationSeconds = callInfo.duration.toInt().takeIf { it > 0 },
                startedAt = callInfo.date,
                sentToCrm = apiResult is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success,
                sentToCrmAt = if (apiResult is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success) System.currentTimeMillis() else null,
                // Новые поля (ЭТАП 2)
                direction = direction,
                resolveMethod = resolveMethod,
                attemptsCount = pendingCall.attempts,
                actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
                endedAt = endedAt
            )
            
            callHistoryStore.addOrUpdate(historyItem)
        }
        
        // Удаляем из ожидаемых
        pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        // Форсированная отправка телеметрии после резолва звонка
        scope.launch {
            try {
                AppContainer.apiClient.flushTelemetry()
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "Ошибка flushTelemetry: ${e.message}")
            }
        }
        
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.CALL_RESOLVED,
            statusText,
            mapOf(
                "status" to status.name,
                "durationSec" to (callInfo.duration.toString()),
                "confidence" to "OBSERVER"
            )
        )
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallLogObserverManager", 
            "Результат звонка определён: ${maskPhone(pendingCall.phoneNumber)} -> $statusText (direction=$direction, resolveMethod=$resolveMethod, actionSource=${pendingCall.actionSource})"
        )
    }

    private suspend fun handleUnknown(
        pendingCall: PendingCall,
        resolveReason: String,
        reasonIfUnknown: String
    ) {
        // Атомарно берём право на резолв, чтобы избежать дублей UNKNOWN
        val canResolve = pendingCallStore.tryMarkResolving(pendingCall.callRequestId)
        if (!canResolve) {
            return
        }

        // Отправляем update в CRM (если режим FULL)
        // When TelemetryMode.FULL is enabled, events will be sent to CRM
        val apiResult = AppContainer.apiClient.sendCallUpdate(
            callRequestId = pendingCall.callRequestId,
            callStatus = CallStatusApi.UNKNOWN.apiValue,
            callStartedAt = pendingCall.startedAtMillis,
            callDurationSeconds = null,
            direction = null,
            resolveMethod = ResolveMethod.UNKNOWN,
            resolveReason = resolveReason,
            reasonIfUnknown = reasonIfUnknown,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )
        
        val historyItem = CallHistoryItem(
            id = pendingCall.callRequestId,
            phone = pendingCall.phoneNumber,
            phoneDisplayName = null,
            status = CallHistoryItem.CallStatus.UNKNOWN,
            durationSeconds = null,
            startedAt = pendingCall.startedAtMillis,
            sentToCrm = apiResult is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success,
            sentToCrmAt = if (apiResult is ru.groupprofi.crmprofi.dialer.network.ApiClient.Result.Success) System.currentTimeMillis() else null,
            direction = null,
            resolveMethod = ResolveMethod.UNKNOWN,
            attemptsCount = pendingCall.attempts,
            actionSource = pendingCall.actionSource ?: ActionSource.UNKNOWN,
            endedAt = null
        )
        
        callHistoryStore.addOrUpdate(historyItem)
        pendingCallStore.removePendingCall(pendingCall.callRequestId)
        
        // Форсированная отправка телеметрии после резолва звонка (даже если UNKNOWN)
        scope.launch {
            try {
                AppContainer.apiClient.flushTelemetry()
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallLogObserverManager", "Ошибка flushTelemetry: ${e.message}")
            }
        }
        
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.CALL_RESOLVED,
            "UNKNOWN",
            mapOf(
                "status" to "UNKNOWN",
                "resolveReason" to resolveReason
            )
        )
        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
            "CallLogObserverManager",
            "CALL_TIMEOUT/UNKNOWN id=${pendingCall.callRequestId} reason=$resolveReason actionSource=${pendingCall.actionSource}"
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
    
    // Используем CallInfo из CallLogCorrelator вместо локального data class
}
