package ru.groupprofi.crmprofi.dialer.ui.dialer

import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.view.HapticFeedbackConstants
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.ActionSource
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import ru.groupprofi.crmprofi.dialer.domain.ResolveMethod
import ru.groupprofi.crmprofi.dialer.diagnostics.DiagnosticsMetricsBuffer
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.network.ApiClient
import java.util.UUID

/**
 * Фрагмент вкладки "Набор" — ручной набор номера и звонок.
 * Фиксирует все ручные звонки в историю и отправляет в CRM/аналитику.
 */
class DialerFragment : Fragment() {
    private lateinit var phoneDisplayText: TextView
    private lateinit var phoneHintText: TextView
    private lateinit var callButton: MaterialButton
    private lateinit var backspaceButton: ImageButton
    private lateinit var lastCallStatus: TextView

    private val callHistoryStore = AppContainer.callHistoryStore
    private val pendingCallStore = AppContainer.pendingCallStore
    private val apiClient = AppContainer.apiClient

    private val COUNTRY_PREFIX = "+7"
    private val TOLL_FREE_PREFIX = "8" // для номеров вида 8 800 ...

    // «Сырая» модель ввода: только цифры 0–9, без префикса +7 / 8.
    private var rawDigits: String = ""

    // Режим 8-800: отдельный UX, как у реальной звонилки.
    private var isTollFree8800: Boolean = false
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_dialer, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        phoneDisplayText = view.findViewById(R.id.phoneDisplayText)
        phoneHintText = view.findViewById(R.id.phoneHintText)
        callButton = view.findViewById(R.id.callButton)
        backspaceButton = view.findViewById(R.id.backspaceButton)
        lastCallStatus = view.findViewById(R.id.lastCallStatus)
        
