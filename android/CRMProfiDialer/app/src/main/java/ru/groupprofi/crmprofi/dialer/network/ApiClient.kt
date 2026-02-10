package ru.groupprofi.crmprofi.dialer.network

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.*
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONArray
import org.json.JSONObject
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.queue.QueueManager
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.config.AppFeatures
import ru.groupprofi.crmprofi.dialer.domain.CallEventPayload
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import java.io.IOException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.text.SimpleDateFormat
import java.util.*
import java.util.concurrent.TimeUnit

/**
 * Единый HTTP клиент для всех API вызовов.
 * Включает interceptors для auth, telemetry, logging.
 * Все методы возвращают Result<T> для безопасной обработки ошибок.
 */
class ApiClient private constructor(context: Context) {
    private val tokenManager = TokenManager.getInstance()
    private val appContext = context.applicationContext
    // Ленивая инициализация QueueManager - создается только при первом использовании
    private val queueManager: QueueManager by lazy { QueueManager(appContext) }
    // Используем интерфейс через AppContainer
    private val callHistoryStore: ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
        get() = AppContainer.callHistoryStore
    private val httpClient: OkHttpClient
    // Отдельный OkHttpClient для long-polling pullCall с увеличенными таймаутами
    private val longPollHttpClient: OkHttpClient
    private val jsonMedia = "application/json; charset=utf-8".toMediaType()
    private val baseUrl = BuildConfig.BASE_URL
    private val scope = kotlinx.coroutines.CoroutineScope(kotlinx.coroutines.Dispatchers.IO)
    private val telemetryBatcher: TelemetryBatcher
    
    init {
        // Создаем TelemetryBatcher для батчинга телеметрии
        // Передаем функцию отправки, которая будет вызывать sendTelemetryBatch и маппить Result -> Outcome
        val deviceId = tokenManager.getDeviceId() ?: ""
        telemetryBatcher = TelemetryBatcher(deviceId) { devId, items ->
            val result = sendTelemetryBatch(devId, items)
            when (result) {
                is Result.Success -> TelemetryBatcher.SendBatchOutcome(ok = true)
                is Result.Error -> TelemetryBatcher.SendBatchOutcome(ok = false, errorMessage = result.message)
            }
        }
        
        // TelemetryInterceptor использует только telemetryBatcher
        val telemetryInterceptor = TelemetryInterceptor(telemetryBatcher)
        
        // Основной httpClient для обычных API (короткие таймауты)
        httpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .addInterceptor(AuthInterceptor(tokenManager, context))
            .addInterceptor(telemetryInterceptor)
            .apply {
                // HTTP logging только в debug
                if (BuildConfig.DEBUG) {
                    addInterceptor(SafeHttpLoggingInterceptor())
                }
            }
            .build()
        
        // Отдельный httpClient для long-polling pullCall (увеличенные таймауты)
        longPollHttpClient = OkHttpClient.Builder()
            .connectTimeout(15, TimeUnit.SECONDS) // Connect timeout остается таким же
            .readTimeout(60, TimeUnit.SECONDS) // Read timeout увеличен до 60 сек для long-poll
            .writeTimeout(30, TimeUnit.SECONDS) // Write не используется для GET
            .callTimeout(70, TimeUnit.SECONDS) // Общий таймаут запроса 70 сек
            .addInterceptor(AuthInterceptor(tokenManager, context))
            // НЕ добавляем TelemetryInterceptor для long-poll (чтобы не создавать лавину метрик)
            .apply {
                // HTTP logging только в debug
                if (BuildConfig.DEBUG) {
                    addInterceptor(SafeHttpLoggingInterceptor())
                }
            }
            .build()
    }
    
    /**
     * Принудительно отправить накопленную телеметрию (вызывается при важных событиях).
     * 
     * В режиме LOCAL_ONLY: завершает локальную обработку, НЕ делает network calls.
     * В режиме FULL: отправляет телеметрию в CRM как обычно.
     */
    suspend fun flushTelemetry() {
        if (AppFeatures.isCrmEnabled()) {
            telemetryBatcher.flushNow()
        } else {
            // LOCAL_ONLY режим: завершаем локальную обработку без отправки
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("ApiClient", "flushTelemetry: LOCAL_ONLY mode, skipping network call")
        }
    }
    
