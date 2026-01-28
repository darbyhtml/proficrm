package ru.groupprofi.crmprofi.dialer.recovery

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.CallListenerService
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason
import ru.groupprofi.crmprofi.dialer.logs.AppLogger

/**
 * Автозапуск после перезагрузки.
 *
 * Важно:
 * - НЕ запускаем сервис без авторизации и device_id
 * - НЕ падаем при отсутствии разрешений/уведомлений
 * - Если запуск не выполнен — сохраняем причину (для UI/диагностики)
 */
class BootCompletedReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_BOOT_COMPLETED) return

        try {
            if (!AppContainer.isInitialized()) {
                AppContainer.init(context.applicationContext)
            }
        } catch (_: Exception) {
            // Даже если контейнер не поднялся — не падаем в receiver.
        }

        val tokenManager = TokenManager.getInstance(context)
        val deviceId = tokenManager.getDeviceId().orEmpty().trim()

        // 1) Авторизация / device_id
        if (!tokenManager.hasTokens()) {
            tokenManager.setServiceBlockReason(ServiceBlockReason.AUTH_MISSING)
            AppLogger.w("BootCompletedReceiver", "Skip autostart: AUTH_MISSING")
            return
        }
        if (deviceId.isBlank()) {
            tokenManager.setServiceBlockReason(ServiceBlockReason.DEVICE_ID_MISSING)
            AppLogger.w("BootCompletedReceiver", "Skip autostart: DEVICE_ID_MISSING")
            return
        }

        // 2) Разрешения (требование: не стартовать, если permissions не OK)
        val hasCallLog = ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.READ_CALL_LOG
        ) == PackageManager.PERMISSION_GRANTED
        val hasPhoneState = ContextCompat.checkSelfPermission(
            context, android.Manifest.permission.READ_PHONE_STATE
        ) == PackageManager.PERMISSION_GRANTED
        if (!hasCallLog || !hasPhoneState) {
            // Это не "ошибка", просто состояние — UI покажет NEEDS_PERMISSIONS
            AppLogger.i("BootCompletedReceiver", "Skip autostart: missing call permissions")
            return
        }

        // 3) Уведомления (Android < 13: без POST_NOTIFICATIONS; Android 13+: runtime check)
        val notificationsEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        if (!notificationsEnabled) {
            tokenManager.setServiceBlockReason(ServiceBlockReason.NOTIFICATIONS_DISABLED)
            AppLogger.w("BootCompletedReceiver", "Skip autostart: NOTIFICATIONS_DISABLED")
            return
        }
        if (Build.VERSION.SDK_INT >= 33) {
            val granted = ContextCompat.checkSelfPermission(
                context,
                android.Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
            if (!granted) {
                tokenManager.setServiceBlockReason(ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING)
                AppLogger.w("BootCompletedReceiver", "Skip autostart: NOTIFICATION_PERMISSION_MISSING")
                return
            }
        }

        // 4) Стартуем сервис (best-effort)
        val access = tokenManager.getAccessToken().orEmpty()
        val refresh = tokenManager.getRefreshToken().orEmpty()
        val svcIntent = Intent(context, CallListenerService::class.java).apply {
            action = CallListenerService.ACTION_START
            putExtra(CallListenerService.EXTRA_DEVICE_ID, deviceId)
            // extras оставляем для совместимости (сервис всё равно читает TokenManager)
            putExtra(CallListenerService.EXTRA_TOKEN, access)
            putExtra(CallListenerService.EXTRA_REFRESH, refresh)
        }

        try {
            if (Build.VERSION.SDK_INT >= 26) {
                ContextCompat.startForegroundService(context, svcIntent)
            } else {
                context.startService(svcIntent)
            }
            AppLogger.i("BootCompletedReceiver", "Autostart service: ok")
        } catch (t: Throwable) {
            tokenManager.setServiceBlockReason(ServiceBlockReason.FOREGROUND_START_FAILED)
            AppLogger.e("BootCompletedReceiver", "Autostart failed: ${t.message}", t)
        }
    }
}

