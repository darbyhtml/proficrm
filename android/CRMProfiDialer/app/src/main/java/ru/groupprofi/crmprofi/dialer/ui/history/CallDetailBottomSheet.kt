package ru.groupprofi.crmprofi.dialer.ui.history

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.core.content.ContextCompat
import com.google.android.material.bottomsheet.BottomSheetDialogFragment
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem

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
        val statusIcon: TextView = view.findViewById(R.id.detailStatusIcon)
        val phoneText: TextView = view.findViewById(R.id.detailPhoneText)
        val nameText: TextView = view.findViewById(R.id.detailNameText)
        val statusText: TextView = view.findViewById(R.id.detailStatusText)
        val metaText: TextView = view.findViewById(R.id.detailMetaText)
        val crmBadge: TextView = view.findViewById(R.id.detailCrmBadge)
        val copyButton = view.findViewById<com.google.android.material.button.MaterialButton>(R.id.detailCopyButton)
        val callButton = view.findViewById<com.google.android.material.button.MaterialButton>(R.id.detailCallButton)

        val (icon, iconColor) = when (c.status) {
            CallHistoryItem.CallStatus.CONNECTED -> "✓" to ContextCompat.getColor(view.context, R.color.accent)
            CallHistoryItem.CallStatus.NO_ANSWER, CallHistoryItem.CallStatus.REJECTED -> "✕" to ContextCompat.getColor(view.context, R.color.warning)
            else -> "•" to ContextCompat.getColor(view.context, R.color.on_surface_variant)
        }
        statusIcon.text = icon
        statusIcon.setTextColor(iconColor)
        statusIcon.contentDescription = getString(R.string.history_status_icon)

        phoneText.text = c.phone
        nameText.text = c.phoneDisplayName ?: ""
        nameText.visibility = if (c.phoneDisplayName != null) View.VISIBLE else View.GONE
        statusText.text = c.getStatusText()
        val duration = c.getDurationText()
        val date = c.getDateTimeText()
        metaText.text = if (duration.isNotEmpty()) "$duration · $date" else date
        crmBadge.text = if (c.sentToCrm) "Отправлено в CRM" else "Не отправлено в CRM"
        crmBadge.setTextColor(
            if (c.sentToCrm) ContextCompat.getColor(view.context, R.color.accent)
            else ContextCompat.getColor(view.context, R.color.on_surface_variant)
        )

        copyButton.setOnClickListener {
            val clipboard = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
            clipboard.setPrimaryClip(ClipData.newPlainText("Номер телефона", c.phone))
            Toast.makeText(requireContext(), getString(R.string.copied), Toast.LENGTH_SHORT).show()
        }
        callButton.setOnClickListener {
            CallFlowCoordinator.getInstance(requireContext()).handleCallCommandFromHistory(c.phone, c.id)
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
    }
}
