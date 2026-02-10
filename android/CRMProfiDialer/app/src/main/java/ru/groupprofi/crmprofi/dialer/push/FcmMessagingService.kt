package ru.groupprofi.crmprofi.dialer.push

import android.content.Context
import android.os.Build
import android.util.Log
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.config.AppFeatures
import ru.groupprofi.crmprofi.dialer.network.ApiClient

/**
 * Firebase Cloud Messaging Service для ускорения доставки команд.
 * 
 * ВАЖНО: Это только ускоритель - основная доставка остается через long-polling.
 * Push используется только для "пробуждения" pullCall цикла, когда команда появилась в CRM.
 * 
 * ТРЕБОВАНИЯ:
 * - Firebase Cloud Messaging должен быть настроен в проекте
 * - AppFeatures.ENABLE_FCM_ACCELERATOR должен быть true
 * - google-services.json должен быть добавлен в проект
 * - FcmMessagingService должен быть зарегистрирован в AndroidManifest.xml (только если Firebase настроен)
 * 
 * ЗАЩИТА ОТ КОМПИЛЯЦИИ БЕЗ FIREBASE:
 * - Этот файл компилируется всегда, но класс НЕ наследуется от FirebaseMessagingService напрямую
 * - Если Firebase SDK не подключен - класс просто не будет использоваться
 * - AndroidManifest.xml НЕ должен содержать регистрацию этого сервиса, если Firebase не настроен
 * - Все обращения к Firebase SDK защищены через try-catch и проверку feature flag
 * 
 * ПРИМЕЧАНИЕ: Реальная реализация FirebaseMessagingService должна быть в отдельном модуле
 * или условно компилироваться только при наличии Firebase SDK.
 */
object FcmMessagingService {
    private const val TAG = "FcmMessagingService"
    private const val PUSH_TYPE_CALL_COMMAND = "CALL_COMMAND_AVAILABLE"
    private val scope = CoroutineScope(Dispatchers.IO)
    
    /**
     * Обработать полученное push-сообщение.
     * Вызывается из реального FirebaseMessagingService (если настроен) через PullCallCoordinator.
     * 
     * ВАЖНО: Этот метод вызывается только если:
     * 1. Firebase SDK подключен
     * 2. AppFeatures.ENABLE_FCM_ACCELERATOR = true
     * 3. Сервис зарегистрирован в AndroidManifest.xml
     */
    fun handlePushMessage(pushType: String?, data: Map<String, String>?) {
        // Проверяем feature flag
        if (!AppFeatures.isFcmAcceleratorEnabled()) {
            Log.d(TAG, "FCM accelerator disabled, ignoring push")
            return
        }
        
        when (pushType) {
            PUSH_TYPE_CALL_COMMAND -> {
                Log.i(TAG, "Received CALL_COMMAND_AVAILABLE push, waking pullCall cycle, dataKeys=${data?.keys}")
                
                // НЕ выполняем тяжелую работу в onMessageReceived
                // Только триггерим wakeNow через PullCallCoordinator
                PullCallCoordinator.wakeNow(reason = "PUSH")
            }
            else -> {
                Log.d(TAG, "Unknown push type: $pushType, dataKeys=${data?.keys}")
            }
        }
    }
    
