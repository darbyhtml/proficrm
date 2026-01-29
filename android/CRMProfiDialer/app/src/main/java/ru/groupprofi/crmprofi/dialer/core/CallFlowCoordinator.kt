package ru.groupprofi.crmprofi.dialer.core

import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.AppState
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.auth.TokenManager

/**
 * Координатор потока обработки команды на звонок.
 * Централизует логику: уведомление → создание PendingCall → запуск определения результата.
 * Само определение результата выполняется через CallLogObserverManager и повторные проверки в CallListenerService.
 */
class CallFlowCoordinator(
    private val context: Context,
    private val pendingCallStore: ru.groupprofi.crmprofi.dialer.domain.PendingCallStore,
    private val notificationManager: ru.groupprofi.crmprofi.dialer.notifications.AppNotificationManager
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    
    companion object {
        @Volatile
        private var INSTANCE: CallFlowCoordinator? = null
        
        fun getInstance(context: Context): CallFlowCoordinator {
            return INSTANCE ?: synchronized(this) {
                INSTANCE ?: CallFlowCoordinator(
                    context.applicationContext,
                    AppContainer.pendingCallStore,
                    AppContainer.notificationManager
                ).also { INSTANCE = it }
            }
        }
    }
    
    /**
     * Обработать команду на звонок.
     * Показывает уведомление, создаёт PendingCall, запускает определение результата.
     * ЭТАП 2: Добавлен actionSource = CRM_UI (команда из CRM через polling).
     */
    fun handleCallCommand(phone: String, callRequestId: String) {
        scope.launch {
            try {
                AppLogger.i("CallFlowCoordinator", "Обработка команды на звонок: ${maskPhone(phone)}, id=$callRequestId")
                
                // 1. Показываем уведомление "Пора позвонить"
                notificationManager.showCallTaskNotification(phone)
                
                // 2. Если приложение на экране - открываем звонилку сразу
                if (AppState.isForeground) {
                    openDialer(phone, callRequestId)
                    // Скрываем уведомление после открытия звонилки
                    notificationManager.dismissCallTaskNotification()
                } else {
                    // В фоне - открываем звонилку с задержкой
                    Handler(Looper.getMainLooper()).postDelayed({
                        openDialer(phone, callRequestId)
                        notificationManager.dismissCallTaskNotification()
                    }, 500)
                }
                
                // 3. Создаём ожидаемый звонок (определение результата запустится через CallLogObserverManager и повторные проверки)
                // ЭТАП 2: actionSource = CRM_UI (команда из CRM)
                startCallResolution(phone, callRequestId, ActionSource.CRM_UI)
                
            } catch (e: Exception) {
                AppLogger.e("CallFlowCoordinator", "Ошибка обработки команды на звонок: ${e.message}", e)
            }
        }
    }
    
    /**
     * Обработать команду на звонок из уведомления.
     * ЭТАП 2: actionSource = NOTIFICATION.
     */
    fun handleCallCommandFromNotification(phone: String, callRequestId: String) {
        scope.launch {
            try {
                AppLogger.i("CallFlowCoordinator", "Обработка команды на звонок из уведомления: ${maskPhone(phone)}, id=$callRequestId")
                
                // Открываем звонилку
                openDialer(phone, callRequestId)
                notificationManager.dismissCallTaskNotification()
                
                // Создаём ожидаемый звонок с actionSource = NOTIFICATION
                startCallResolution(phone, callRequestId, ActionSource.NOTIFICATION)
                
            } catch (e: Exception) {
                AppLogger.e("CallFlowCoordinator", "Ошибка обработки команды из уведомления: ${e.message}", e)
            }
        }
    }
    
    /**
     * Обработать команду на звонок из истории.
     * ЭТАП 2: actionSource = HISTORY.
     */
    fun handleCallCommandFromHistory(phone: String, callRequestId: String? = null) {
        scope.launch {
            try {
                AppLogger.i("CallFlowCoordinator", "Обработка команды на звонок из истории: ${maskPhone(phone)}")
                
                // Открываем звонилку
                openDialer(phone, callRequestId)
                
                // Если есть callRequestId (из истории звонка, который был отправлен в CRM) - создаём PendingCall
                // Если нет (ручной звонок) - просто открываем звонилку, не отслеживаем
                if (callRequestId != null) {
                    startCallResolution(phone, callRequestId, ActionSource.HISTORY)
                } else {
                    // Ручной звонок - не отслеживаем
                    AppLogger.d("CallFlowCoordinator", "Ручной звонок из истории, не отслеживаем")
                }
                
            } catch (e: Exception) {
                AppLogger.e("CallFlowCoordinator", "Ошибка обработки команды из истории: ${e.message}", e)
            }
        }
    }
    
    /**
     * Открыть системную звонилку.
     */
    private fun openDialer(phone: String, callRequestId: String?) {
        try {
            val uri = Uri.parse("tel:$phone")
            val dialIntent = Intent(Intent.ACTION_DIAL, uri).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            context.startActivity(dialIntent)
            val openedAt = System.currentTimeMillis()
            if (!callRequestId.isNullOrBlank()) {
                TokenManager.getInstance().saveLastDialerOpened(callRequestId, openedAt)
                val receivedAt = TokenManager.getInstance().getLastCallCommandReceivedAt()
                val delta = if (receivedAt != null) (openedAt - receivedAt).coerceAtLeast(0L) else null
                AppLogger.i("CallFlowCoordinator", "DIALER_OPENED id=$callRequestId deltaMs=${delta ?: -1}")
            } else {
                AppLogger.i("CallFlowCoordinator", "Звонилка открыта: ${maskPhone(phone)}")
            }
        } catch (e: Exception) {
            AppLogger.e("CallFlowCoordinator", "Ошибка открытия звонилки: ${e.message}", e)
        }
    }
    
    /**
     * Начать процесс определения результата звонка.
     * ЭТАП 2: Добавлен actionSource для отслеживания источника действия.
     */
    private suspend fun startCallResolution(phone: String, callRequestId: String, actionSource: ActionSource) {
        try {
            val normalizedPhone = PhoneNumberNormalizer.normalize(phone)
            val startedAt = System.currentTimeMillis()
            
            // Создаём ожидаемый звонок с actionSource
            val pendingCall = PendingCall(
                callRequestId = callRequestId,
                phoneNumber = normalizedPhone,
                startedAtMillis = startedAt,
                state = PendingCall.PendingState.PENDING,
                attempts = 0,
                actionSource = actionSource
            )
            
            pendingCallStore.addPendingCall(pendingCall)
            AppLogger.i("CallFlowCoordinator", "Начато определение результата звонка: ${maskPhone(phone)}, actionSource=$actionSource")
            
            // Повторные проверки CallLog выполняются в CallListenerService через CallLogObserverManager
            // CallFlowCoordinator только создаёт PendingCall, CallLogObserverManager сам найдёт результат
            
        } catch (e: Exception) {
            AppLogger.e("CallFlowCoordinator", "Ошибка при создании ожидаемого звонка: ${e.message}", e)
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
