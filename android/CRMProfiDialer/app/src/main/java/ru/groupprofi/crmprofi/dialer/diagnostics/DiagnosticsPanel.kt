package ru.groupprofi.crmprofi.dialer.diagnostics

import android.content.Context
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.telecom.TelecomManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.network.PullCallMetrics
import ru.groupprofi.crmprofi.dialer.permissions.PermissionGate
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsMetricsBuffer
import java.text.SimpleDateFormat
import java.util.*

/**
 * Панель диагностики для отладки и поддержки.
 * Собирает информацию о состоянии приложения, разрешениях, сети, CallLog observer и т.д.
 */
object DiagnosticsPanel {
    
    /**
     * Сгенерировать диагностический отчет.
     */
    fun generateReport(context: Context): String {
        val sb = StringBuilder()
        val dateFormat = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.getDefault())
        
        sb.appendLine("=== CRM ПРОФИ ДИАГНОСТИЧЕСКИЙ ОТЧЕТ ===")
        sb.appendLine("Дата: ${dateFormat.format(Date())}")
        sb.appendLine()
        
        // 1. Информация о приложении
        sb.appendLine("--- ПРИЛОЖЕНИЕ ---")
        sb.appendLine("Версия: ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})")
        sb.appendLine("Build type: ${BuildConfig.BUILD_TYPE}")
        sb.appendLine("Android: ${Build.VERSION.RELEASE} (SDK ${Build.VERSION.SDK_INT})")
        sb.appendLine("Manufacturer: ${Build.MANUFACTURER}")
        sb.appendLine("Model: ${Build.MODEL}")
        sb.appendLine()
        
        // 2. Разрешения
        sb.appendLine("--- РАЗРЕШЕНИЯ ---")
        val callLogTracking = PermissionGate.checkCallLogTracking(context)
        val foregroundNotification = PermissionGate.checkForegroundNotification(context)
        sb.appendLine("CallLog tracking: ${if (callLogTracking.isGranted) "OK" else "NOT OK"}")
        if (!callLogTracking.isGranted) {
            sb.appendLine("  Отсутствуют: ${callLogTracking.missingPermissions.joinToString()}")
        }
        sb.appendLine("Foreground notification: ${if (foregroundNotification.isGranted) "OK" else "NOT OK"}")
        if (!foregroundNotification.isGranted) {
            sb.appendLine("  Отсутствуют: ${foregroundNotification.missingPermissions.joinToString()}")
        }
        sb.appendLine()
        
        // 3. PullCall метрики
        sb.appendLine("--- PULLCALL МЕТРИКИ ---")
        sb.appendLine("Текущий режим: ${PullCallMetrics.currentMode}")
        sb.appendLine("Причина деградации: ${PullCallMetrics.degradationReason}")
        sb.appendLine("Последняя команда: ${PullCallMetrics.getSecondsSinceLastCommand()?.let { "${it}с назад" } ?: "не было"}")
        sb.appendLine("Средняя задержка pullCall (последние 20): ${PullCallMetrics.lastPullLatencyMs}мс")
        sb.appendLine("Средняя задержка доставки (последние 20): ${PullCallMetrics.getAverageDeliveryLatencyMs()?.let { "${it}мс" } ?: "N/A"}")
        sb.appendLine("429 за последний час: ${PullCallMetrics.get429CountLastHour()}")
        sb.appendLine("Максимальный backoff достигнут: ${PullCallMetrics.maxBackoffReached}")
        sb.appendLine("Время в backoff: ${PullCallMetrics.getTimeSpentInBackoffMs() / 1000}с")
        sb.appendLine()
        
        // 4. CallLog Observer статус
        sb.appendLine("--- CALLLOG OBSERVER ---")
        // Проверяем, зарегистрирован ли observer (через проверку разрешений)
        val hasCallLogPerm = callLogTracking.isGranted
        sb.appendLine("Статус: ${if (hasCallLogPerm) "RUNNING (разрешения есть)" else "STOPPED (нет READ_CALL_LOG)"}")
        if (!hasCallLogPerm) {
            sb.appendLine("Причина остановки: READ_CALL_LOG не выдано")
        }
        sb.appendLine()
        
