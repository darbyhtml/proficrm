package ru.groupprofi.crmprofi.dialer.recovery

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.CallListenerService
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.ServiceBlockReason
import ru.groupprofi.crmprofi.dialer.logs.AppLogger

/**
 * Автозапуск после перезагрузки.
 *
 * Важно:
 * - TokenManager/AppContainer инициализируются асинхронно (без disk I/O на main thread).
 * - НЕ запускаем сервис без авторизации и device_id
 * - НЕ падаем при отсутствии разрешений/уведомлений
 */
class BootCompletedReceiver : BroadcastReceiver() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    override fun onReceive(context: Context, intent: Intent?) {
        if (intent?.action != Intent.ACTION_BOOT_COMPLETED) return

        val pendingResult = goAsync()
        val appContext = context.applicationContext

        scope.launch {
            try {
                TokenManager.init(appContext)
                if (!AppContainer.isInitialized()) {
                    AppContainer.init(appContext)
                }
            } catch (e: Exception) {
                AppLogger.w("BootCompletedReceiver", "Init failed: ${e.message}")
                pendingResult.finish()
                return@launch
            }

            withContext(Dispatchers.Main) {
                try {
                    val tokenManager = TokenManager.getInstance()
                    val deviceId = tokenManager.getDeviceId().orEmpty().trim()

                    if (!tokenManager.hasTokens()) {
                        tokenManager.setServiceBlockReason(ServiceBlockReason.AUTH_MISSING)
                        AppLogger.w("BootCompletedReceiver", "Skip autostart: AUTH_MISSING")
                        pendingResult.finish()
                        return@withContext
                    }
                    if (deviceId.isBlank()) {
                        tokenManager.setServiceBlockReason(ServiceBlockReason.DEVICE_ID_MISSING)
                        AppLogger.w("BootCompletedReceiver", "Skip autostart: DEVICE_ID_MISSING")
                        pendingResult.finish()
                        return@withContext
                    }

                    val hasCallLog = ContextCompat.checkSelfPermission(
                        context, android.Manifest.permission.READ_CALL_LOG
                    ) == PackageManager.PERMISSION_GRANTED
                    val hasPhoneState = ContextCompat.checkSelfPermission(
                        context, android.Manifest.permission.READ_PHONE_STATE
                    ) == PackageManager.PERMISSION_GRANTED
                    if (!hasCallLog || !hasPhoneState) {
                        AppLogger.i("BootCompletedReceiver", "Skip autostart: missing call permissions")
                        pendingResult.finish()
                        return@withContext
                    }

                    val notificationsEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
                    if (!notificationsEnabled) {
                        tokenManager.setServiceBlockReason(ServiceBlockReason.NOTIFICATIONS_DISABLED)
                        AppLogger.w("BootCompletedReceiver", "Skip autostart: NOTIFICATIONS_DISABLED")
                        pendingResult.finish()
                        return@withContext
                    }
                    if (Build.VERSION.SDK_INT >= 33) {
                        val granted = ContextCompat.checkSelfPermission(
                            context,
                            android.Manifest.permission.POST_NOTIFICATIONS
                        ) == PackageManager.PERMISSION_GRANTED
                        if (!granted) {
                            tokenManager.setServiceBlockReason(ServiceBlockReason.NOTIFICATION_PERMISSION_MISSING)
                            AppLogger.w("BootCompletedReceiver", "Skip autostart: NOTIFICATION_PERMISSION_MISSING")
                            pendingResult.finish()
                            return@withContext
                        }
                    }

                    val access = tokenManager.getAccessToken().orEmpty()
                    val refresh = tokenManager.getRefreshToken().orEmpty()
                    val svcIntent = Intent(context, CallListenerService::class.java).apply {
                        action = CallListenerService.ACTION_START
                        putExtra(CallListenerService.EXTRA_DEVICE_ID, deviceId)
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
                } finally {
                    pendingResult.finish()
                }
            }
        }
    }
}
