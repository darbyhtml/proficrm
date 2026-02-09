package ru.groupprofi.crmprofi.dialer.auth

import android.content.Context
import android.content.SharedPreferences
import android.os.Trace
import android.util.Log
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

/**
 * Единая точка правды для управления токенами и учетными данными.
 * Использует EncryptedSharedPreferences с fallback на обычные prefs.
 * Обеспечивает thread-safe refresh токенов под Mutex.
 *
 * ВАЖНО: тяжёлая инициализация (EncryptedSharedPreferences, Tink keyset) выполняется
 * только в фоне. Вызовите [init] до первого [getInstance].
 */
class TokenManager private constructor(private val prefs: SharedPreferences) {
    private val refreshMutex = Mutex()

    companion object {
        private const val PREFS = "crmprofi_dialer"
        private const val KEY_ACCESS = "access"
        private const val KEY_REFRESH = "refresh"
        private const val KEY_USERNAME = "username"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_IS_ADMIN = "is_admin"
        private const val KEY_ENCRYPTION_ENABLED = "_encryption_enabled"
        private const val KEY_MIGRATED = "_migrated_to_token_manager"

        private const val KEY_SERVICE_BLOCK_REASON = "service_block_reason"
        private const val KEY_SERVICE_BLOCK_AT = "service_block_at"
        private const val KEY_LAST_SERVICE_FOREGROUND_OK_AT = "service_foreground_ok_at"
        private const val KEY_LAST_POLL_LATENCY_MS = "last_poll_latency_ms"
        private const val KEY_LAST_COMMAND_CALL_REQUEST_ID = "last_command_call_request_id"
        private const val KEY_LAST_COMMAND_RECEIVED_AT = "last_command_received_at"
        private const val KEY_LAST_DIALER_OPENED_AT = "last_dialer_opened_at"
        private const val KEY_LAST_DIALER_OPENED_CALL_REQUEST_ID = "last_dialer_opened_call_request_id"
        private const val KEY_LAST_REFRESH_SUCCESS_AT = "last_refresh_success_at"
        private const val KEY_REFRESH_FAILURE_COUNT = "refresh_failure_count"

        @Volatile
        private var INSTANCE: TokenManager? = null

        private val initMutex = Mutex()

        /**
         * Асинхронная инициализация (idempotent). Вызывать с applicationContext.
         * Вся тяжёлая работа (disk I/O, crypto) выполняется на Dispatchers.IO.
         */
        suspend fun init(appContext: Context): TokenManager = initMutex.withLock {
            INSTANCE?.let { return it }
            withContext(Dispatchers.IO) {
                Trace.beginSection("TokenManager.init")
                try {
                    val ctx = appContext.applicationContext
                    val prefs = createSecurePrefs(ctx)
                    val tm = TokenManager(prefs)
                    tm.migrateOldPrefsIfNeeded(ctx)
                    INSTANCE = tm
                    tm
                } finally {
                    Trace.endSection()
                }
            }
        }

        /**
         * Получить инициализированный экземпляр. Вызывать только после [init].
         * @throws IllegalStateException если [init] ещё не был вызван
         */
        fun getInstance(): TokenManager {
            return INSTANCE ?: throw IllegalStateException(
                "TokenManager not initialized. Call TokenManager.init(applicationContext) first."
            )
        }

        /**
         * Получить экземпляр или null, если ещё не инициализирован (для деградированного режима).
         */
        fun getInstanceOrNull(): TokenManager? = INSTANCE

        /**
         * Создать EncryptedSharedPreferences с fallback на обычные prefs.
         * Вызывать только с Dispatchers.IO (disk + crypto).
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
    }

    /**
     * Миграция старых plain prefs (если они есть) в secure prefs.
     * Вызывать только из init на IO dispatcher.
     */
    private fun migrateOldPrefsIfNeeded(context: Context) {
        val migrated = prefs.getBoolean(KEY_MIGRATED, false)
        if (migrated) return

        try {
            val oldPrefs = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
            val oldAccess = oldPrefs.getString(KEY_ACCESS, null)
            val oldRefresh = oldPrefs.getString(KEY_REFRESH, null)
            val oldUsername = oldPrefs.getString(KEY_USERNAME, null)
            val oldDeviceId = oldPrefs.getString(KEY_DEVICE_ID, null)

            if (!oldAccess.isNullOrBlank() && getAccessToken() == null) {
                Log.i("TokenManager", "Migrating tokens from old prefs to secure prefs")
                prefs.edit()
                    .putString(KEY_ACCESS, oldAccess)
                    .putString(KEY_REFRESH, oldRefresh ?: "")
                    .putString(KEY_USERNAME, oldUsername ?: "")
                    .putString(KEY_DEVICE_ID, oldDeviceId ?: "")
                    .putBoolean(KEY_MIGRATED, true)
                    .apply()
            } else {
                prefs.edit().putBoolean(KEY_MIGRATED, true).apply()
            }
        } catch (e: Exception) {
            Log.e("TokenManager", "Migration error: ${e.message}")
            prefs.edit().putBoolean(KEY_MIGRATED, true).apply()
        }
    }

    fun getAccessToken(): String? =
        prefs.getString(KEY_ACCESS, null)?.takeIf { it.isNotBlank() }

    fun getRefreshToken(): String? =
        prefs.getString(KEY_REFRESH, null)?.takeIf { it.isNotBlank() }

    fun getUsername(): String? =
        prefs.getString(KEY_USERNAME, null)?.takeIf { it.isNotBlank() }

    fun getDeviceId(): String? =
        prefs.getString(KEY_DEVICE_ID, null)?.takeIf { it.isNotBlank() }

    fun saveTokens(access: String, refresh: String, username: String) {
        prefs.edit()
            .putString(KEY_ACCESS, access)
            .putString(KEY_REFRESH, refresh)
            .putString(KEY_USERNAME, username)
            .apply()
    }

    fun saveDeviceId(deviceId: String) {
        prefs.edit().putString(KEY_DEVICE_ID, deviceId).apply()
    }

    fun updateAccessToken(access: String) {
        prefs.edit().putString(KEY_ACCESS, access).apply()
    }

    fun isEncryptionEnabled(): Boolean =
        prefs.getBoolean(KEY_ENCRYPTION_ENABLED, true)

    fun clearAll() {
        prefs.edit()
            .remove(KEY_ACCESS)
            .remove(KEY_REFRESH)
            .remove(KEY_USERNAME)
            .remove(KEY_DEVICE_ID)
            .apply()
    }

    fun hasTokens(): Boolean =
        !getAccessToken().isNullOrBlank() && !getRefreshToken().isNullOrBlank()

    fun getRefreshMutex(): Mutex = refreshMutex

    fun saveLastPoll(code: Int, time: String) {
        prefs.edit()
            .putInt("last_poll_code", code)
            .putString("last_poll_at", time)
            .apply()
    }

    fun getLastPollCode(): Int = prefs.getInt("last_poll_code", -1)

    fun getLastPollAt(): String? = prefs.getString("last_poll_at", null)

    fun saveLastPollLatencyMs(ms: Long) {
        prefs.edit().putLong(KEY_LAST_POLL_LATENCY_MS, ms.coerceAtLeast(0)).apply()
    }

    fun getLastPollLatencyMs(): Long? {
        val v = prefs.getLong(KEY_LAST_POLL_LATENCY_MS, -1L)
        return v.takeIf { it >= 0L }
    }

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

    fun saveIsAdmin(isAdmin: Boolean) {
        prefs.edit().putBoolean(KEY_IS_ADMIN, isAdmin).apply()
    }

    fun isAdmin(): Boolean = prefs.getBoolean(KEY_IS_ADMIN, false)
    
    /**
     * Сохранить время последнего успешного refresh токена.
     */
    fun saveLastRefreshSuccessAt(timestamp: Long) {
        prefs.edit().putLong(KEY_LAST_REFRESH_SUCCESS_AT, timestamp).apply()
    }
    
    /**
     * Получить время последнего успешного refresh токена.
     */
    fun getLastRefreshSuccessAt(): Long? {
        val v = prefs.getLong(KEY_LAST_REFRESH_SUCCESS_AT, 0L)
        return v.takeIf { it > 0L }
    }
    
    /**
     * Увеличить счетчик неудачных попыток refresh.
     */
    fun incrementRefreshFailureCount() {
        val current = prefs.getInt(KEY_REFRESH_FAILURE_COUNT, 0)
        prefs.edit().putInt(KEY_REFRESH_FAILURE_COUNT, current + 1).apply()
    }
    
    /**
     * Сбросить счетчик неудачных попыток refresh.
     */
    fun resetRefreshFailureCount() {
        prefs.edit().remove(KEY_REFRESH_FAILURE_COUNT).apply()
    }
    
    /**
     * Получить счетчик неудачных попыток refresh.
     */
    fun getRefreshFailureCount(): Int = prefs.getInt(KEY_REFRESH_FAILURE_COUNT, 0)

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

    fun markServiceForegroundOk() {
        prefs.edit().putLong(KEY_LAST_SERVICE_FOREGROUND_OK_AT, System.currentTimeMillis()).apply()
    }

    fun getLastServiceForegroundOkAt(): Long? {
        val v = prefs.getLong(KEY_LAST_SERVICE_FOREGROUND_OK_AT, 0L)
        return v.takeIf { it > 0L }
    }
}