        setupCallButton()
        setupKeypad(view)
        setupBackspace()
        renderPhoneState()
    }

    private fun setupKeypad(root: View) {
        val buttons = listOf(
            R.id.digitButton1 to '1',
            R.id.digitButton2 to '2',
            R.id.digitButton3 to '3',
            R.id.digitButton4 to '4',
            R.id.digitButton5 to '5',
            R.id.digitButton6 to '6',
            R.id.digitButton7 to '7',
            R.id.digitButton8 to '8',
            R.id.digitButton9 to '9',
            R.id.digitButton0 to '0',
            R.id.digitButtonStar to '*',
            R.id.digitButtonHash to '#',
        )

        buttons.forEach { (id, value) ->
            val button = root.findViewById<MaterialButton>(id)
            button?.setOnClickListener { view ->
                performHaptic(view)
                onDigitPressed(value)
            }
        }
    }

    private fun setupBackspace() {
        backspaceButton.setOnClickListener { view ->
            performHaptic(view)
            onBackspace()
        }
        backspaceButton.setOnLongClickListener { view ->
            performHaptic(view)
            onBackspaceLongPress()
            true
        }
    }

    private fun onDigitPressed(digit: Char) {
        if (!digit.isDigit()) return

        // Россия UX по умолчанию: первая «8» не уходит в номер,
        // т.к. у нас уже есть +7. Но даём возможность набрать
        // отдельный режим 8‑800… — обрабатываем его отдельно.
        if (!isTollFree8800 && rawDigits.isEmpty() && digit == '8') {
            // Кандидат на 8‑800: фиксируем режим, но не добавляем 8 в rawDigits,
            // чтобы rawDigits по‑прежнему был “чистыми” цифрами без префикса.
            isTollFree8800 = true
            renderPhoneState()
            return
        }

        if (rawDigits.length < 10) {
            rawDigits += digit
        }
        renderPhoneState()
    }

    private fun onBackspace() {
        if (rawDigits.isNotEmpty()) {
            rawDigits = rawDigits.dropLast(1)
        }
        if (rawDigits.isEmpty()) {
            // Возвращаемся в обычный российский режим с +7.
            isTollFree8800 = false
        }
        renderPhoneState()
    }

    private fun onBackspaceLongPress() {
        rawDigits = ""
        isTollFree8800 = false
        renderPhoneState()
    }

    private fun performHaptic(view: View) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            view.performHapticFeedback(HapticFeedbackConstants.CONTEXT_CLICK)
        } else {
            @Suppress("DEPRECATION")
            view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY)
        }
    }

    /** Автоформат номера для отображения.
     *
     *  Режимы:
     *  - обычный российский номер: +7 (XXX) XXX-XX-XX
     *  - номер 8‑800…: 8 800 XXX-XX-XX
     *
     *  Вход: только «сырые» цифры номера без префиксов +7 / 8.
     */
    private fun formatPhoneDisplay(raw: String): String {
        val digits = raw.take(10)

        if (isTollFree8800) {
            // Формат: 8 800 XXX-XX-XX (на реальном dialer’е часто без скобок)
            if (digits.isEmpty()) {
                return TOLL_FREE_PREFIX
            }

            val sb = StringBuilder(TOLL_FREE_PREFIX)
            sb.append(" ")

            when {
                digits.length <= 3 -> {
                    // Строим "800"
                    sb.append(digits)
                }
                digits.length <= 6 -> {
                    sb.append(digits.substring(0, 3))
                        .append(" ")
                        .append(digits.substring(3))
                }
                digits.length <= 8 -> {
                    sb.append(digits.substring(0, 3))
                        .append(" ")
                        .append(digits.substring(3, 6))
                        .append("-")
                        .append(digits.substring(6))
                }
                else -> {
                    sb.append(digits.substring(0, 3))
                        .append(" ")
                        .append(digits.substring(3, 6))
                        .append("-")
                        .append(digits.substring(6, 8))
                        .append("-")
                        .append(digits.substring(8))
                }
            }

            return sb.toString()
        } else {
            if (digits.isEmpty()) {
                return COUNTRY_PREFIX
            }

            val sb = StringBuilder(COUNTRY_PREFIX).append(" ")

            when {
                digits.length <= 3 -> {
                    sb.append("(")
                    sb.append(digits)
                }
                digits.length <= 6 -> {
                    sb.append("(")
                        .append(digits.substring(0, 3))
                        .append(") ")
                        .append(digits.substring(3))
                }
                digits.length <= 8 -> {
                    sb.append("(")
                        .append(digits.substring(0, 3))
                        .append(") ")
                        .append(digits.substring(3, 6))
                        .append("-")
                        .append(digits.substring(6))
                }
                else -> {
                    sb.append("(")
                        .append(digits.substring(0, 3))
                        .append(") ")
                        .append(digits.substring(3, 6))
                        .append("-")
                        .append(digits.substring(6, 8))
                        .append("-")
                        .append(digits.substring(8))
                }
            }

            return sb.toString()
        }
    }
    
    private fun setupCallButton() {
        callButton.setOnClickListener {
            performHaptic(it)
            val digitsCount = rawDigits.length
            if (digitsCount != 10) {
                Toast.makeText(requireContext(), getString(R.string.dialer_enter_phone_hint), Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }
            if (rawDigits.isBlank()) {
                Toast.makeText(requireContext(), getString(R.string.dialer_enter_phone_hint), Toast.LENGTH_SHORT).show()
                return@setOnClickListener
            }

            // Для вызова используем полный номер c нужным префиксом.
            val fullPhone = if (isTollFree8800) {
                TOLL_FREE_PREFIX + rawDigits
            } else {
                COUNTRY_PREFIX + rawDigits
            }
            initiateManualCall(fullPhone)
        }
    }

    private fun renderPhoneState() {
        val digitsCount = rawDigits.length

        // Отображение номера сверху
        phoneDisplayText.text = formatPhoneDisplay(rawDigits)

        // Подсказка под номером: только при пустом вводе
        phoneHintText.visibility = if (digitsCount == 0) View.VISIBLE else View.GONE

        // Состояние кнопки звонка
        val canCall = digitsCount == 10
        callButton.isEnabled = canCall
        if (canCall) {
            callButton.text = getString(R.string.dialer_call_button)
            callButton.backgroundTintList =
                android.content.res.ColorStateList.valueOf(
                    ContextCompat.getColor(requireContext(), R.color.dialer_call_enabled_bg)
                )
            callButton.setTextColor(ContextCompat.getColor(requireContext(), android.R.color.white))
            callButton.iconTint =
                android.content.res.ColorStateList.valueOf(
                    ContextCompat.getColor(requireContext(), android.R.color.white)
                )
        } else {
            callButton.text = getString(R.string.dialer_call_button_enter_number)
            callButton.backgroundTintList =
                android.content.res.ColorStateList.valueOf(
                    ContextCompat.getColor(requireContext(), R.color.dialer_call_disabled_bg)
                )
            val disabledColor = ContextCompat.getColor(requireContext(), R.color.on_surface_variant)
            callButton.setTextColor(disabledColor)
            callButton.iconTint =
                android.content.res.ColorStateList.valueOf(disabledColor)
        }

        // Статус последнего звонка по умолчанию скрыт, чтобы не ломать layout.
        lastCallStatus.visibility = View.GONE
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