    /**
     * Обработать обновление FCM токена.
     * Вызывается из реального FirebaseMessagingService (если настроен).
     */
    fun handleNewToken(context: Context, token: String) {
        Log.i(TAG, "FCM token refreshed: ${token.take(20)}...")

        // Если accelerator выключен — просто логируем токен и выходим.
        if (!AppFeatures.isFcmAcceleratorEnabled()) {
            Log.d(TAG, "FCM accelerator disabled, skipping token registration")
            return
        }

        val tm = try {
            TokenManager.getInstanceOrNull()
        } catch (e: IllegalStateException) {
            null
        }

        val deviceId = tm?.getDeviceId()?.trim().orEmpty()
        if (deviceId.isBlank()) {
            Log.w(TAG, "handleNewToken: deviceId is empty, cannot register FCM token yet")
            return
        }

        val appContext = context.applicationContext
        val deviceName = Build.MODEL ?: "Android"
        val apiClient = ApiClient.getInstance(appContext)

        scope.launch {
            try {
                val result = apiClient.registerDevice(deviceId, deviceName, token)
                if (result is ApiClient.Result.Error) {
                    Log.w(TAG, "Failed to register FCM token on server: code=${result.code}, msg=${result.message}")
                } else {
                    Log.i(TAG, "FCM token registered on server for deviceId=${deviceId.takeLast(4)}")
                }
            } catch (e: Exception) {
                Log.w(TAG, "Exception while registering FCM token: ${e.message}")
            }
        }
    }
}

/**
 * Координатор для управления pullCall циклом из внешних источников (push, UI, etc).
 * Позволяет "пробудить" pullCall цикл немедленно, минуя обычные задержки.
 */
object PullCallCoordinator {
    private var wakeCallback: ((String) -> Unit)? = null
    
    /**
     * Установить callback для пробуждения pullCall цикла.
     * Вызывается из CallListenerService при старте.
     */
    fun setWakeCallback(callback: (String) -> Unit) {
        wakeCallback = callback
    }
    
    /**
     * Пробудить pullCall цикл немедленно.
     * Отменяет текущую задержку/backoff и переходит в BURST режим на короткий период.
     * 
     * @param reason причина пробуждения ("PUSH", "USER_ACTION", "NETWORK_RESTORED", etc)
     */
    fun wakeNow(reason: String) {
        Log.i("PullCallCoordinator", "WakeNow called: reason=$reason")
        wakeCallback?.invoke(reason)
    }
}

/**
 * ВАЖНО: Push "сам по себе" не заработает от одного только флага.
 * 
 * FcmMessagingService — это object, он НЕ наследуется от FirebaseMessagingService.
 * Чтобы push заработал, нужна отдельная реализация (impl), которая:
 * - наследуется от FirebaseMessagingService,
 * - в onMessageReceived вызывает FcmMessagingService.handlePushMessage(...),
 * - зарегистрирована в AndroidManifest.xml (только когда Firebase подключен).
 * 
 * Итого: "включить флаг" (ENABLE_FCM_ACCELERATOR = true) недостаточно — нужен "impl"
 * (FcmMessagingServiceImpl или аналог) + manifest + google-services.json.
 * 
 * ---
 * 
 * Если вы хотите подключить Firebase SDK:
 * 
 * 1. Добавьте зависимость в build.gradle:
 *    implementation 'com.google.firebase:firebase-messaging:23.x.x'
 * 
 * 2. Добавьте google-services.json в app/
 * 
 * 3. Создайте реальный FirebaseMessagingService в отдельном файле:
 * 
 *    package ru.groupprofi.crmprofi.dialer.push
 *    
 *    import com.google.firebase.messaging.FirebaseMessagingService
 *    import com.google.firebase.messaging.RemoteMessage
 *    
 *    class FcmMessagingServiceImpl : FirebaseMessagingService() {
 *        override fun onMessageReceived(remoteMessage: RemoteMessage) {
 *            val pushType = remoteMessage.data["type"]
 *            FcmMessagingService.handlePushMessage(pushType, remoteMessage.data)
 *        }
 *        
 *        override fun onNewToken(token: String) {
 *            FcmMessagingService.handleNewToken(applicationContext, token)
 *        }
 *    }
 * 
 * 4. Зарегистрируйте в AndroidManifest.xml (только если Firebase настроен):
 *    <service
 *        android:name=".push.FcmMessagingServiceImpl"
 *        android:exported="false">
 *        <intent-filter>
 *            <action android:name="com.google.firebase.MESSAGING_EVENT" />
 *        </intent-filter>
 *    </service>
 * 
 * 5. Установите AppFeatures.ENABLE_FCM_ACCELERATOR = true
 */
