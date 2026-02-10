package ru.groupprofi.crmprofi.dialer.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage

/**
 * Реальная реализация FirebaseMessagingService, которая делегирует логику
 * в объект FcmMessagingService. Важно: этот сервис должен использоваться
 * только если Firebase SDK настроен и добавлен google-services.json.
 */
class FcmMessagingServiceImpl : FirebaseMessagingService() {

    override fun onMessageReceived(remoteMessage: RemoteMessage) {
        val pushType = remoteMessage.data["type"]
        FcmMessagingService.handlePushMessage(pushType, remoteMessage.data)
    }

    override fun onNewToken(token: String) {
        // Передаём applicationContext, чтобы FcmMessagingService мог вызвать ApiClient
        FcmMessagingService.handleNewToken(applicationContext, token)
    }
}


