package ru.groupprofi.crmprofi.dialer.auth

import android.content.Context
import android.content.SharedPreferences
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * Единая точка правды для управления токенами и учетными данными.
 * Использует EncryptedSharedPreferences с fallback на обычные prefs.
 * Обеспечивает thread-safe refresh токенов под Mutex.
 */
class TokenManager private constructor(context: Context) {
    private val prefs: SharedPreferences
    private val refreshMutex = Mutex()
    
    init {
        prefs = createSecurePrefs(context)
        // Миграция старых plain prefs (если есть)
        migrateOldPrefsIfNeeded(context)
    }
    
    companion object {
        private const val PREFS = "crmprofi_dialer"
        private const val KEY_ACCESS = "access"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_USERNAME = "username"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_IS_ADMIN = "is_admin"
        private const val KEY_ENCRYPTION_ENABLED = "_encryption_enabled"
        private const val KEY_MIGRATED = "_migrated_to_token_manager"

        // Состояние блокировки готовности сервиса (не ошибка, а причина "не готово к звонкам")
        private const val KEY_SERVICE_BLOCK_REASON = "service_block_reason"
        private const val KEY_SERVICE_BLOCK_AT = "service_block_at"
        private const val KEY_LAST_SERVICE_FOREGROUND_OK_AT = "service_foreground_ok_at"

        // Метрики polling / команда на звонок (для диагностики и аналитики)
        private const val KEY_LAST_POLL_LATENCY_MS = "last_poll_latency_ms"
        private const val KEY_LAST_COMMAND_CALL_REQUEST_ID = "last_command_call_request_id"
        private const val KEY_LAST_COMMAND_RECEIVED_AT = "last_command_received_at"
        private const val KEY_LAST_DIALER_OPENED_AT = "last_dialer_opened_at"
        private const val KEY_LAST_DIALER_OPENED_CALL_REQUEST_ID = "last_dialer_opened_call_request_id"
        
        @Volatile
        private var INSTANCE: TokenManager? = null
        
        /**
         * Получить singleton экземпляр TokenManager.
         */
        fun getInstance(context: Context): TokenManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: TokenManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    /**
     * Создать EncryptedSharedPreferences с fallback на обычные prefs.
     */
    private fun createSecurePrefs(context: Context): SharedPreferences {
        return try {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            EncryptedSharedPreferences.create(
                context,
                PREFS,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM
            ).also {
                it.edit().putBoolean(KEY_ENCRYPTION_ENABLED, true).apply()
            }
        } catch (e: Exception) {
            Log.w("TokenManager", "EncryptedSharedPreferences failed, using fallback: ${e.message}")
            val fallback = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            fallback.edit().putBoolean(KEY_ENCRYPTION_ENABLED, false).apply()
            fallback
        }
    }
    
    /**
     * Миграция старых plain prefs (если они есть) в secure prefs.
     * Выполняется один раз при первом запуске TokenManager.
     */
    private fun migrateOldPrefsIfNeeded(context: Context) {
        val migrated = prefs.getBoolean(KEY_MIGRATED, false)
        if (migrated) return
        
        try {
            // Проверяем, есть ли старые plain prefs с данными
            val oldPrefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val oldAccess = oldPrefs.getString(KEY_ACCESS, null)
            val oldRefresh = oldPrefs.getString(KEY_REFRESH, null)
            val oldUsername = oldPrefs.getString(KEY_USERNAME, null)
            val oldDeviceId = oldPrefs.getString(KEY_DEVICE_ID, null)
            
            // Если есть данные в старых prefs и их нет в secure prefs - мигрируем
            if (!oldAccess.isNullOrBlank() && getAccessToken() == null) {
                Log.i("TokenManager", "Migrating tokens from old prefs to secure prefs")
                prefs.edit()
                    .putString(KEY_ACCESS, oldAccess)
                    .putString(KEY_REFRESH, oldRefresh ?: "")
                    .putString(KEY_USERNAME, oldUsername ?: "")
                    .putString(KEY_DEVICE_ID, oldDeviceId ?: "")
                    .putBoolean(KEY_MIGRATED, true)
                    .apply()
                
                // Очищаем старые plain prefs (опционально, можно оставить для совместимости)
                // oldPrefs.edit().clear().apply()
            } else {
                // Просто помечаем как мигрированные
                prefs.edit().putBoolean(KEY_MIGRATED, true).apply()
            }
        } catch (e: Exception) {
            Log.e("TokenManager", "Migration error: ${e.message}")
            // Помечаем как мигрированные, чтобы не повторять
            prefs.edit().putBoolean(KEY_MIGRATED, true).apply()
        }
    }
    
    /**
     * Получить access token (может быть null).
     */
    fun getAccessToken(): String? {
        return prefs.getString(KEY_ACCESS, null)?.takeIf { it.isNotBlank() }
    }
    
    /**
     * Получить refresh token (может быть null).
     */
    fun getRefreshToken(): String? {
        return prefs.getString(KEY_REFRESH, null)?.takeIf { it.isNotBlank() }
    }
    
    /**
     * Получить username (может быть null).
     */
    fun getUsername(): String? {
        return prefs.getString(KEY_USERNAME, null)?.takeIf { it.isNotBlank() }
    }
    
    /**
     * Получить device_id (может быть null).
     */
    fun getDeviceId(): String? {
        return prefs.getString(KEY_DEVICE_ID, null)?.takeIf { it.isNotBlank() }
    }
    
    /**
     * Сохранить токены и username.
     */
    fun saveTokens(access: String, refresh: String, username: String) {
        prefs.edit()
            .putString(KEY_ACCESS, access)
            .putString(KEY_REFRESH, refresh)
            .putString(KEY_USERNAME, username)
            .apply()
    }
    
    /**
     * Сохранить device_id.
     */
    fun saveDeviceId(deviceId: String) {
        prefs.edit()
            .putString(KEY_DEVICE_ID, deviceId)
            .apply()
    }
    
    /**
     * Обновить только access token (после refresh).
     */
    fun updateAccessToken(access: String) {
        prefs.edit()
            .putString(KEY_ACCESS, access)
            .apply()
    }
    
    /**
     * Проверить, включено ли шифрование.
     */
    fun isEncryptionEnabled(): Boolean {
        return prefs.getBoolean(KEY_ENCRYPTION_ENABLED, true)
    }
    
    /**
     * Очистить все токены и учетные данные.
     */
    fun clearAll() {
        prefs.edit()
            .remove(KEY_ACCESS)
            .remove(KEY_REFRESH)
            .remove(KEY_USERNAME)
            .remove(KEY_DEVICE_ID)
            .apply()
    }
    
    /**
     * Проверить, есть ли сохраненные токены (пользователь залогинен).
     */
    fun hasTokens(): Boolean {
        return !getAccessToken().isNullOrBlank() && !getRefreshToken().isNullOrBlank()
    }
    
    /**
     * Получить Mutex для refresh операций (для использования в ApiClient).
     * ВАЖНО: не вызывать refresh напрямую из TokenManager, это делает ApiClient.
     */
    fun getRefreshMutex(): Mutex {
        return refreshMutex
    }
    
    /**
     * Сохранить last poll code и time (для UI статуса).
     */
    fun saveLastPoll(code: Int, time: String) {
        prefs.edit()
            .putInt("last_poll_code", code)
            .putString("last_poll_at", time)
            .apply()
    }
    
    /**
     * Получить last poll code.
     */
    fun getLastPollCode(): Int {
        return prefs.getInt("last_poll_code", -1)
    }
    
    /**
     * Получить last poll time.
     */
    fun getLastPollAt(): String? {
        return prefs.getString("last_poll_at", null)
    }

    fun saveLastPollLatencyMs(ms: Long) {
        prefs.edit().putLong(KEY_LAST_POLL_LATENCY_MS, ms.coerceAtLeast(0)).apply()
    }

    fun getLastPollLatencyMs(): Long? {
        val v = prefs.getLong(KEY_LAST_POLL_LATENCY_MS, -1L)
        return v.takeIf { it >= 0L }
    }

    /**
     * Команда на звонок получена устройством (device_received_at).
     */
    fun saveLastCallCommand(callRequestId: String, receivedAtMs: Long) {
        prefs.edit()
            .putString(KEY_LAST_COMMAND_CALL_REQUEST_ID, callRequestId)
            .putLong(KEY_LAST_COMMAND_RECEIVED_AT, receivedAtMs)
            .apply()
    }

    fun getLastCallCommandId(): String? = prefs.getString(KEY_LAST_COMMAND_CALL_REQUEST_ID, null)

    fun getLastCallCommandReceivedAt(): Long? {
        val v = prefs.getLong(KEY_LAST_COMMAND_RECEIVED_AT, 0L)
        return v.takeIf { it > 0L }
    }

    /**
     * Системная звонилка открыта (dialer_opened_at) — для измерения задержек на устройстве.
     */
    fun saveLastDialerOpened(callRequestId: String, openedAtMs: Long) {
        prefs.edit()
            .putString(KEY_LAST_DIALER_OPENED_CALL_REQUEST_ID, callRequestId)
            .putLong(KEY_LAST_DIALER_OPENED_AT, openedAtMs)
            .apply()
    }

    fun getLastDialerOpenedAt(): Long? {
        val v = prefs.getLong(KEY_LAST_DIALER_OPENED_AT, 0L)
        return v.takeIf { it > 0L }
    }

    fun getLastDialerOpenedCallRequestId(): String? =
        prefs.getString(KEY_LAST_DIALER_OPENED_CALL_REQUEST_ID, null)
    
    /**
     * Сохранить флаг is_admin.
     */
    fun saveIsAdmin(isAdmin: Boolean) {
        prefs.edit()
            .putBoolean(KEY_IS_ADMIN, isAdmin)
            .apply()
    }
    
    /**
     * Проверить, является ли пользователь администратором.
     */
    fun isAdmin(): Boolean {
        return prefs.getBoolean(KEY_IS_ADMIN, false)
    }

    /**
     * Сохранить причину, почему приложение/сервис не готовы к звонкам.
     * null означает "причина очищена".
     */
    fun setServiceBlockReason(reason: ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason?) {
        val editor = prefs.edit()
        if (reason == null) {
            editor.remove(KEY_SERVICE_BLOCK_REASON)
            editor.remove(KEY_SERVICE_BLOCK_AT)
        } else {
            editor.putString(KEY_SERVICE_BLOCK_REASON, reason.name)
            editor.putLong(KEY_SERVICE_BLOCK_AT, System.currentTimeMillis())
        }
        editor.apply()
    }

    fun getServiceBlockReason(): ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason? {
        val raw = prefs.getString(KEY_SERVICE_BLOCK_REASON, null) ?: return null
        return try {
            ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason.valueOf(raw)
        } catch (_: Exception) {
            null
        }
    }

    fun getServiceBlockAt(): Long? {
        val v = prefs.getLong(KEY_SERVICE_BLOCK_AT, 0L)
        return v.takeIf { it > 0L }
    }

    /**
     * Отметка, что startForeground был успешен (для диагностики "сервис жив").
     */
    fun markServiceForegroundOk() {
        prefs.edit().putLong(KEY_LAST_SERVICE_FOREGROUND_OK_AT, System.currentTimeMillis()).apply()
    }

    fun getLastServiceForegroundOkAt(): Long? {
        val v = prefs.getLong(KEY_LAST_SERVICE_FOREGROUND_OK_AT, 0L)
        return v.takeIf { it > 0L }
    }
}
