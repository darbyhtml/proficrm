package ru.groupprofi.crmprofi.dialer.notifications

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import ru.groupprofi.crmprofi.dialer.MainActivity
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.ui.onboarding.OnboardingActivity
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.logs.AppLogger

/**
 * Менеджер уведомлений приложения.
 * Управляет только двумя типами пользовательских уведомлений:
 * 1. "Пора позвонить" - команда на звонок
 * 2. "Приложение не работает" - проблемы с готовностью
 */
class AppNotificationManager private constructor(context: Context) {
    private val appContext = context.applicationContext
    private val notificationManager = appContext.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
    
    companion object {
        private const val CHANNEL_CALL_TASK = "crmprofi_call_task"
        private const val CHANNEL_APP_STATE = "crmprofi_app_state"
        
        private const val NOTIF_ID_CALL = 1001
        private const val NOTIF_ID_APP_STATE = 1002
        
        @Volatile
        private var INSTANCE: AppNotificationManager? = null
        
        fun getInstance(context: Context): AppNotificationManager {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: AppNotificationManager(context.applicationContext).also { INSTANCE = it }
            }
        }
    }
    
    init {
        createChannels()
    }
    
    /**
     * Создать каналы уведомлений.
     */
    private fun createChannels() {
        if (Build.VERSION.SDK_INT < 26) return
        
        // Канал для заданий на звонок
        val callChannel = NotificationChannel(
            CHANNEL_CALL_TASK,
            "Задание на звонок",
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = "Уведомления о командах на звонок из CRM"
            enableVibration(true)
            enableLights(true)
        }
        notificationManager.createNotificationChannel(callChannel)
        
        // Канал для статуса приложения
        val stateChannel = NotificationChannel(
            CHANNEL_APP_STATE,
            "Статус приложения",
            NotificationManager.IMPORTANCE_DEFAULT
        ).apply {
            description = "Уведомления о проблемах с работой приложения"
            enableVibration(false)
            enableLights(false)
        }
        notificationManager.createNotificationChannel(stateChannel)
    }
    
    /**
     * Показать уведомление "Пора позвонить".
     */
    fun showCallTaskNotification(phone: String) {
        try {
            val uri = Uri.parse("tel:$phone")
            val dialIntent = Intent(Intent.ACTION_DIAL, uri).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            
            val dialPendingIntent = PendingIntent.getActivity(
                appContext,
                NOTIF_ID_CALL,
                dialIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
            )
            
            // Маскируем номер для логов
            val maskedPhone = maskPhone(phone)
            
            val notification = NotificationCompat.Builder(appContext, CHANNEL_CALL_TASK)
                .setSmallIcon(android.R.drawable.sym_action_call)
                .setContentTitle(appContext.getString(R.string.notification_call_task_title))
                .setContentText(appContext.getString(R.string.notification_call_task_text, maskedPhone))
                .setStyle(NotificationCompat.BigTextStyle()
                    .bigText(appContext.getString(R.string.notification_call_task_text, maskedPhone)))
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setCategory(NotificationCompat.CATEGORY_CALL)
                .setAutoCancel(true)
                .setContentIntent(dialPendingIntent)
                .addAction(
                    android.R.drawable.sym_action_call,
                    appContext.getString(R.string.notification_action_call),
                    dialPendingIntent
                )
                .setShowWhen(true)
                .setWhen(System.currentTimeMillis())
                .build()
            
            notificationManager.notify(NOTIF_ID_CALL, notification)
            AppLogger.i("AppNotificationManager", "Показано уведомление 'Пора позвонить': $maskedPhone")
        } catch (e: Exception) {
            AppLogger.e("AppNotificationManager", "Ошибка показа уведомления 'Пора позвонить': ${e.message}", e)
        }
    }
    
    /**
     * Скрыть уведомление "Пора позвонить".
     */
    fun dismissCallTaskNotification() {
        try {
            notificationManager.cancel(NOTIF_ID_CALL)
            AppLogger.d("AppNotificationManager", "Скрыто уведомление 'Пора позвонить'")
        } catch (e: Exception) {
            AppLogger.e("AppNotificationManager", "Ошибка скрытия уведомления: ${e.message}", e)
        }
    }
    
    /**
     * Показать уведомление "Приложение не работает".
     */
    fun showAppStateNotification(state: AppReadinessChecker.ReadyState) {
        try {
            val (_, message, fixAction) = getAppStateNotificationContent(state)
            
            val fixIntent = when (fixAction) {
                AppReadinessChecker.FixActionType.REQUEST_PERMISSIONS -> {
                    Intent(appContext, OnboardingActivity::class.java).apply {
                        putExtra(OnboardingActivity.EXTRA_START_STEP, "PERMISSIONS")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
                AppReadinessChecker.FixActionType.OPEN_NOTIFICATION_SETTINGS -> {
                    Intent(appContext, OnboardingActivity::class.java).apply {
                        putExtra(OnboardingActivity.EXTRA_START_STEP, "NOTIFICATIONS")
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
                AppReadinessChecker.FixActionType.SHOW_LOGIN -> {
                    Intent(appContext, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
                AppReadinessChecker.FixActionType.OPEN_NETWORK_SETTINGS -> {
                    Intent(android.provider.Settings.ACTION_WIRELESS_SETTINGS).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
                AppReadinessChecker.FixActionType.RESTART_SERVICE -> {
                    Intent(appContext, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
                AppReadinessChecker.FixActionType.NONE -> {
                    Intent(appContext, MainActivity::class.java).apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                    }
                }
            }
            
            val fixPendingIntent = PendingIntent.getActivity(
                appContext,
                NOTIF_ID_APP_STATE,
                fixIntent,
                PendingIntent.FLAG_UPDATE_CURRENT or (if (Build.VERSION.SDK_INT >= 23) PendingIntent.FLAG_IMMUTABLE else 0)
            )
            
            val notification = NotificationCompat.Builder(appContext, CHANNEL_APP_STATE)
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setContentTitle(appContext.getString(R.string.notification_app_state_title))
                .setContentText(message)
                .setStyle(NotificationCompat.BigTextStyle().bigText(message))
                .setPriority(NotificationCompat.PRIORITY_DEFAULT)
                .setCategory(NotificationCompat.CATEGORY_STATUS)
                .setAutoCancel(false)
                .setOngoing(true)
                .setContentIntent(fixPendingIntent)
                .addAction(
                    android.R.drawable.ic_menu_edit,
                    appContext.getString(R.string.notification_action_fix),
                    fixPendingIntent
                )
                .setShowWhen(true)
                .setWhen(System.currentTimeMillis())
                .build()
            
            notificationManager.notify(NOTIF_ID_APP_STATE, notification)
            AppLogger.i("AppNotificationManager", "Показано уведомление 'Приложение не работает': $state")
        } catch (e: Exception) {
            AppLogger.e("AppNotificationManager", "Ошибка показа уведомления 'Приложение не работает': ${e.message}", e)
        }
    }
    
    /**
     * Скрыть уведомление "Приложение не работает".
     */
    fun dismissAppStateNotification() {
        try {
            notificationManager.cancel(NOTIF_ID_APP_STATE)
            AppLogger.d("AppNotificationManager", "Скрыто уведомление 'Приложение не работает'")
        } catch (e: Exception) {
            AppLogger.e("AppNotificationManager", "Ошибка скрытия уведомления: ${e.message}", e)
        }
    }
    
    /**
     * Получить контент уведомления "Приложение не работает" на основе состояния.
     */
    private fun getAppStateNotificationContent(state: AppReadinessChecker.ReadyState): Triple<String, String, AppReadinessChecker.FixActionType> {
        return when (state) {
            AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS -> Triple(
                "Приложение не работает",
                "Нужны разрешения для работы. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.REQUEST_PERMISSIONS
            )
            AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS -> Triple(
                "Приложение не работает",
                "Нужно включить уведомления. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.OPEN_NOTIFICATION_SETTINGS
            )
            AppReadinessChecker.ReadyState.NEEDS_AUTH -> Triple(
                "Приложение не работает",
                "Нужно войти в систему. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.SHOW_LOGIN
            )
            AppReadinessChecker.ReadyState.NO_NETWORK -> Triple(
                "Приложение не работает",
                "Нет подключения к интернету. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.OPEN_NETWORK_SETTINGS
            )
            AppReadinessChecker.ReadyState.SERVICE_STOPPED -> Triple(
                "Приложение не работает",
                "Приложение остановлено. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.RESTART_SERVICE
            )
            else -> Triple(
                "Приложение не работает",
                "Произошла ошибка. Нажмите «Исправить» — мы поможем настроить.",
                AppReadinessChecker.FixActionType.NONE
            )
        }
    }
    
    /**
     * Маскировать номер телефона для логов.
     */
    private fun maskPhone(phone: String): String {
        if (phone.length <= 4) return "***"
        return "${phone.take(3)}***${phone.takeLast(4)}"
    }
}
