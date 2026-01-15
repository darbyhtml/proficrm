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
        
        // Обрабатываем 401/403 для graceful logout
        if ((response.code == 401 || response.code == 403) && context != null) {
            Log.w("AuthInterceptor", "Received ${response.code}, clearing tokens and stopping service")
            // Очищаем токены и останавливаем сервис в фоне
            CoroutineScope(Dispatchers.IO).launch {
                tokenManager.clearAll()
                try {
                    context.stopService(Intent(context, CallListenerService::class.java))
                } catch (e: Exception) {
                    Log.e("AuthInterceptor", "Failed to stop service: ${e.message}")
                }
            }
        }
        
        return response
    }
}
