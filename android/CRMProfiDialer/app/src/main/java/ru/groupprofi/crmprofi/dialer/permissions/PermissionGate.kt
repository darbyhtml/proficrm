package ru.groupprofi.crmprofi.dialer.permissions

import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * Единая проверка разрешений для всех функций приложения.
 * Обеспечивает graceful degradation при отсутствии разрешений.
 */
object PermissionGate {
    
    /**
     * Результат проверки разрешений для конкретной функции.
     */
    data class PermissionStatus(
        val isGranted: Boolean,
        val missingPermissions: List<String>,
        val canRequest: Boolean, // Можно ли запросить разрешение (не запрещено навсегда)
        val userMessage: String // Сообщение для пользователя
    )
    
    /**
     * Проверить разрешения для ручных звонков.
     * Требует: CALL_PHONE (неявно через Intent.ACTION_CALL/DIAL)
     */
    fun checkManualCall(@Suppress("UNUSED_PARAMETER") context: Context): PermissionStatus {
        // CALL_PHONE не требуется для Intent.ACTION_DIAL, но может быть нужен для ACTION_CALL
        // В нашем случае используем ACTION_DIAL, поэтому всегда OK
        return PermissionStatus(
            isGranted = true,
            missingPermissions = emptyList(),
            canRequest = true,
            userMessage = "Готово к ручным звонкам"
        )
    }
    
    /**
     * Проверить разрешения для отслеживания результата через CallLogObserver.
     * Требует: READ_CALL_LOG, READ_PHONE_STATE
     */
    fun checkCallLogTracking(context: Context): PermissionStatus {
        val missing = mutableListOf<String>()
        val canRequest = mutableListOf<String>()
        
        val callLogPerm = android.Manifest.permission.READ_CALL_LOG
        val phoneStatePerm = android.Manifest.permission.READ_PHONE_STATE
        
        val hasCallLog = ContextCompat.checkSelfPermission(context, callLogPerm) == PackageManager.PERMISSION_GRANTED
        val hasPhoneState = ContextCompat.checkSelfPermission(context, phoneStatePerm) == PackageManager.PERMISSION_GRANTED
        
        if (!hasCallLog) {
            missing.add("READ_CALL_LOG")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val canRequestCallLog = if (context is android.app.Activity) {
                    android.app.Activity::class.java.getMethod("shouldShowRequestPermissionRationale", String::class.java)
                        .invoke(context, callLogPerm) as? Boolean ?: true
                } else {
                    true
                }
                if (canRequestCallLog) {
                    canRequest.add("READ_CALL_LOG")
                }
            } else {
                canRequest.add("READ_CALL_LOG")
            }
        }
        
        if (!hasPhoneState) {
            missing.add("READ_PHONE_STATE")
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val canRequestPhoneState = if (context is android.app.Activity) {
                    android.app.Activity::class.java.getMethod("shouldShowRequestPermissionRationale", String::class.java)
                        .invoke(context, phoneStatePerm) as? Boolean ?: true
                } else {
                    true
                }
                if (canRequestPhoneState) {
                    canRequest.add("READ_PHONE_STATE")
                }
            } else {
                canRequest.add("READ_PHONE_STATE")
            }
        }
        
        val userMessage = when {
            missing.isEmpty() -> "Разрешения для отслеживания звонков выданы"
            missing.size == 2 -> "Нужны разрешения: READ_CALL_LOG и READ_PHONE_STATE"
            missing.contains("READ_CALL_LOG") -> "Нужно разрешение: READ_CALL_LOG (для определения результата звонка)"
            else -> "Нужно разрешение: READ_PHONE_STATE"
        }
        
        return PermissionStatus(
            isGranted = missing.isEmpty(),
            missingPermissions = missing,
            canRequest = canRequest.isNotEmpty(),
            userMessage = userMessage
        )
    }
    
    /**
     * Проверить разрешения для foreground уведомлений.
     * Требует: POST_NOTIFICATIONS (Android 13+)
     */
    fun checkForegroundNotification(context: Context): PermissionStatus {
        if (Build.VERSION.SDK_INT < 33) {
            // Android 12 и ниже - уведомления работают без разрешения
            val enabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
            return PermissionStatus(
                isGranted = enabled,
                missingPermissions = if (enabled) emptyList() else listOf("NOTIFICATIONS_ENABLED"),
                canRequest = true,
                userMessage = if (enabled) "Уведомления включены" else "Уведомления выключены в настройках"
            )
        }
        
        val notifPerm = android.Manifest.permission.POST_NOTIFICATIONS
        val hasNotifPerm = ContextCompat.checkSelfPermission(context, notifPerm) == PackageManager.PERMISSION_GRANTED
        val enabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        
        val isGranted = hasNotifPerm && enabled
        
        val missing = mutableListOf<String>()
        if (!hasNotifPerm) missing.add("POST_NOTIFICATIONS")
        if (!enabled) missing.add("NOTIFICATIONS_ENABLED")
        
        val canRequest = if (!hasNotifPerm && context is android.app.Activity) {
            try {
                android.app.Activity::class.java.getMethod("shouldShowRequestPermissionRationale", String::class.java)
                    .invoke(context, notifPerm) as? Boolean ?: true
            } catch (e: Exception) {
                true
            }
        } else {
            true
        }
        
        val userMessage = when {
            isGranted -> "Уведомления разрешены"
            !hasNotifPerm && !enabled -> "Нужно разрешение POST_NOTIFICATIONS и включить уведомления в настройках"
            !hasNotifPerm -> "Нужно разрешение POST_NOTIFICATIONS (Android 13+)"
            else -> "Уведомления выключены в настройках"
        }
        
        return PermissionStatus(
            isGranted = isGranted,
            missingPermissions = missing,
            canRequest = canRequest,
            userMessage = userMessage
        )
    }
    
    /**
     * Проверить все разрешения для полноценной работы приложения.
     */
    fun checkFullReadiness(context: Context): FullReadinessStatus {
        val callLogTracking = checkCallLogTracking(context)
        val foregroundNotification = checkForegroundNotification(context)
        
        val allGranted = callLogTracking.isGranted && foregroundNotification.isGranted
        
        return FullReadinessStatus(
            isFullyReady = allGranted,
            callLogTracking = callLogTracking,
            foregroundNotification = foregroundNotification,
            canDegrade = callLogTracking.isGranted || foregroundNotification.isGranted // Может работать частично
        )
    }
    
    /**
     * Полный статус готовности приложения.
     */
    data class FullReadinessStatus(
        val isFullyReady: Boolean,
        val callLogTracking: PermissionStatus,
        val foregroundNotification: PermissionStatus,
        val canDegrade: Boolean // Может работать в режиме деградации
    )
    
    /**
     * Получить список всех необходимых разрешений для запроса.
     */
    fun getAllNeededPermissions(context: Context): List<String> {
        val needed = mutableListOf<String>()
        
        val callLogTracking = checkCallLogTracking(context)
        if (!callLogTracking.isGranted) {
            needed.addAll(callLogTracking.missingPermissions)
        }
        
        val foregroundNotification = checkForegroundNotification(context)
        if (!foregroundNotification.isGranted && foregroundNotification.missingPermissions.contains("POST_NOTIFICATIONS")) {
            needed.add(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        
        return needed.distinct()
    }
}
