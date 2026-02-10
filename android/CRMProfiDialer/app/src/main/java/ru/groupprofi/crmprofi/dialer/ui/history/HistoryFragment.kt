package ru.groupprofi.crmprofi.dialer.ui.history

import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.Lifecycle
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.ListAdapter
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.textfield.TextInputEditText
import com.google.android.material.button.MaterialButton
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallDirection
import ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer
import androidx.core.content.ContextCompat

/**
 * Фрагмент вкладки "История" - список всех звонков (AUTO и MANUAL).
 */
class HistoryFragment : Fragment() {
    private lateinit var recyclerView: RecyclerView
    private lateinit var searchInput: TextInputEditText
    private lateinit var emptyState: TextView
    private lateinit var filterAllButton: MaterialButton
    private lateinit var filterOutgoingButton: MaterialButton
    private lateinit var filterIncomingButton: MaterialButton
    private lateinit var filterMissedButton: MaterialButton
    private lateinit var filterFailedButton: MaterialButton
    private lateinit var adapter: CallsHistoryAdapter
    
    private val callHistoryStore = AppContainer.callHistoryStore
    private var searchJob: Job? = null
    private var searchQuery = ""
    
    private enum class FilterType {
        ALL, OUTGOING, INCOMING, MISSED, FAILED
    }
    
    private var filterType: FilterType = FilterType.ALL
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_history, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        recyclerView = view.findViewById(R.id.callsRecyclerView)
        searchInput = view.findViewById(R.id.searchInput)
        emptyState = view.findViewById(R.id.emptyState)
        filterAllButton = view.findViewById(R.id.filterAllButton)
        filterOutgoingButton = view.findViewById(R.id.filterOutgoingButton)
        filterIncomingButton = view.findViewById(R.id.filterIncomingButton)
        filterMissedButton = view.findViewById(R.id.filterMissedButton)
        filterFailedButton = view.findViewById(R.id.filterFailedButton)
        
        adapter = CallsHistoryAdapter(
            onCallClick = { handleCallAction(it) },
            onItemClick = { showCallDetailBottomSheet(it) }
        )
        
        recyclerView.layoutManager = LinearLayoutManager(requireContext())
        recyclerView.adapter = adapter
        
