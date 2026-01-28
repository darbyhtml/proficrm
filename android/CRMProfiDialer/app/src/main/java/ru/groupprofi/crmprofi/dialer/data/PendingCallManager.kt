package ru.groupprofi.crmprofi.dialer.data

import android.content.Context
import android.content.SharedPreferences
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import java.util.concurrent.ConcurrentHashMap

/**
 * Реализация PendingCallStore для управления жизненным циклом ожидаемых звонков.
 * Хранит в памяти и дублирует в SharedPreferences для устойчивости.
 */
class PendingCallManager private constructor(context: Context) : PendingCallStore {
    private val prefs: SharedPreferences = context.getSharedPreferences("pending_calls", Context.MODE_PRIVATE)
    private val cache = ConcurrentHashMap<String, PendingCall>()
    private val mutex = Mutex()
    
    // Реактивный поток наличия активных ожидаемых звонков
    private val _hasActivePendingCallsFlow = MutableStateFlow(false)
    override val hasActivePendingCallsFlow: StateFlow<Boolean> = _hasActivePendingCallsFlow.asStateFlow()
    
    companion object {
        @Volatile
        private var INSTANCE: PendingCallManager? = null
        
        fun getInstance(context: Context): PendingCallManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: PendingCallManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    init {
        // Загружаем сохранённые звонки при инициализации
        loadFromPrefs()
        updateActiveCallsFlow()
    }
    
    /**
     * Обновить поток активных ожидаемых звонков.
     */
    private fun updateActiveCallsFlow() {
        val hasActive = cache.values.any { 
            it.state == PendingCall.PendingState.PENDING || 
            it.state == PendingCall.PendingState.RESOLVING 
        }
        _hasActivePendingCallsFlow.value = hasActive
    }
    
    /**
     * Добавить новый ожидаемый звонок.
     */
    override suspend fun addPendingCall(call: PendingCall) = withContext(Dispatchers.IO) {
        mutex.withLock {
            cache[call.callRequestId] = call
            saveToPrefs()
            updateActiveCallsFlow()
        }
    }
    
    /**
     * Получить ожидаемый звонок по ID запроса.
     */
    override suspend fun getPendingCall(callRequestId: String): PendingCall? = withContext(Dispatchers.IO) {
        mutex.withLock {
            cache[callRequestId]
        }
    }
    
    /**
     * Получить все активные ожидаемые звонки (PENDING или RESOLVING).
     */
    override suspend fun getActivePendingCalls(): List<PendingCall> = withContext(Dispatchers.IO) {
        mutex.withLock {
            cache.values.filter { 
                it.state == PendingCall.PendingState.PENDING || 
                it.state == PendingCall.PendingState.RESOLVING 
            }
        }
    }
    
    /**
     * Обновить состояние звонка.
     */
    override suspend fun updateCallState(
        callRequestId: String,
        newState: PendingCall.PendingState,
        incrementAttempts: Boolean
    ) = withContext(Dispatchers.IO) {
        mutex.withLock {
            val existing = cache[callRequestId] ?: return@withContext
            val updated = existing.copy(
                state = newState,
                attempts = if (incrementAttempts) existing.attempts + 1 else existing.attempts
            )
            cache[callRequestId] = updated
            saveToPrefs()
            updateActiveCallsFlow()
        }
    }
    
    /**
     * Удалить ожидаемый звонок (после успешного определения результата).
     */
    override suspend fun removePendingCall(callRequestId: String) = withContext(Dispatchers.IO) {
        mutex.withLock {
            cache.remove(callRequestId)
            saveToPrefs()
            updateActiveCallsFlow()
        }
    }
    
    /**
     * Очистить старые завершённые звонки (старше 1 часа).
     */
    override suspend fun cleanupOldCalls() = withContext(Dispatchers.IO) {
        mutex.withLock {
            val now = System.currentTimeMillis()
            val toRemove = cache.values.filter { call ->
                (call.state == PendingCall.PendingState.RESOLVED || 
                 call.state == PendingCall.PendingState.FAILED) &&
                (now - call.startedAtMillis) > 3600000 // 1 час
            }
            toRemove.forEach { cache.remove(it.callRequestId) }
            if (toRemove.isNotEmpty()) {
                saveToPrefs()
                updateActiveCallsFlow()
            }
        }
    }
    
    /**
     * Очистить устаревшие активные звонки (если прошло больше 10 минут и звонок не состоялся).
     * Помечает их как FAILED и возвращает список ID для удаления из истории (если нужно).
     * Увеличено до 10 минут для более надежного определения результата звонка.
     */
    suspend fun cleanupExpiredPendingCalls(): List<String> = withContext(Dispatchers.IO) {
        mutex.withLock {
            val now = System.currentTimeMillis()
            // Требование: максимум 2–5 минут ожидания результата (дальше это не "ошибка", а timeout → unknown)
            val expiredTimeout = 5 * 60 * 1000L // 5 минут
            
            val expired = cache.values.filter { call ->
                (call.state == PendingCall.PendingState.PENDING || 
                 call.state == PendingCall.PendingState.RESOLVING) &&
                (now - call.startedAtMillis) > expiredTimeout
            }
            
            // Помечаем устаревшие звонки как FAILED
            expired.forEach { call ->
                val updated = call.copy(state = PendingCall.PendingState.FAILED)
                cache[call.callRequestId] = updated
            }
            
            if (expired.isNotEmpty()) {
                saveToPrefs()
                updateActiveCallsFlow()
            }
            
            // Возвращаем список ID устаревших звонков для обработки
            expired.map { it.callRequestId }
        }
    }
    
    /**
     * Очистить все ожидаемые звонки (для Safe Mode).
     */
    override suspend fun clearAll() = withContext(Dispatchers.IO) {
        mutex.withLock {
            cache.clear()
            saveToPrefs()
            updateActiveCallsFlow()
        }
    }

    /**
     * Атомарно пометить звонок как RESOLVING, если он ещё в состоянии PENDING.
     * Увеличивает attempts на 1 при успешном переходе.
     */
    override suspend fun tryMarkResolving(callRequestId: String): Boolean = withContext(Dispatchers.IO) {
        mutex.withLock {
            val existing = cache[callRequestId] ?: return@withContext false
            if (existing.state != PendingCall.PendingState.PENDING) {
                return@withContext false
            }
            val updated = existing.copy(
                state = PendingCall.PendingState.RESOLVING,
                attempts = existing.attempts + 1
            )
            cache[callRequestId] = updated
            saveToPrefs()
            updateActiveCallsFlow()
            true
        }
    }
    
    /**
     * Сохранить в SharedPreferences.
     * ЭТАП 2: Сохраняем actionSource (если есть).
     */
    private fun saveToPrefs() {
        val jsonArray = JSONArray()
        cache.values.forEach { call ->
            val json = JSONObject().apply {
                put("callRequestId", call.callRequestId)
                put("phoneNumber", call.phoneNumber)
                put("startedAtMillis", call.startedAtMillis)
                put("state", call.state.name)
                put("attempts", call.attempts)
                // Новое поле (ЭТАП 2: сохраняем только если есть)
                if (call.actionSource != null) {
                    put("actionSource", call.actionSource.apiValue)
                }
            }
            jsonArray.put(json)
        }
        prefs.edit().putString("calls", jsonArray.toString()).apply()
    }
    
    /**
     * Загрузить из SharedPreferences.
     * ЭТАП 2: Безопасная загрузка actionSource (если отсутствует - null).
     */
    private fun loadFromPrefs() {
        val jsonStr = prefs.getString("calls", null) ?: return
        try {
            val jsonArray = JSONArray(jsonStr)
            for (i in 0 until jsonArray.length()) {
                val json = jsonArray.getJSONObject(i)
                val state = PendingCall.PendingState.valueOf(json.getString("state"))
                
                // Безопасная загрузка actionSource (если отсутствует - null)
                val actionSourceStr = json.optString("actionSource")
                val actionSource = if (actionSourceStr.isNullOrEmpty()) null else {
                    try {
                        ActionSource.values().find { it.apiValue == actionSourceStr }
                    } catch (e: Exception) {
                        null
                    }
                }
                
                val call = PendingCall(
                    callRequestId = json.getString("callRequestId"),
                    phoneNumber = json.getString("phoneNumber"),
                    startedAtMillis = json.getLong("startedAtMillis"),
                    state = state,
                    attempts = json.getInt("attempts"),
                    actionSource = actionSource
                )
                // Загружаем только активные звонки (PENDING или RESOLVING)
                if (state == PendingCall.PendingState.PENDING || state == PendingCall.PendingState.RESOLVING) {
                    cache[call.callRequestId] = call
                }
            }
        } catch (e: Exception) {
            // Игнорируем ошибки парсинга (старые записи продолжают читаться)
        }
    }
}