        // 5. Сеть
        sb.appendLine("--- СЕТЬ ---")
        val connectivityManager = context.getSystemService(Context.CONNECTIVITY_SERVICE) as? ConnectivityManager
        if (connectivityManager != null) {
            val network = connectivityManager.activeNetwork
            val capabilities = network?.let { connectivityManager.getNetworkCapabilities(it) }
            val isConnected = capabilities != null && (
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) ||
                capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            )
            sb.appendLine("Статус: ${if (isConnected) "Доступна" else "Недоступна"}")
            if (capabilities != null) {
                val transports = mutableListOf<String>()
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)) transports.add("WiFi")
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR)) transports.add("Cellular")
                if (capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)) transports.add("Ethernet")
                sb.appendLine("Типы: ${transports.joinToString()}")
            }
        } else {
            sb.appendLine("Статус: Не удалось определить")
        }
        sb.appendLine()
        
        // 6. Dual SIM / Default Dialer
        sb.appendLine("--- DUAL SIM / DEFAULT DIALER ---")
        val telephonyManager = context.getSystemService(Context.TELEPHONY_SERVICE) as? TelephonyManager
        if (telephonyManager != null) {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                @Suppress("DEPRECATION")
                val phoneCount = telephonyManager.phoneCount
                sb.appendLine("Количество SIM: $phoneCount")
                sb.appendLine("Dual SIM detected: ${phoneCount > 1}")
            } else {
                sb.appendLine("Dual SIM: Недоступно (Android < 6.0)")
            }
            
            // Проверка default dialer (TelecomManager, API 23+)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                val telecomManager = context.getSystemService(Context.TELECOM_SERVICE) as? TelecomManager
                val defaultDialer = telecomManager?.defaultDialerPackage
                val isDefaultDialer = defaultDialer == context.packageName
                sb.appendLine("Default dialer: ${if (isDefaultDialer) "Да (это приложение)" else "Нет ($defaultDialer)"}")
            }
        } else {
            sb.appendLine("TelephonyManager недоступен")
        }
        sb.appendLine()
        
        // 7. Активные ожидаемые звонки
        sb.appendLine("--- АКТИВНЫЕ ЗВОНКИ ---")
        val pendingCalls = kotlinx.coroutines.runBlocking { AppContainer.pendingCallStore.getActivePendingCalls() }
        sb.appendLine("Количество активных: ${pendingCalls.size}")
        pendingCalls.take(5).forEachIndexed { index, call ->
            sb.appendLine("  ${index + 1}. ID=${call.callRequestId.take(8)}..., phone=***${call.phoneNumber.takeLast(4)}, state=${call.state}")
        }
        if (pendingCalls.size > 5) {
            sb.appendLine("  ... и еще ${pendingCalls.size - 5}")
        }
        sb.appendLine()
        
        // 8. История звонков (статистика)
        sb.appendLine("--- ИСТОРИЯ ЗВОНКОВ ---")
        kotlinx.coroutines.runBlocking {
            val allCalls = AppContainer.callHistoryStore.getAllCalls()
            val todayCalls = allCalls.filter { 
                val today = Calendar.getInstance().apply { 
                    set(Calendar.HOUR_OF_DAY, 0)
                    set(Calendar.MINUTE, 0)
                    set(Calendar.SECOND, 0)
                    set(Calendar.MILLISECOND, 0)
                }.timeInMillis
                it.startedAt >= today
            }
            sb.appendLine("Всего звонков: ${allCalls.size}")
            sb.appendLine("Сегодня: ${todayCalls.size}")
            val byStatus = todayCalls.groupBy { it.status }
            byStatus.forEach { (status, calls) ->
                sb.appendLine("  ${status.name}: ${calls.size}")
            }
        }
        sb.appendLine()
        
        // 8.5. Диагностические события (последние 20)
        sb.appendLine("--- ДИАГНОСТИЧЕСКИЕ СОБЫТИЯ (последние 20) ---")
        val recentEvents = DiagnosticsMetricsBuffer.getLastEvents(20)
        if (recentEvents.isEmpty()) {
            sb.appendLine("Событий нет")
        } else {
            recentEvents.forEach { event ->
                val timeStr = SimpleDateFormat("HH:mm:ss", Locale.getDefault()).format(Date(event.timestamp))
                sb.appendLine("[$timeStr] ${event.type.name}: ${event.message}")
                if (event.metadata.isNotEmpty()) {
                    event.metadata.forEach { (key, value) ->
                        sb.appendLine("  $key: $value")
                    }
                }
            }
        }
        sb.appendLine()
        
        // 9. Авторизация
        sb.appendLine("--- АВТОРИЗАЦИЯ ---")
        val tokenManager = AppContainer.tokenManager
        val hasTokens = tokenManager.hasTokens()
        sb.appendLine("Статус: ${if (hasTokens) "Авторизован" else "Не авторизован"}")
        if (hasTokens) {
            val username = tokenManager.getUsername()
            sb.appendLine("Пользователь: ${username ?: "N/A"}")
        }
        sb.appendLine()
        
        sb.appendLine("=== КОНЕЦ ОТЧЕТА ===")
        
        return sb.toString()
    }
    
    /**
     * Скопировать отчет в буфер обмена.
     */
    fun copyToClipboard(context: Context, report: String) {
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.DIAGNOSTICS_EXPORTED,
            "Отчёт скопирован в буфер обмена",
            mapOf("action" to "copy")
        )
        val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
        clipboard?.setPrimaryClip(ClipData.newPlainText("CRM ПРОФИ Диагностика", report))
    }
    
    /**
     * Поделиться отчетом через Intent.
     */
    fun shareReport(context: Context, report: String) {
        DiagnosticsMetricsBuffer.addEvent(
            DiagnosticsMetricsBuffer.EventType.DIAGNOSTICS_EXPORTED,
            "Отчёт экспортирован (поделиться)",
            mapOf("action" to "share")
        )
        val intent = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, report)
            putExtra(Intent.EXTRA_SUBJECT, "CRM ПРОФИ Диагностика")
        }
        context.startActivity(Intent.createChooser(intent, "Поделиться диагностикой"))
    }
}
