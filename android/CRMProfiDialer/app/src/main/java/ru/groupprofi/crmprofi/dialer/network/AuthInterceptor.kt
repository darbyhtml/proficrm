package ru.groupprofi.crmprofi.dialer.network

import android.content.Context
import android.content.Intent
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import okhttp3.Interceptor
import okhttp3.Response
import ru.groupprofi.crmprofi.dialer.CallListenerService
import ru.groupprofi.crmprofi.dialer.auth.TokenManager

/**
 * Interceptor для автоматической подстановки Bearer токена.
 * НЕ подставляет токен для /api/token/ и /api/token/refresh/.
 * Обрабатывает 401/403 для graceful logout.
 */
class AuthInterceptor(
    private val tokenManager: TokenManager,
    private val context: Context? = null
) : Interceptor {
    
    override fun intercept(chain: Interceptor.Chain): Response {
        val original = chain.request()
        val url = original.url.toString()
        
        // Не добавляем токен для login, refresh и QR exchange endpoints
        if (url.contains("/api/token/") || url.contains("/api/phone/qr/exchange/")) {
            return chain.proceed(original)
        }
        
        // Получаем access token из TokenManager
        val token = tokenManager.getAccessToken()
        if (token.isNullOrBlank()) {
            return chain.proceed(original)
        }
        
        // Добавляем Bearer токен в заголовок
        val requestBuilder = original.newBuilder()
            .header("Authorization", "Bearer $token")
        
        val response = chain.proceed(requestBuilder.build())
        
        // НЕ очищаем токены в AuthInterceptor при 401/403 - это может быть временная ошибка.
        // Очистка токенов должна происходить только в refreshToken() когда refresh token реально истек.
        // AuthInterceptor только логирует для диагностики.
        if (response.code == 401 || response.code == 403) {
            ru.groupprofi.crmprofi.dialer.logs.AppLogger.w("AuthInterceptor", "Received ${response.code}, will try refresh token in ApiClient")
            // Не очищаем токены здесь - пусть ApiClient.refreshToken() решает, истек ли refresh token
        }
        
        return response
    }
}
