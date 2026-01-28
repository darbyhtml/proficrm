package ru.groupprofi.crmprofi.dialer.domain

/**
 * Контракт для синхронизации данных о звонках между Android и CRM Backend.
 * ЭТАП 1: Константы и enum для подготовки к отправке новых полей.
 * Реальная отправка новых полей начнётся в ЭТАП 2.
 */

/**
 * Статус звонка для API (маппинг в строки для отправки в CRM).
 */
enum class CallStatusApi(val apiValue: String) {
    CONNECTED("connected"),
    NO_ANSWER("no_answer"),
    REJECTED("rejected"),
    MISSED("missed"),
    BUSY("busy"),
    UNKNOWN("unknown");  // Новое: для случаев, когда результат не определён
    
    companion object {
        /**
         * Маппинг из CallHistoryItem.CallStatus в CallStatusApi.
         */
        fun fromCallHistoryStatus(status: CallHistoryItem.CallStatus): CallStatusApi {
            return when (status) {
                CallHistoryItem.CallStatus.CONNECTED -> CONNECTED
                CallHistoryItem.CallStatus.NO_ANSWER -> NO_ANSWER
                CallHistoryItem.CallStatus.REJECTED -> REJECTED
                CallHistoryItem.CallStatus.UNKNOWN -> UNKNOWN
            }
        }
    }
}

/**
 * Направление звонка.
 */
enum class CallDirection(val apiValue: String) {
    OUTGOING("outgoing"),    // Исходящий
    INCOMING("incoming"),    // Входящий
    MISSED("missed"),        // Пропущенный
    UNKNOWN("unknown");      // Неизвестно
    
    companion object {
        /**
         * Маппинг из CallLog.Calls.TYPE в CallDirection.
         */
        fun fromCallLogType(type: Int): CallDirection {
            return when (type) {
                android.provider.CallLog.Calls.OUTGOING_TYPE -> OUTGOING
                android.provider.CallLog.Calls.INCOMING_TYPE -> INCOMING
                android.provider.CallLog.Calls.MISSED_TYPE -> MISSED
                else -> UNKNOWN
            }
        }
    }
}

/**
 * Метод определения результата звонка.
 */
enum class ResolveMethod(val apiValue: String) {
    OBSERVER("observer"),    // Определено через ContentObserver (CallLogObserverManager)
    RETRY("retry"),          // Определено через повторные проверки (CallListenerService.scheduleCallLogChecks)
    UNKNOWN("unknown");      // Неизвестно
}

/**
 * Источник действия пользователя (откуда пришла команда на звонок).
 */
enum class ActionSource(val apiValue: String) {
    CRM_UI("crm_ui"),           // Команда из CRM (polling)
    NOTIFICATION("notification"), // Нажатие на уведомление "Пора позвонить"
    HISTORY("history"),         // Нажатие "Перезвонить" из истории звонков
    UNKNOWN("unknown");          // Неизвестно (ручной звонок или не отслеживается)
}

/**
 * Payload для отправки данных о звонке в CRM.
 * ЭТАП 1: Структура готова, но реальная отправка новых полей начнётся в ЭТАП 2.
 */
data class CallEventPayload(
    val callRequestId: String,
    val callStatus: String? = null,
    val callStartedAt: Long? = null,
    val callDurationSeconds: Int? = null,
    // Новые поля (ЭТАП 1: структура, ЭТАП 2: реальная отправка)
    val callEndedAt: Long? = null,
    val direction: String? = null,
    val resolveMethod: String? = null,
    // Почему результат UNKNOWN (это нормально, не failure)
    val resolveReason: String? = null,
    val reasonIfUnknown: String? = null,
    val attemptsCount: Int? = null,
    val actionSource: String? = null
) {
    /**
     * Создать legacy payload (только 4 поля для обратной совместимости).
     */
    fun toLegacyJson(): String {
        val json = org.json.JSONObject()
        json.put("call_request_id", callRequestId)
        if (callStatus != null) json.put("call_status", callStatus)
        if (callStartedAt != null) {
            val date = java.util.Date(callStartedAt)
            val sdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
            sdf.timeZone = java.util.TimeZone.getTimeZone("UTC")
            json.put("call_started_at", sdf.format(date))
        }
        if (callDurationSeconds != null) json.put("call_duration_seconds", callDurationSeconds)
        return json.toString()
    }
    
    /**
     * Создать extended payload (со всеми полями).
     * ЭТАП 2: будет использоваться для отправки новых полей.
     */
    fun toExtendedJson(): String {
        val json = org.json.JSONObject()
        json.put("call_request_id", callRequestId)
        if (callStatus != null) json.put("call_status", callStatus)
        if (callStartedAt != null) {
            val date = java.util.Date(callStartedAt)
            val sdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
            sdf.timeZone = java.util.TimeZone.getTimeZone("UTC")
            json.put("call_started_at", sdf.format(date))
        }
        if (callDurationSeconds != null) json.put("call_duration_seconds", callDurationSeconds)
        // Новые поля (ЭТАП 2: будут заполняться реальными данными)
        if (callEndedAt != null) {
            val date = java.util.Date(callEndedAt)
            val sdf = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", java.util.Locale.US)
            sdf.timeZone = java.util.TimeZone.getTimeZone("UTC")
            json.put("call_ended_at", sdf.format(date))
        }
        if (direction != null) json.put("direction", direction)
        if (resolveMethod != null) json.put("resolve_method", resolveMethod)
        if (resolveReason != null) json.put("resolve_reason", resolveReason)
        if (reasonIfUnknown != null) json.put("reason_if_unknown", reasonIfUnknown)
        if (attemptsCount != null) json.put("attempts_count", attemptsCount)
        if (actionSource != null) json.put("action_source", actionSource)
        return json.toString()
    }
}