    companion object {
        @Volatile
        private var INSTANCE: ApiClient? = null
        
        fun getInstance(context: Context): ApiClient {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: ApiClient(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    /**
     * Результат API вызова (успех или ошибка).
     */
    sealed class Result<out T> {
        data class Success<out T>(val data: T) : Result<T>()
        data class Error(val message: String, val code: Int? = null) : Result<Nothing>()
    }
    
    /**
     * Логин: получить access и refresh токены.
     * Возвращает Triple(access, refresh, isAdmin).
     */
    suspend fun login(username: String, password: String): Result<Triple<String, String, Boolean>> = withContext(Dispatchers.IO) {
        try {
            val url = "$baseUrl/api/token/"
            val bodyJson = JSONObject()
                .put("username", username)
                .put("password", password)
                .toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (!res.isSuccessful) {
                    val errorMsg = try {
                        val errorObj = JSONObject(raw)
                        errorObj.optString("detail", "Ошибка входа")
                    } catch (_: Exception) {
                        "Ошибка входа: HTTP ${res.code}"
                    }
                    return@withContext Result.Error(errorMsg, res.code)
                }
                
                val obj = JSONObject(raw)
                val access = obj.optString("access", "")
                val refresh = obj.optString("refresh", "")
                val isAdmin = obj.optBoolean("is_admin", false)
                
                // Не логируем токены и полный ответ (безопасность)
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("ApiClient", "Login response: is_admin=$isAdmin, payload size=${raw.length}")
                
                if (access.isBlank() || refresh.isBlank()) {
                    return@withContext Result.Error("Неверный формат ответа сервера")
                }
                
                Result.Success(Triple(access, refresh, isAdmin))
            }
        } catch (e: UnknownHostException) {
            Result.Error("Нет подключения к интернету")
        } catch (e: SocketTimeoutException) {
            Result.Error("Превышено время ожидания ответа")
        } catch (e: Exception) {
            Result.Error("Ошибка сети: ${e.message}")
        }
    }
    
    /**
     * Обмен QR-токена на JWT access/refresh токены.
     * Используется для быстрого входа по QR-коду.
     * При временной ошибке DNS (UnknownHostException) повторяет запрос до 3 раз с паузой — часто сетевая готовность запаздывает после камеры/смены экрана.
     */
    suspend fun exchangeQrToken(qrToken: String): Result<QrTokenResult> = withContext(Dispatchers.IO) {
        val url = "$baseUrl/api/phone/qr/exchange/"
        val bodyJson = JSONObject().put("token", qrToken).toString()
        val maxAttempts = 3
        val retryDelayMs = 1500L

        repeat(maxAttempts) { attempt ->
            try {
                val req = Request.Builder()
                    .url(url)
                    .post(bodyJson.toRequestBody(jsonMedia))
                    .build()
                httpClient.newCall(req).execute().use { res ->
                    val raw = res.body?.string() ?: ""
                    if (!res.isSuccessful) {
                        val errorMsg = try {
                            val errorObj = JSONObject(raw)
                            errorObj.optString("detail", "QR-код истёк или уже использован")
                        } catch (_: Exception) {
                            when (res.code) {
                                400 -> "QR-код истёк или уже использован"
                                401 -> "QR-код недействителен"
                                else -> "Ошибка обмена QR-кода: HTTP ${res.code}"
                            }
                        }
                        return@withContext Result.Error(errorMsg, res.code)
                    }
                    val obj = JSONObject(raw)
                    val access = obj.optString("access", "")
                    val refresh = obj.optString("refresh", "")
                    val username = obj.optString("username", "").ifBlank { "user" }
                    val isAdmin = obj.optBoolean("is_admin", false)
                    if (access.isBlank() || refresh.isBlank()) {
                        return@withContext Result.Error("Неверный формат ответа сервера")
                    }
                    return@withContext Result.Success(QrTokenResult(access, refresh, username, isAdmin))
                }
            } catch (e: UnknownHostException) {
                if (attempt == maxAttempts - 1) {
                    return@withContext Result.Error("Не удалось подключиться к серверу. Проверьте интернет и нажмите «Повторить».")
                }
                kotlinx.coroutines.delay(retryDelayMs)
            } catch (e: SocketTimeoutException) {
                return@withContext Result.Error("Превышено время ожидания ответа")
            } catch (e: Exception) {
                return@withContext Result.Error("Ошибка сети: ${e.message}")
            }
        }
        Result.Error("Не удалось подключиться к серверу. Проверьте интернет и нажмите «Повторить».")
    }
    
    /**
     * Refresh токен (вызывается из AuthInterceptor при 401).
     * Использует Mutex из TokenManager для предотвращения параллельных refresh.
     * Улучшенная логика: не очищает токены при временных сетевых ошибках, только при реальном истечении refresh token.
     */
    suspend fun refreshToken(): Result<String?> = withContext(Dispatchers.IO) {
        val refresh = tokenManager.getRefreshToken()
        if (refresh.isNullOrBlank()) {
            return@withContext Result.Error("Refresh token отсутствует", 401)
        }
        
        // Проверяем, не слишком ли много неудачных попыток подряд (backoff)
        val failureCount = tokenManager.getRefreshFailureCount()
        val lastSuccess = tokenManager.getLastRefreshSuccessAt()
        val now = System.currentTimeMillis()
        
        // Если было много неудачных попыток и последний успех был недавно (< 1 часа) - это временная ошибка
        if (failureCount >= 3 && lastSuccess != null && (now - lastSuccess) < 3600000L) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Too many refresh failures ($failureCount), but last success was recent. Skipping refresh to avoid clearing tokens.")
            return@withContext Result.Error("Временная сетевая ошибка, повторите позже", 0)
        }
        
        return@withContext tokenManager.getRefreshMutex().withLock {
            try {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("ApiClient", "Refreshing access token (failure count: $failureCount)")
                val url = "$baseUrl/api/token/refresh/"
                val bodyJson = JSONObject().put("refresh", refresh).toString()
                
                val req = Request.Builder()
                    .url(url)
                    .post(bodyJson.toRequestBody(jsonMedia))
                    .build()
                
                httpClient.newCall(req).execute().use { res ->
                    val raw = res.body?.string() ?: ""
                    if (!res.isSuccessful) {
                        if (res.code == 401 || res.code == 403) {
                            // Refresh token истек на сервере - требуется повторный вход
                            // НО: проверяем, не была ли это временная ошибка (если последний успех был недавно)
                            val shouldClear = if (lastSuccess != null && (now - lastSuccess) < 3600000L) {
                                // Последний успех был недавно - возможно временная ошибка сервера
                                // Не очищаем токены, только логируем
                                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Refresh token returned ${res.code}, but last success was recent. Not clearing tokens (possible temporary server error).")
                                false
                            } else {
                                // Последний успех был давно или его не было - refresh token реально истек
                                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Refresh token expired (${res.code}), clearing tokens")
                                true
                            }
                            
                            if (shouldClear) {
                                tokenManager.clearAll()
                                return@withLock Result.Error("Сессия истекла, требуется повторный вход", res.code)
                            } else {
                                // Временная ошибка - увеличиваем счетчик, но не очищаем токены
                                tokenManager.incrementRefreshFailureCount()
                                return@withLock Result.Error("Временная ошибка сервера, повторите позже", res.code)
                            }
                        }
                        // Другие ошибки сервера (5xx) - не очищаем токены, это временная ошибка
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Refresh token failed: HTTP ${res.code} (temporary error, not clearing tokens)")
                        tokenManager.incrementRefreshFailureCount()
                        return@withLock Result.Error("Ошибка сервера: HTTP ${res.code}", res.code)
                    }
                    
                    val access = JSONObject(raw).optString("access", "").ifBlank { null }
                    if (access == null) {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("ApiClient", "Refresh token: invalid response format")
                        tokenManager.incrementRefreshFailureCount()
                        return@withLock Result.Error("Неверный формат ответа сервера")
                    }
                    
                    // Успешный refresh - сохраняем новый access token и сбрасываем счетчик ошибок
                    tokenManager.updateAccessToken(access)
                    tokenManager.saveLastRefreshSuccessAt(now)
                    tokenManager.resetRefreshFailureCount()
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("ApiClient", "Access token refreshed successfully")
                    Result.Success(access)
                }
            } catch (e: UnknownHostException) {
                // Сетевая ошибка - не очищаем токены, это временная проблема
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Refresh token: network error (no internet), not clearing tokens")
                tokenManager.incrementRefreshFailureCount()
                Result.Error("Нет подключения к интернету")
            } catch (e: SocketTimeoutException) {
                // Таймаут - не очищаем токены, это временная проблема
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Refresh token: timeout, not clearing tokens")
                tokenManager.incrementRefreshFailureCount()
                Result.Error("Превышено время ожидания ответа")
            } catch (e: Exception) {
                // Другие ошибки - не очищаем токены, это временная проблема
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("ApiClient", "Refresh token error: ${e.message}, not clearing tokens", e)
                tokenManager.incrementRefreshFailureCount()
                Result.Error("Ошибка сети: ${e.message}")
            }
        }
    }
    
    /**
     * Регистрация устройства.
     * Опционально может передавать fcmToken для push-ускорителя.
     */
    suspend fun registerDevice(
        deviceId: String,
        deviceName: String,
        fcmToken: String? = null
    ): Result<Unit> = withContext(Dispatchers.IO) {
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Error("Токен отсутствует")
        }
        
        try {
            val url = "$baseUrl/api/phone/devices/register/"
            val bodyObj = JSONObject()
                .put("device_id", deviceId)
                .put("device_name", deviceName)
            val fcm = fcmToken?.trim().orEmpty()
            if (fcm.isNotEmpty()) {
                bodyObj.put("fcm_token", fcm)
            }
            val bodyJson = bodyObj.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    // Регистрация не критична, логируем но не падаем
                    ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Register device failed: HTTP ${res.code}")
                }
                Result.Success(Unit)
            }
        } catch (e: Exception) {
            // Регистрация не критична, логируем но не падаем
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("ApiClient", "Register device error: ${e.message}")
            Result.Success(Unit)
        }
    }
    
    /**
     * Получить следующую команду на звонок (long-polling).
     * Использует long-pollHttpClient с увеличенными таймаутами.
     * Поддерживает параметр wait_seconds для long-polling (если сервер поддерживает).
     * 
     * Возвращает PullCallResult с информацией о Retry-After заголовке для rate limiting.
     */
    suspend fun pullCall(
        deviceId: String,
        waitSeconds: Int = 25, // Таймаут ожидания для long-poll (25 секунд)
        useLongPoll: Boolean = true // Использовать long-poll или обычный запрос
    ): PullCallResult = withContext(Dispatchers.IO) {
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext PullCallResult(Result.Error("Токен отсутствует"), retryAfterSeconds = null, latencyMs = 0L)
        }
        
        val startTime = System.currentTimeMillis()
        try {
            // Добавляем параметр wait_seconds для long-polling (если сервер поддерживает)
            val url = if (useLongPoll && waitSeconds > 0) {
                "$baseUrl/api/phone/calls/pull/?device_id=$deviceId&wait_seconds=$waitSeconds"
            } else {
                "$baseUrl/api/phone/calls/pull/?device_id=$deviceId"
            }
            
            val req = Request.Builder()
                .url(url)
                .get()
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            // Используем longPollHttpClient для long-polling запросов
            val client = if (useLongPoll) longPollHttpClient else httpClient
            
            client.newCall(req).execute().use { res ->
                val requestLatencyMs = System.currentTimeMillis() - startTime
                val code = res.code
                val body = res.body?.string()
                
                // Извлекаем Retry-After заголовок для rate limiting (429)
                val retryAfterHeader = res.header("Retry-After")
                val retryAfterSeconds = retryAfterHeader?.toIntOrNull()
                
                when (code) {
                    204 -> {
                        // Нет команд
                        return@withContext PullCallResult(Result.Success(null), retryAfterSeconds = null, latencyMs = requestLatencyMs)
                    }
                    401 -> {
                        // Unauthorized - попробуем refresh
                        val refreshResult = refreshToken()
                        if (refreshResult is Result.Success && refreshResult.data != null) {
                            // Повторяем запрос с новым токеном
                            val newToken = refreshResult.data
                            val retryReq = Request.Builder()
                                .url(url)
                                .get()
                                .addHeader("Authorization", "Bearer $newToken")
                                .build()
                            
                            httpClient.newCall(retryReq).execute().use { retryRes ->
                                val retryCode = retryRes.code
                                val retryBody = retryRes.body?.string()
                                val retryAfterRetry = retryRes.header("Retry-After")?.toIntOrNull()
                                
                                val result = when {
                                    retryCode == 200 && !retryBody.isNullOrBlank() -> Result.Success(parseCallResponse(retryBody).getOrNull())
                                    retryCode == 204 -> Result.Success(null)
                                    else -> Result.Error("Ошибка после refresh: HTTP $retryCode", retryCode)
                                }
                                return@withContext PullCallResult(result, retryAfterSeconds = retryAfterRetry, latencyMs = requestLatencyMs)
                            }
                        } else {
                            // Refresh не удался - требуется повторный вход
                            return@withContext PullCallResult(Result.Error("Требуется повторный вход", 401), retryAfterSeconds = null, latencyMs = requestLatencyMs)
                        }
                    }
                    200 -> {
                        val result = if (body.isNullOrBlank()) {
                            Result.Success(null)
                        } else {
                            parseCallResponse(body)
                        }
                        return@withContext PullCallResult(result, retryAfterSeconds = null, latencyMs = requestLatencyMs)
                    }
                    429 -> {
                        // Rate limited - возвращаем ошибку с Retry-After
                        return@withContext PullCallResult(
                            Result.Error("Rate limited", 429),
                            retryAfterSeconds = retryAfterSeconds,
                            latencyMs = requestLatencyMs
                        )
                    }
                    else -> {
                        return@withContext PullCallResult(Result.Error("Неожиданный код ответа: $code", code), retryAfterSeconds = null, latencyMs = requestLatencyMs)
                    }
                }
            }
        } catch (e: UnknownHostException) {
            PullCallResult(Result.Error("Нет подключения к интернету", 0), retryAfterSeconds = null, latencyMs = System.currentTimeMillis() - startTime)
        } catch (e: SocketTimeoutException) {
            PullCallResult(Result.Error("Превышено время ожидания ответа", 0), retryAfterSeconds = null, latencyMs = System.currentTimeMillis() - startTime)
        } catch (e: Exception) {
            PullCallResult(Result.Error("Ошибка сети: ${e.message}", 0), retryAfterSeconds = null, latencyMs = System.currentTimeMillis() - startTime)
        }
    }
    
    /**
     * Результат pullCall с информацией о Retry-After заголовке и latency.
     */
    data class PullCallResult(
        val result: Result<PullCallResponse?>,
        val retryAfterSeconds: Int?,
        val latencyMs: Long = 0L // Latency запроса в миллисекундах
    )
    
    /**
     * Расширение Result для получения данных без проверки типа.
     */
    private fun <T> Result<T>.getOrNull(): T? = when (this) {
        is Result.Success -> data
        is Result.Error -> null
    }
    
    private fun parseCallResponse(body: String): Result<PullCallResponse?> {
        return try {
            val obj = JSONObject(body)
            val phone = obj.optString("phone", "")
            val callRequestId = obj.optString("id", "")
            if (phone.isBlank()) {
                Result.Success(null)
            } else {
                Result.Success(PullCallResponse(phone, callRequestId))
            }
        } catch (e: Exception) {
            Result.Error("Ошибка парсинга ответа: ${e.message}")
        }
    }
    
    /**
     * Отправить данные о звонке.
     * ЭТАП 2: Расширенная версия с новыми полями (опциональными для обратной совместимости).
     * 
     * В режиме LOCAL_ONLY: сохраняет данные локально, НЕ отправляет в CRM.
     * В режиме FULL: отправляет в CRM как обычно.
     * 
     * When TelemetryMode.FULL is enabled, events will be sent to CRM.
     */
    suspend fun sendCallUpdate(
        callRequestId: String,
        callStatus: String?,
        callStartedAt: Long?,
        callDurationSeconds: Int?,
        // Новые поля (ЭТАП 2, опциональные)
        direction: ru.groupprofi.crmprofi.dialer.domain.CallDirection? = null,
        resolveMethod: ru.groupprofi.crmprofi.dialer.domain.ResolveMethod? = null,
        resolveReason: String? = null,
        reasonIfUnknown: String? = null,
        attemptsCount: Int? = null,
        actionSource: ru.groupprofi.crmprofi.dialer.domain.ActionSource? = null,
        endedAt: Long? = null
    ): Result<Unit> = withContext(Dispatchers.IO) {
        // LOCAL_ONLY режим: не отправляем в CRM, только логируем
        if (!AppFeatures.isCrmEnabled()) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.i(
                "ApiClient",
                "sendCallUpdate: LOCAL_ONLY mode, call saved locally (id=$callRequestId, status=$callStatus, source=${actionSource?.apiValue})"
            )
            // Возвращаем Success, чтобы UI не зависел от сети
            // sentToCrm будет false в CallHistoryItem
            return@withContext Result.Success(Unit)
        }
        
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Error("Токен отсутствует")
        }
        
        try {
            val url = "$baseUrl/api/phone/calls/update/"
            
            // ЭТАП 2: Создаём CallEventPayload и выбираем legacy или extended формат
            val payload = CallEventPayload(
                callRequestId = callRequestId,
                callStatus = callStatus,
                callStartedAt = callStartedAt,
                callDurationSeconds = callDurationSeconds,
                callEndedAt = endedAt,
                direction = direction?.apiValue,
                resolveMethod = resolveMethod?.apiValue,
                resolveReason = resolveReason,
                reasonIfUnknown = reasonIfUnknown,
                attemptsCount = attemptsCount,
                actionSource = actionSource?.apiValue
            )
            
            // Логика: если есть хотя бы одно новое поле - отправляем extended, иначе legacy
            val hasNewFields = direction != null || resolveMethod != null || resolveReason != null ||
                    reasonIfUnknown != null || attemptsCount != null || actionSource != null || endedAt != null
            val bodyJson = if (hasNewFields) {
                payload.toExtendedJson()
            } else {
                payload.toLegacyJson()
            }
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                if (res.isSuccessful) {
                    // Обновляем статус отправки в истории (асинхронно, не блокируем ответ)
                    scope.launch {
                        try {
                            callHistoryStore.markSent(callRequestId, System.currentTimeMillis())
                        } catch (_: Exception) {
                            // Игнорируем ошибки обновления истории (не критично)
                        }
                    }
                    return@withContext Result.Success(Unit)
                }
                
                // Fallback: сервер не принимает extended payload (строгие 400/415/422)
                if (hasNewFields && res.code in setOf(400, 415, 422)) {
                    val legacyPayload = CallEventPayload(
                        callRequestId = callRequestId,
                        callStatus = callStatus,
                        callStartedAt = callStartedAt,
                        callDurationSeconds = callDurationSeconds
                    )
                    val legacyJson = legacyPayload.toLegacyJson()
                    res.close()
                    
                    val legacyReq = Request.Builder()
                        .url(url)
                        .post(legacyJson.toRequestBody(jsonMedia))
                        .addHeader("Authorization", "Bearer $token")
                        .build()
                    
                    httpClient.newCall(legacyReq).execute().use { legacyRes ->
                        if (legacyRes.isSuccessful) {
                            scope.launch {
                                try {
                                    callHistoryStore.markSent(callRequestId, System.currentTimeMillis())
                                } catch (_: Exception) {
                                }
                            }
                            return@withContext Result.Success(Unit)
                        } else {
                            return@withContext Result.Error(
                                "Ошибка отправки (legacy): HTTP ${legacyRes.code}",
                                legacyRes.code
                            )
                        }
                    }
                }
                
                // При ошибке сервера (5xx) - добавляем в очередь для повторной отправки
                if (res.code in 500..599) {
                    queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
                }
                Result.Error("Ошибка отправки: HTTP ${res.code}", res.code)
            }
        } catch (e: UnknownHostException) {
            // Нет интернета - добавляем в очередь
            val payload = CallEventPayload(
                callRequestId = callRequestId,
                callStatus = callStatus,
                callStartedAt = callStartedAt,
                callDurationSeconds = callDurationSeconds,
                callEndedAt = endedAt,
                direction = direction?.apiValue,
                resolveMethod = resolveMethod?.apiValue,
                resolveReason = resolveReason,
                reasonIfUnknown = reasonIfUnknown,
                attemptsCount = attemptsCount,
                actionSource = actionSource?.apiValue
            )
            val hasNewFields = direction != null || resolveMethod != null || resolveReason != null ||
                    reasonIfUnknown != null || attemptsCount != null || actionSource != null || endedAt != null
            val bodyJson = if (hasNewFields) {
                payload.toExtendedJson()
            } else {
                payload.toLegacyJson()
            }
            queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            Result.Error("Нет подключения к интернету", 0)
        } catch (e: SocketTimeoutException) {
            val payload = CallEventPayload(
                callRequestId = callRequestId,
                callStatus = callStatus,
                callStartedAt = callStartedAt,
                callDurationSeconds = callDurationSeconds,
                callEndedAt = endedAt,
                direction = direction?.apiValue,
                resolveMethod = resolveMethod?.apiValue,
                resolveReason = resolveReason,
                reasonIfUnknown = reasonIfUnknown,
                attemptsCount = attemptsCount,
                actionSource = actionSource?.apiValue
            )
            val hasNewFields = direction != null || resolveMethod != null || resolveReason != null ||
                    reasonIfUnknown != null || attemptsCount != null || actionSource != null || endedAt != null
            val bodyJson = if (hasNewFields) {
                payload.toExtendedJson()
            } else {
                payload.toLegacyJson()
            }
            queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            Result.Error("Превышено время ожидания ответа", 0)
        } catch (e: IOException) {
            val payload = CallEventPayload(
                callRequestId = callRequestId,
                callStatus = callStatus,
                callStartedAt = callStartedAt,
                callDurationSeconds = callDurationSeconds,
                callEndedAt = endedAt,
                direction = direction?.apiValue,
                resolveMethod = resolveMethod?.apiValue,
                resolveReason = resolveReason,
                reasonIfUnknown = reasonIfUnknown,
                attemptsCount = attemptsCount,
                actionSource = actionSource?.apiValue
            )
            val hasNewFields = direction != null || resolveMethod != null || resolveReason != null ||
                    reasonIfUnknown != null || attemptsCount != null || actionSource != null || endedAt != null
            val bodyJson = if (hasNewFields) {
                payload.toExtendedJson()
            } else {
                payload.toLegacyJson()
            }
            queueManager.enqueue("call_update", "/api/phone/calls/update/", bodyJson)
            Result.Error("Ошибка сети: ${e.message}", 0)
        } catch (e: Exception) {
            Result.Error("Ошибка: ${e.message}")
        }
    }
    
    /**
     * Отправить heartbeat.
     * 
     * В режиме LOCAL_ONLY: НЕ отправляет heartbeat в CRM.
     * В режиме FULL: отправляет как обычно.
     */
    suspend fun sendHeartbeat(
        deviceId: String,
        deviceName: String,
        appVersion: String,
        lastPollCode: Int?,
        lastPollAt: Long?,
        encryptionEnabled: Boolean,
        stuckMetrics: QueueManager.StuckMetrics?
    ): Result<Unit> = withContext(Dispatchers.IO) {
        // LOCAL_ONLY режим: не отправляем heartbeat
        if (!AppFeatures.isCrmEnabled()) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("ApiClient", "sendHeartbeat: LOCAL_ONLY mode, skipping")
            return@withContext Result.Success(Unit)
        }
        
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Success(Unit) // Heartbeat не критичен
        }
        
        try {
            val url = "$baseUrl/api/phone/devices/heartbeat/"
            val iso = SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
                timeZone = TimeZone.getTimeZone("UTC")
            }.format(Date(lastPollAt ?: System.currentTimeMillis()))
            
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("device_name", deviceName)
                put("app_version", appVersion)
                if (lastPollCode != null) put("last_poll_code", lastPollCode)
                put("last_poll_at", iso)
                put("encryption_enabled", encryptionEnabled)
                
                if (stuckMetrics != null) {
                    put("queue_stuck", true)
                    put("stuck_count", stuckMetrics.stuckCount)
                    put("oldest_stuck_age_sec", stuckMetrics.oldestStuckAgeSec)
                    val stuckByTypeJson = JSONObject()
                    stuckMetrics.stuckByType.forEach { (type, count) ->
                        stuckByTypeJson.put(type, count)
                    }
                    put("stuck_by_type", stuckByTypeJson)
                }
            }.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    if (res.code in 500..599) {
                        queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
                    }
                }
                Result.Success(Unit)
            }
        } catch (e: UnknownHostException) {
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("device_name", deviceName)
                put("app_version", appVersion)
                if (lastPollCode != null) put("last_poll_code", lastPollCode)
                put("last_poll_at", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss'Z'", Locale.US).apply {
                    timeZone = TimeZone.getTimeZone("UTC")
                }.format(Date(lastPollAt ?: System.currentTimeMillis())))
                put("encryption_enabled", encryptionEnabled)
            }.toString()
            queueManager.enqueue("heartbeat", "/api/phone/devices/heartbeat/", bodyJson)
            Result.Success(Unit)
        } catch (e: Exception) {
            Result.Success(Unit) // Heartbeat не критичен
        }
    }
    
    /**
     * Отправить батч телеметрии.
     * 
     * В режиме LOCAL_ONLY: НЕ отправляет телеметрию в CRM.
     * В режиме FULL: отправляет как обычно.
     */
    suspend fun sendTelemetryBatch(deviceId: String, items: List<TelemetryItem>): Result<Unit> = withContext(Dispatchers.IO) {
        // LOCAL_ONLY режим: не отправляем телеметрию
        if (!AppFeatures.isCrmEnabled()) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.d(
                "ApiClient",
                "sendTelemetryBatch: LOCAL_ONLY mode, skipping telemetry (${items.size} items)"
            )
            return@withContext Result.Success(Unit)
        }
        
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Success(Unit) // Telemetry не критична
        }
        
        try {
            val url = "$baseUrl/api/phone/telemetry/"
            val itemsArray = JSONArray()
            items.forEach { item ->
                itemsArray.put(JSONObject().apply {
                    if (item.ts != null) put("ts", item.ts)
                    if (item.type != null) put("type", item.type)
                    if (item.endpoint != null) put("endpoint", item.endpoint)
                    if (item.httpCode != null) put("http_code", item.httpCode)
                    if (item.valueMs != null) put("value_ms", item.valueMs)
                    if (item.extra != null) put("extra", JSONObject(item.extra))
                })
            }
            
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("items", itemsArray)
            }.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    // При 429 не добавляем телеметрию в очередь - это создаст лавину запросов
                    // Телеметрия не критична, просто пропускаем при rate limiting
                    if (res.code == 429) {
                        // Логируем но не добавляем в очередь
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.d("ApiClient", "Telemetry batch rate-limited (429), skipping")
                        return@use Result.Success(Unit)
                    }
                    if (res.code in 500..599) {
                        queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
                    }
                }
                Result.Success(Unit)
            }
        } catch (e: UnknownHostException) {
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("items", JSONArray())
            }.toString()
            queueManager.enqueue("telemetry", "/api/phone/telemetry/", bodyJson)
            Result.Success(Unit)
        } catch (e: Exception) {
            Result.Success(Unit) // Telemetry не критична
        }
    }
    
    /**
     * Отправить лог-бандл.
     */
    suspend fun sendLogBundle(
        deviceId: String,
        ts: String,
        levelSummary: String,
        source: String,
        payload: String
    ): Result<Unit> = withContext(Dispatchers.IO) {
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Success(Unit) // Logs не критичны
        }
        
        try {
            val url = "$baseUrl/api/phone/logs/"
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("ts", ts)
                put("level_summary", levelSummary)
                put("source", source)
                put("payload", payload)
            }.toString()
            
            val req = Request.Builder()
                .url(url)
                .post(bodyJson.toRequestBody(jsonMedia))
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                if (!res.isSuccessful) {
                    if (res.code in 500..599) {
                        queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
                    }
                }
                Result.Success(Unit)
            }
        } catch (e: UnknownHostException) {
            val bodyJson = JSONObject().apply {
                put("device_id", deviceId)
                put("ts", ts)
                put("level_summary", levelSummary)
                put("source", source)
                put("payload", payload)
            }.toString()
            queueManager.enqueue("log_bundle", "/api/phone/logs/", bodyJson)
            Result.Success(Unit)
        } catch (e: Exception) {
            Result.Success(Unit) // Logs не критичны
        }
    }
    
    /**
     * Получить информацию о текущем пользователе (включая роль).
     * Используется для проверки прав доступа (например, для логов администратора).
     */
    suspend fun getUserInfo(): Result<UserInfo> = withContext(Dispatchers.IO) {
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return@withContext Result.Error("Токен отсутствует", 401)
        }
        
        try {
            val url = "$baseUrl/api/phone/user/info/"
            val req = Request.Builder()
                .url(url)
                .get()
                .addHeader("Authorization", "Bearer $token")
                .build()
            
            httpClient.newCall(req).execute().use { res ->
                val raw = res.body?.string() ?: ""
                if (!res.isSuccessful) {
                    val errorMsg = try {
                        val errorObj = JSONObject(raw)
                        errorObj.optString("detail", "Ошибка получения информации о пользователе")
                    } catch (_: Exception) {
                        "Ошибка: HTTP ${res.code}"
                    }
                    return@withContext Result.Error(errorMsg, res.code)
                }
                
                val obj = JSONObject(raw)
                val username = obj.optString("username", "")
                val isAdmin = obj.optBoolean("is_admin", false)
                
                Result.Success(UserInfo(username = username, isAdmin = isAdmin))
            }
        } catch (e: UnknownHostException) {
            Result.Error("Нет подключения к интернету")
        } catch (e: SocketTimeoutException) {
            Result.Error("Превышено время ожидания ответа")
        } catch (e: Exception) {
            Result.Error("Ошибка сети: ${e.message}")
        }
    }
    
    /**
     * Информация о пользователе.
     */
    data class UserInfo(
        val username: String,
        val isAdmin: Boolean
    )
    
    /**
     * Результат обмена QR-токена.
     */
    data class QrTokenResult(
        val access: String,
        val refresh: String,
        val username: String,
        val isAdmin: Boolean
    )
    
    /**
     * Получить OkHttpClient (для использования в других местах, если нужно).
     */
    fun getHttpClient(): OkHttpClient = httpClient
    
    /**
     * Ответ на pullCall.
     */
    data class PullCallResponse(
        val phone: String,
        val callRequestId: String
    )
    
    /**
     * Элемент телеметрии.
     */
    data class TelemetryItem(
        val ts: String? = null,
        val type: String? = null,
        val endpoint: String? = null,
        val httpCode: Int? = null,
        val valueMs: Int? = null,
        val extra: Map<String, Any>? = null
    )
}
