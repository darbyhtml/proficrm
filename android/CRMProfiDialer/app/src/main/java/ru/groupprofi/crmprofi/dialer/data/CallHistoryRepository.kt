package ru.groupprofi.crmprofi.dialer.data

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import java.util.concurrent.ConcurrentHashMap

/**
 * Реализация CallHistoryStore для хранения истории звонков локально.
 * Использует SharedPreferences для простоты (можно заменить на Room при необходимости).
 */
class CallHistoryRepository private constructor(context: Context) : CallHistoryStore {
    private val prefs: SharedPreferences = context.getSharedPreferences("call_history", Context.MODE_PRIVATE)
    private val cache = ConcurrentHashMap<String, CallHistoryItem>()
    
    // Реактивный поток истории звонков
    private val _callsFlow = MutableStateFlow<List<CallHistoryItem>>(emptyList())
    override val callsFlow: StateFlow<List<CallHistoryItem>> = _callsFlow.asStateFlow()
    
    // Реактивный поток количества звонков
    private val _countFlow = MutableStateFlow(0)
    override val countFlow: StateFlow<Int> = _countFlow.asStateFlow()
    
    companion object {
        @Volatile
        private var INSTANCE: CallHistoryRepository? = null
        
        fun getInstance(context: Context): CallHistoryRepository {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: CallHistoryRepository(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    /**
     * Добавить или обновить звонок в истории.
     */
    override suspend fun addOrUpdate(call: CallHistoryItem) = withContext(Dispatchers.IO) {
        cache[call.id] = call
        saveToPrefs()
        updateFlows()
    }
    
    /**
     * @deprecated Используйте addOrUpdate
     */
    @Deprecated("Используйте addOrUpdate", ReplaceWith("addOrUpdate(call)"))
    suspend fun saveCall(call: CallHistoryItem) = addOrUpdate(call)
    
    /**
     * Отметить звонок как отправленный в CRM.
     */
    override suspend fun markSent(callId: String, sentAt: Long) = withContext(Dispatchers.IO) {
        val call = cache[callId] ?: return@withContext
        val updated = call.copy(sentToCrm = true, sentToCrmAt = sentAt)
        cache[callId] = updated
        saveToPrefs()
        updateFlows()
    }
    
    /**
     * @deprecated Используйте markSent
     */
    @Deprecated("Используйте markSent", ReplaceWith("markSent(callId, sentAt)"))
    suspend fun markAsSentToCrm(callId: String, sentAt: Long) = markSent(callId, sentAt)
    
    /**
     * Получить все звонки.
     */
    override suspend fun getAllCalls(): List<CallHistoryItem> = withContext(Dispatchers.IO) {
        if (cache.isEmpty()) {
            loadFromPrefs()
        }
        cache.values.toList()
    }
    
    /**
     * Получить звонок по ID.
     */
    override suspend fun getCallById(id: String): CallHistoryItem? = withContext(Dispatchers.IO) {
        if (cache.isEmpty()) {
            loadFromPrefs()
        }
        cache[id]
    }
    
    /**
     * Сохранить в SharedPreferences.
     * ЭТАП 2: Сохраняем новые поля (если есть), старые записи продолжают читаться.
     */
    private fun saveToPrefs() {
        val jsonArray = JSONArray()
        cache.values.forEach { call ->
            val json = JSONObject().apply {
                put("id", call.id)
                put("phone", call.phone)
                put("phoneDisplayName", call.phoneDisplayName ?: "")
                put("status", call.status.name)
                put("statusText", call.statusText)
                put("durationSeconds", call.durationSeconds ?: 0)
                put("startedAt", call.startedAt)
                put("sentToCrm", call.sentToCrm)
                put("sentToCrmAt", call.sentToCrmAt ?: 0)
                // Новые поля (ЭТАП 2: сохраняем только если есть)
                if (call.direction != null) put("direction", call.direction.apiValue)
                if (call.resolveMethod != null) put("resolveMethod", call.resolveMethod.apiValue)
                if (call.attemptsCount != null) put("attemptsCount", call.attemptsCount)
                if (call.actionSource != null) put("actionSource", call.actionSource.apiValue)
                if (call.endedAt != null) put("endedAt", call.endedAt)
            }
            jsonArray.put(json)
        }
        prefs.edit().putString("calls", jsonArray.toString()).apply()
    }
    
    /**
     * Загрузить из SharedPreferences.
     * ЭТАП 2: Безопасная загрузка с поддержкой старых записей (новые поля nullable).
     */
    private fun loadFromPrefs() {
        val jsonStr = prefs.getString("calls", null) ?: return
        try {
            val jsonArray = JSONArray(jsonStr)
            for (i in 0 until jsonArray.length()) {
                val json = jsonArray.getJSONObject(i)
                val status = CallHistoryItem.CallStatus.valueOf(json.getString("status"))
                
                // Безопасная загрузка новых полей (если отсутствуют - null)
                val directionStr = json.optString("direction", null)
                val direction = directionStr?.let { 
                    try {
                        CallDirection.values().find { it.apiValue == directionStr }
                    } catch (e: Exception) {
                        null
                    }
                }
                
                val resolveMethodStr = json.optString("resolveMethod", null)
                val resolveMethod = resolveMethodStr?.let {
                    try {
                        ResolveMethod.values().find { it.apiValue == resolveMethodStr }
                    } catch (e: Exception) {
                        null
                    }
                }
                
                val actionSourceStr = json.optString("actionSource", null)
                val actionSource = actionSourceStr?.let {
                    try {
                        ActionSource.values().find { it.apiValue == actionSourceStr }
                    } catch (e: Exception) {
                        null
                    }
                }
                
                val call = CallHistoryItem(
                    id = json.getString("id"),
                    phone = json.getString("phone"),
                    phoneDisplayName = json.optString("phoneDisplayName").takeIf { it.isNotEmpty() },
                    status = status,
                    statusText = json.getString("statusText"),
                    durationSeconds = json.optInt("durationSeconds").takeIf { it > 0 },
                    startedAt = json.getLong("startedAt"),
                    sentToCrm = json.getBoolean("sentToCrm"),
                    sentToCrmAt = json.optLong("sentToCrmAt").takeIf { it > 0 },
                    // Новые поля (ЭТАП 2: безопасная загрузка, если отсутствуют - null)
                    direction = direction,
                    resolveMethod = resolveMethod,
                    attemptsCount = json.optInt("attemptsCount").takeIf { it >= 0 },
                    actionSource = actionSource,
                    endedAt = json.optLong("endedAt").takeIf { it > 0 }
                )
                cache[call.id] = call
            }
        } catch (e: Exception) {
            // Игнорируем ошибки парсинга (старые записи продолжают читаться)
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("CallHistoryRepository", "Ошибка загрузки истории: ${e.message}")
        }
    }
    
    /**
     * Обновить потоки Flow при изменении данных.
     */
    private fun updateFlows() {
        val calls = cache.values.toList()
        _callsFlow.value = calls
        _countFlow.value = calls.size
    }
}
