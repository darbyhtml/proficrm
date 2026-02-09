package ru.groupprofi.crmprofi.dialer.ui.dialer

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.HapticFeedbackConstants
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.network.ApiClient
import ru.groupprofi.crmprofi.dialer.domain.CallStatusApi
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsMetricsBuffer
import java.util.UUID

/**
 * Фрагмент вкладки "Телефон" - ручной набор номера и звонок.
 * Фиксирует все ручные звонки в историю и отправляет в CRM/аналитику.
 */
class DialerFragment : Fragment() {
    private lateinit var phoneInput: TextInputEditText
    private lateinit var callButton: MaterialButton
    private lateinit var lastCallStatus: TextView
    
    private val callHistoryStore = AppContainer.callHistoryStore
    private val pendingCallStore = AppContainer.pendingCallStore
    private val apiClient = AppContainer.apiClient
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_dialer, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        phoneInput = view.findViewById(R.id.phoneInput)
        callButton = view.findViewById(R.id.callButton)
        lastCallStatus = view.findViewById(R.id.lastCallStatus)
        
        setupPhoneInput()
        setupCallButton()
    }
    
    private var isFormatting = false
    private fun setupPhoneInput() {
        phoneInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            override fun afterTextChanged(s: Editable?) {
                if (isFormatting) return
                val raw = s?.toString() ?: ""
                val digits = raw.filter { it.isDigit() }
                val formatted = formatPhoneDisplay(digits)
                if (formatted != raw) {
                    isFormatting = true
                    val newSel = formatted.length.coerceAtMost(phoneInput.selectionStart + (formatted.length - raw.length).coerceAtLeast(0))
                    phoneInput.text?.replace(0, raw.length, formatted)
                    phoneInput.setSelection(newSel.coerceIn(0, formatted.length))
                    isFormatting = false
                }
                callButton.isEnabled = digits.length >= 7
            }
        })
    }

    /** Автоформат номера: +7 (XXX) XXX-XX-XX для российских 11 цифр. */
    private fun formatPhoneDisplay(digits: String): String {
        if (digits.isEmpty()) return ""
        when {
            digits == "8" -> return "8"
            digits == "7" -> return "+7"
            digits.startsWith("8") && digits.length <= 11 -> return formatPhoneDisplay("7" + digits.drop(1))
            digits.startsWith("7") && digits.length <= 11 -> {
                val rest = digits.drop(1)
                return buildString {
                    append("+7")
                    if (rest.isNotEmpty()) append(" (").append(rest.take(3))
                    if (rest.length > 3) append(") ").append(rest.drop(3).take(3))
                    if (rest.length > 6) append("-").append(rest.drop(6).take(2))
                    if (rest.length > 8) append("-").append(rest.drop(8).take(2))
                }
            }
            else -> return digits.chunked(3).joinToString(" ").take(20)
        }
    }
    
    private fun setupCallButton() {
        callButton.setOnClickListener {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
                it.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
            } else {
                @Suppress("DEPRECATION")
                it.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
            }
            val phone = phoneInput.text?.toString()?.trim() ?: ""
            if (phone.isBlank()) {
                Toast.makeText(requireContext(), "Введите номер телефона", Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            initiateManualCall(phone)
        }
    }
    
    /**
     * Инициировать ручной звонок.
     * Создает запись в истории, открывает звонилку, отслеживает результат.
     */
    private fun initiateManualCall(phone: String) {
        lifecycleScope.launch(Dispatchers.IO) {
            try {
                val normalizedPhone = PhoneNumberNormalizer.normalize(phone)
                val callRequestId = UUID.randomUUID().toString() // Генерируем уникальный ID для ручного звонка
                val startedAt = System.currentTimeMillis()
                
                AppLogger.i("DialerFragment", "MANUAL_CALL_INITIATED phone=${maskPhone(normalizedPhone)} id=$callRequestId")
                
                // Создаем запись в истории со статусом PENDING (будет обновлен после звонка)
                val historyItem = CallHistoryItem(
                    id = callRequestId,
                    phone = normalizedPhone,
                    phoneDisplayName = null,
                    status = CallHistoryItem.CallStatus.UNKNOWN, // Начнем с UNKNOWN, обновим после звонка
                    durationSeconds = null,
                    startedAt = startedAt,
                    sentToCrm = false,
                    sentToCrmAt = null,
                    direction = null,
                    resolveMethod = ResolveMethod.UNKNOWN,
                    attemptsCount = 0,
                    actionSource = ActionSource.MANUAL, // Важно: помечаем как MANUAL
                    endedAt = null
                )
                
                callHistoryStore.addOrUpdate(historyItem)
                
                // Проверяем разрешения перед инициированием звонка
                val callLogTrackingStatus = ru.groupprofi.crmprofi.dialer.permissions.PermissionGate.checkCallLogTracking(requireContext())
                val canTrackResult = callLogTrackingStatus.isGranted
                
                // Открываем системную звонилку
                withContext(Dispatchers.Main) {
                    openDialer(normalizedPhone)
                    if (canTrackResult) {
                        lastCallStatus.text = "Звонок начат..."
                    } else {
                        lastCallStatus.text = "Звонок начат. Результат не может быть определён — нет доступа к журналу вызовов"
                        lastCallStatus.setTextColor(0xFFFF9800.toInt()) // Оранжевый цвет для предупреждения
                    }
                    lastCallStatus.visibility = View.VISIBLE
                }
                
                // Если нет разрешений - сразу помечаем как UNKNOWN с причиной
                if (!canTrackResult) {
                    val unknownItem = historyItem.copy(
                        status = CallHistoryItem.CallStatus.UNKNOWN,
                        resolveMethod = ResolveMethod.UNKNOWN,
                        attemptsCount = 0
                    )
                    callHistoryStore.addOrUpdate(unknownItem)
                    
                    // Отправляем в CRM (если режим FULL) с причиной
                    val apiResult = apiClient.sendCallUpdate(
                        callRequestId = callRequestId,
                        callStatus = ru.groupprofi.crmprofi.dialer.domain.CallStatusApi.UNKNOWN.apiValue,
                        callStartedAt = startedAt,
                        callDurationSeconds = null,
                        direction = null,
                        resolveMethod = ResolveMethod.UNKNOWN,
                        resolveReason = "missing_calllog_permission",
                        reasonIfUnknown = "READ_CALL_LOG not granted - cannot determine call result",
                        attemptsCount = 0,
                        actionSource = ActionSource.MANUAL,
                        endedAt = null
                    )
                    
                    // Обновляем sentToCrm флаг
                    if (apiResult is ApiClient.Result.Success) {
                        callHistoryStore.markSent(callRequestId, System.currentTimeMillis())
                    }
                    
                    AppLogger.w("DialerFragment", "Manual call initiated without READ_CALL_LOG permission - result cannot be determined")
                    return@launch
                }
                
                // Создаем PendingCall для отслеживания результата через CallFlowCoordinator
                // Используем handleCallCommandFromHistory, но с actionSource = MANUAL
                // CallFlowCoordinator создаст PendingCall, который будет отслеживаться через CallLogObserverManager
                // Для ручных звонков создаем PendingCall напрямую с actionSource = MANUAL
                val pendingCall = ru.groupprofi.crmprofi.dialer.domain.PendingCall(
                    callRequestId = callRequestId,
                    phoneNumber = normalizedPhone,
                    startedAtMillis = startedAt,
                    state = ru.groupprofi.crmprofi.dialer.domain.PendingCall.PendingState.PENDING,
                    attempts = 0,
                    actionSource = ActionSource.MANUAL
                )
                // CALL_RESOLVE_START только при первом добавлении (ручной звонок = один раз на действие)
                val isNew = pendingCallStore.getPendingCall(callRequestId) == null
                AppContainer.pendingCallStore.addPendingCall(pendingCall)
                if (isNew) {
                    DiagnosticsMetricsBuffer.addEvent(
                        DiagnosticsMetricsBuffer.EventType.CALL_RESOLVE_START,
                        "Поиск результата по CallLog",
                        mapOf(
                            "source" to ActionSource.MANUAL.name,
                            "callRequestId" to callRequestId.take(8) + "..."
                        )
                    )
                }
                
                // Отправляем событие manual_call_initiated в телеметрию
                // (через существующую систему телеметрии)
                apiClient.flushTelemetry()
                
            } catch (e: Exception) {
                AppLogger.e("DialerFragment", "Ошибка инициирования ручного звонка: ${e.message}", e)
                withContext(Dispatchers.Main) {
                    Toast.makeText(requireContext(), "Ошибка: ${e.message}", Toast.LENGTH_LONG).show()
                }
            }
        }
    }
    
    /**
     * Открыть системную звонилку.
     */
    private fun openDialer(phone: String) {
        try {
            val uri = Uri.parse("tel:$phone")
            val dialIntent = Intent(Intent.ACTION_DIAL, uri).apply {
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(dialIntent)
        } catch (e: Exception) {
            Toast.makeText(requireContext(), "Не удалось открыть звонилку", Toast.LENGTH_SHORT).show()
            AppLogger.e("DialerFragment", "Ошибка открытия звонилки: ${e.message}", e)
        }
    }
    
    /**
     * Маскировать номер для логов.
     */
    private fun maskPhone(phone: String): String {
        if (phone.length <= 4) return "***"
        return "${phone.take(3)}***${phone.takeLast(4)}"
    }
}