        setupSearch()
        setupFilters()
        setupReactiveSubscription()
    }
    
    private fun setupFilters() {
        filterAllButton.setOnClickListener { setFilter(FilterType.ALL) }
        filterOutgoingButton.setOnClickListener { setFilter(FilterType.OUTGOING) }
        filterIncomingButton.setOnClickListener { setFilter(FilterType.INCOMING) }
        filterMissedButton.setOnClickListener { setFilter(FilterType.MISSED) }
        filterFailedButton.setOnClickListener { setFilter(FilterType.FAILED) }
        updateFilterButtons()
    }

    private fun setFilter(type: FilterType) {
        if (filterType == type) return
        filterType = type
        updateFilterButtons()
        updateFilteredCalls()
    }

    private fun updateFilterButtons() {
        val context = requireContext()
        fun style(button: MaterialButton, selected: Boolean) {
            if (selected) {
                button.setBackgroundColor(ContextCompat.getColor(context, R.color.bottom_nav_selected))
                button.setTextColor(ContextCompat.getColor(context, android.R.color.white))
            } else {
                button.setBackgroundColor(ContextCompat.getColor(context, R.color.surface_variant))
                button.setTextColor(ContextCompat.getColor(context, R.color.on_surface))
            }
        }
        style(filterAllButton, filterType == FilterType.ALL)
        style(filterOutgoingButton, filterType == FilterType.OUTGOING)
        style(filterIncomingButton, filterType == FilterType.INCOMING)
        style(filterMissedButton, filterType == FilterType.MISSED)
        style(filterFailedButton, filterType == FilterType.FAILED)
    }
    
    private fun setupSearch() {
        searchInput.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            
            override fun afterTextChanged(s: Editable?) {
                searchJob?.cancel()
                searchJob = lifecycleScope.launch {
                    delay(300) // Debounce
                    searchQuery = s?.toString()?.trim() ?: ""
                    updateFilteredCalls()
                }
            }
        })
    }
    
    private fun setupReactiveSubscription() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.callsFlow.collectLatest { _ ->
                    updateFilteredCalls()
                }
            }
        }
    }
    
    private fun updateFilteredCalls() {
        lifecycleScope.launch {
            val allCalls = callHistoryStore.callsFlow.value
            
            // Фильтруем по поисковому запросу
            val searchFiltered = if (searchQuery.isBlank()) {
                allCalls
            } else {
                val normalizedQuery = PhoneNumberNormalizer.normalize(searchQuery)
                allCalls.filter { call ->
                    val normalizedPhone = PhoneNumberNormalizer.normalize(call.phone)
                    normalizedPhone.contains(normalizedQuery, ignoreCase = true) ||
                    call.phoneDisplayName?.contains(searchQuery, ignoreCase = true) == true
                }
            }

            val filtered = filterByType(searchFiltered)
            
            adapter.submitList(filtered.sortedByDescending { it.startedAt })
            
            // Показываем пустое состояние
            if (filtered.isEmpty()) {
                emptyState.visibility = View.VISIBLE
                recyclerView.visibility = View.GONE
            } else {
                emptyState.visibility = View.GONE
                recyclerView.visibility = View.VISIBLE
            }
        }
    }

    private fun filterByType(calls: List<CallHistoryItem>): List<CallHistoryItem> {
        return when (filterType) {
            FilterType.ALL -> calls
            FilterType.OUTGOING -> calls.filter { it.direction == CallDirection.OUTGOING }
            FilterType.INCOMING -> calls.filter { it.direction == CallDirection.INCOMING }
            FilterType.MISSED -> calls.filter {
                it.direction == CallDirection.MISSED || it.status == CallHistoryItem.CallStatus.NO_ANSWER
            }
            FilterType.FAILED -> calls.filter {
                it.status == CallHistoryItem.CallStatus.REJECTED ||
                it.status == CallHistoryItem.CallStatus.UNKNOWN ||
                it.status == CallHistoryItem.CallStatus.NO_ACTION
            }
        }
    }
    
    private fun handleCallAction(call: CallHistoryItem) {
        val coordinator = CallFlowCoordinator.getInstance(requireContext())
        coordinator.handleCallCommandFromHistory(call.phone, call.id)
    }

    private fun showCallDetailBottomSheet(call: CallHistoryItem) {
        CallDetailBottomSheet.newInstance(call).show(childFragmentManager, CallDetailBottomSheet.TAG)
    }

    /**
     * Адаптер для списка звонков: ListAdapter + DiffUtil для минимума allocation и 60fps на слабых устройствах.
     */
    private class CallsHistoryAdapter(
        private val onCallClick: (CallHistoryItem) -> Unit,
        private val onItemClick: (CallHistoryItem) -> Unit
    ) : ListAdapter<CallHistoryItem, CallsHistoryAdapter.ViewHolder>(DIFF_CALLBACK) {

        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_call_history, parent, false)
            return ViewHolder(view, onCallClick, onItemClick)
        }

        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            holder.bind(getItem(position))
        }

        companion object {
            private val DIFF_CALLBACK = object : DiffUtil.ItemCallback<CallHistoryItem>() {
                override fun areItemsTheSame(a: CallHistoryItem, b: CallHistoryItem) = a.id == b.id
                override fun areContentsTheSame(a: CallHistoryItem, b: CallHistoryItem) = a == b
            }
        }

        class ViewHolder(
            itemView: View,
            private val onCallClick: (CallHistoryItem) -> Unit,
            private val onItemClick: (CallHistoryItem) -> Unit
        ) : RecyclerView.ViewHolder(itemView) {
            private val statusIcon: TextView = itemView.findViewById(R.id.statusIcon)
            private val phoneText: TextView = itemView.findViewById(R.id.phoneText)
            private val nameText: TextView = itemView.findViewById(R.id.nameText)
            private val statusText: TextView = itemView.findViewById(R.id.statusText)
            private val durationText: TextView = itemView.findViewById(R.id.durationText)
            private val dateText: TextView = itemView.findViewById(R.id.dateText)
            private val crmBadge: TextView = itemView.findViewById(R.id.crmBadge)
            private val copyButton: com.google.android.material.button.MaterialButton = itemView.findViewById(R.id.copyButton)
            private val callButton: com.google.android.material.button.MaterialButton = itemView.findViewById(R.id.callButton)

            fun bind(call: CallHistoryItem) {
                val (icon, iconColor) = when (call.status) {
                    CallHistoryItem.CallStatus.CONNECTED -> "✓" to ContextCompat.getColor(itemView.context, R.color.accent)
                    CallHistoryItem.CallStatus.NO_ANSWER, CallHistoryItem.CallStatus.REJECTED -> "✕" to ContextCompat.getColor(itemView.context, R.color.warning)
                    else -> "•" to ContextCompat.getColor(itemView.context, R.color.on_surface_variant)
                }
                statusIcon.text = icon
                statusIcon.setTextColor(iconColor)

                phoneText.text = call.phone
                nameText.text = call.phoneDisplayName ?: ""
                nameText.visibility = if (call.phoneDisplayName != null) View.VISIBLE else View.GONE

                statusText.text = call.getStatusText()
                durationText.text = call.getDurationText()
                dateText.text = call.getDateTimeText()

                val badgeText = if (call.sentToCrm) "Отправлено в CRM" else "Не отправлено в CRM"
                crmBadge.text = badgeText
                val badgeColor = if (call.sentToCrm) ContextCompat.getColor(itemView.context, R.color.accent) else ContextCompat.getColor(itemView.context, R.color.on_surface_variant)
                crmBadge.setTextColor(badgeColor)

                itemView.contentDescription = "${call.phone}, ${call.getStatusText()}"
                itemView.setOnClickListener { onItemClick(call) }

                copyButton.setOnClickListener {
                    val clipboard = itemView.context.getSystemService(android.content.Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
                    val clip = android.content.ClipData.newPlainText("Номер телефона", call.phone)
                    clipboard.setPrimaryClip(clip)
                    android.widget.Toast.makeText(itemView.context, "Скопировано", android.widget.Toast.LENGTH_SHORT).show()
                }
                callButton.setOnClickListener { onCallClick(call) }
            }
        }
    }
}
