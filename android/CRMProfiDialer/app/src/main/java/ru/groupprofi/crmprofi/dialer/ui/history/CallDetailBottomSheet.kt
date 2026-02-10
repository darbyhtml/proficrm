package ru.groupprofi.crmprofi.dialer.ui.history

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.ImageView
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer

/**
 * Bottom sheet с деталями звонка. Показывается по тапу на элемент истории.
 * Без новой Activity — только UI слой.
 */
class CallDetailBottomSheet : BottomSheetDialogFragment() {

    private var call: CallHistoryItem? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        @Suppress("DEPRECATION")
        call = arguments?.getSerializable(ARG_CALL) as? CallHistoryItem
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.bottom_sheet_call_detail, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        val c = call ?: return

        // Header actions
        val copyIcon: ImageButton = view.findViewById(R.id.detailCopyIcon)
        val callIcon: ImageButton = view.findViewById(R.id.detailCallIcon)
        val closeIcon: ImageButton = view.findViewById(R.id.detailCloseIcon)

        // Title and phone
        val nameText: TextView = view.findViewById(R.id.detailNameText)
        val phoneText: TextView = view.findViewById(R.id.detailPhoneText)

        // Status block
        val statusBlock: View = view.findViewById(R.id.detailStatusBlock)
        val statusIcon: ImageView = view.findViewById(R.id.detailStatusIcon)
        val statusTitle: TextView = view.findViewById(R.id.detailStatusTitle)
        val statusSubtitle: TextView = view.findViewById(R.id.detailStatusSubtitle)

        // Info cards
        val dateValue: TextView = view.findViewById(R.id.detailDateValue)
        val durationValue: TextView = view.findViewById(R.id.detailDurationValue)
        val idValue: TextView = view.findViewById(R.id.detailIdValue)

        // CRM status
        val crmIcon: ImageView = view.findViewById(R.id.detailCrmIcon)
        val crmText: TextView = view.findViewById(R.id.detailCrmText)

        // Заголовок: имя или "Неизвестный номер"
        val displayName = c.phoneDisplayName ?: getString(R.string.unknown_number)
        nameText.text = displayName

        // Телефон всегда форматируем
        phoneText.text = formatPhoneForDisplay(c.phone)

        // Статусный блок и иконка
        val (bgColor, iconRes, iconTint, statusTitleText, statusSubtitleText) = when {
            c.status == CallHistoryItem.CallStatus.CONNECTED -> {
                quintupleOf(
                    R.color.statusDoneBg,
                    R.drawable.ic_call_small,
                    R.color.statusDoneText,
                    "Завершён",
                    "Звонок состоялся"
                )
            }
            c.direction == CallDirection.MISSED ||
                    c.status == CallHistoryItem.CallStatus.NO_ANSWER -> {
                quintupleOf(
                    R.color.statusMissedBg,
                    R.drawable.ic_call_failed_small,
                    R.color.statusMissedText,
                    "Пропущен",
                    "Пропущенный звонок"
                )
            }
            else -> {
                quintupleOf(
                    R.color.statusFailBg,
                    R.drawable.ic_call_failed_small,
                    R.color.statusFailText,
                    "Не удалось соединить",
                    "Техническая ошибка"
                )
            }
        }

        statusBlock.setBackgroundColor(ContextCompat.getColor(view.context, bgColor))
        statusIcon.setImageResource(iconRes)
        statusIcon.setColorFilter(ContextCompat.getColor(view.context, iconTint))
        statusTitle.text = statusTitleText
        statusSubtitle.text = statusSubtitleText

        // Дата и время
        dateValue.text = c.getDateTimeText()

        // Длительность
        val durationSec = c.durationSeconds ?: 0
        durationValue.text = if (durationSec <= 0) {
            getString(R.string.call_detail_duration_not_happened)
        } else {
            val minutes = durationSec / 60
            val seconds = durationSec % 60
            String.format("%d:%02d", minutes, seconds)
        }

        // ID звонка (в модели он всегда non-null)
        idValue.text = c.id

        // CRM статус
        if (c.sentToCrm) {
            crmIcon.setImageResource(R.drawable.ic_check_circle)
            crmText.text = getString(R.string.call_detail_crm_synced)
            crmText.setTextColor(ContextCompat.getColor(view.context, R.color.brand_teal))
        } else {
            crmIcon.setImageResource(R.drawable.ic_warning)
            crmText.text = getString(R.string.call_detail_crm_not_synced)
            crmText.setTextColor(ContextCompat.getColor(view.context, R.color.on_surface_variant))
        }

        copyIcon.setOnClickListener {
            val clipboard = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("Номер телефона", c.phone))
            Toast.makeText(requireContext(), getString(R.string.copied), Toast.LENGTH_SHORT).show()
        }
        callIcon.setOnClickListener {
            CallFlowCoordinator.getInstance(requireContext()).handleCallCommandFromHistory(c.phone, c.id)
            dismiss()
        }

        closeIcon.setOnClickListener {
            dismiss()
        }
    }

    companion object {
        const val TAG = "CallDetailBottomSheet"
        private const val ARG_CALL = "call"

        fun newInstance(call: CallHistoryItem): CallDetailBottomSheet {
            return CallDetailBottomSheet().apply {
                arguments = Bundle().apply { putSerializable(ARG_CALL, call) }
            }
        }

        private fun quintupleOf(
            first: Int,
            second: Int,
            third: Int,
            fourth: String,
            fifth: String
        ): Quintuple<Int, Int, Int, String, String> = Quintuple(first, second, third, fourth, fifth)

        private data class Quintuple<A, B, C, D, E>(
            val first: A,
            val second: B,
            val third: C,
            val fourth: D,
            val fifth: E
        )
    }

    private fun formatPhoneForDisplay(phone: String): String {
        val normalized = PhoneNumberNormalizer.normalize(phone)
        if (normalized.length < 10) return phone

        val digits = when {
            normalized.length == 11 && (normalized.startsWith("7") || normalized.startsWith("8")) ->
                normalized.substring(1)
            normalized.length == 10 -> normalized
            else -> normalized.takeLast(10)
        }

        return buildString {
            append("+7 ")
            append("(")
            append(digits.substring(0, 3))
            append(") ")
            append(digits.substring(3, 6))
            append("-")
            append(digits.substring(6, 8))
            append("-")
            append(digits.substring(8))
        }
    }
}
