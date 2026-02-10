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
import android.content.res.ColorStateList
import androidx.core.view.ViewCompat

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

            // Счётчики для всех фильтров (учитывают поиск)
            val allCount = searchFiltered.size
            val outgoingCount = filterByType(searchFiltered, FilterType.OUTGOING).size
            val incomingCount = filterByType(searchFiltered, FilterType.INCOMING).size
            val missedCount = filterByType(searchFiltered, FilterType.MISSED).size
            val failedCount = filterByType(searchFiltered, FilterType.FAILED).size

            filterAllButton.text = getString(R.string.history_filter_all, allCount)
            filterOutgoingButton.text = getString(R.string.history_filter_outgoing, outgoingCount)
            filterIncomingButton.text = getString(R.string.history_filter_incoming, incomingCount)
            filterMissedButton.text = getString(R.string.history_filter_missed, missedCount)
            filterFailedButton.text = getString(R.string.history_filter_failed, failedCount)

            // Применяем текущий фильтр
            val filtered = filterByType(searchFiltered, filterType)

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

    private fun filterByType(calls: List<CallHistoryItem>, type: FilterType): List<CallHistoryItem> {
        return when (type) {
            FilterType.ALL -> calls
            FilterType.OUTGOING -> calls.filter { it.direction == CallDirection.OUTGOING }
            FilterType.INCOMING -> calls.filter { it.direction == CallDirection.INCOMING }
            FilterType.MISSED -> calls.filter { isMissed(it) }
            FilterType.FAILED -> calls.filter { isFailed(it) }
        }
    }

    // Пропущенные: звонки, которые не были приняты пользователем.
    private fun isMissed(call: CallHistoryItem): Boolean {
        return call.direction == CallDirection.MISSED ||
                call.status == CallHistoryItem.CallStatus.NO_ANSWER
    }

    // «Неудачные»: технические/системные кейсы, не зависящие от менеджера.
    // REJECTED, NO_ACTION, UNKNOWN (FAILED приходит как один из этих статусов).
    private fun isFailed(call: CallHistoryItem): Boolean {
        return call.status == CallHistoryItem.CallStatus.REJECTED ||
                call.status == CallHistoryItem.CallStatus.NO_ACTION ||
                call.status == CallHistoryItem.CallStatus.UNKNOWN
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
            private val statusIcon: android.widget.ImageView = itemView.findViewById(R.id.statusIcon)
            private val phoneText: TextView = itemView.findViewById(R.id.phoneText)
            private val nameText: TextView = itemView.findViewById(R.id.nameText)
            private val statusText: TextView = itemView.findViewById(R.id.statusText)
            private val durationText: TextView = itemView.findViewById(R.id.durationText)
            private val dateText: TextView = itemView.findViewById(R.id.dateText)
            private val crmBadge: TextView = itemView.findViewById(R.id.crmBadge)
            private val divider: View = itemView.findViewById(R.id.divider)

            fun bind(call: CallHistoryItem) {
                val context = itemView.context

                // Левая иконка: обычные звонки — серая, пропущенные/неудачные — красная.
                val (iconRes, iconBgColor, iconTint) = when {
                    isFailed(call) || isMissed(call) -> Triple(
                        R.drawable.ic_call_failed_small,
                        ContextCompat.getColor(context, R.color.history_badge_failed_bg),
                        ContextCompat.getColor(context, R.color.history_badge_failed_text)
                    )
                    else -> Triple(
                        R.drawable.ic_call_small,
                        ContextCompat.getColor(context, R.color.softGrayBg),
                        ContextCompat.getColor(context, R.color.on_surface_variant)
                    )
                }
                statusIcon.setImageResource(iconRes)
                ViewCompat.setBackgroundTintList(
                    statusIcon.parent as View,
                    ColorStateList.valueOf(iconBgColor)
                )
                statusIcon.imageTintList = ColorStateList.valueOf(iconTint)

                // Первая строка: имя, если есть, иначе номер.
                val title = call.phoneDisplayName ?: formatPhoneForDisplay(call.phone)
                phoneText.text = title

                // Вторая строка: всегда номер (здесь можно добавить форматирование при необходимости).
                nameText.text = if (call.phoneDisplayName != null) {
                    formatPhoneForDisplay(call.phone)
                } else {
                    ""
                }

                // Третья строка: время и длительность (только для успешных).
                val timeText = java.text.SimpleDateFormat("HH:mm", java.util.Locale("ru"))
                    .format(java.util.Date(call.startedAt))
                val durationSec = call.durationSeconds ?: 0
                val durationPart = if (call.status == CallHistoryItem.CallStatus.CONNECTED && durationSec > 0) {
                    val minutes = durationSec / 60
                    val seconds = durationSec % 60
                    String.format("%d:%02d", minutes, seconds)
                } else {
                    null
                }
                statusText.text = if (durationPart != null) {
                    "$timeText · $durationPart"
                } else {
                    timeText
                }

                // Заглушки duration/date не используем в новом UI
                durationText.text = ""
                dateText.text = ""

                // Бейдж статуса звонка как в макете: "Завершён", "Пропущен", "Не удалось соединить"
                val (badgeText, badgeBg, badgeTextColor) = when {
                    call.status == CallHistoryItem.CallStatus.CONNECTED ->
                        Triple(
                            "Завершён",
                            ContextCompat.getColor(context, R.color.history_badge_completed_bg),
                            ContextCompat.getColor(context, R.color.history_badge_completed_text)
                        )
                    isMissed(call) ->
                        Triple(
                            "Пропущен",
                            ContextCompat.getColor(context, R.color.history_badge_missed_bg),
                            ContextCompat.getColor(context, R.color.history_badge_missed_text)
                        )
                    isFailed(call) ->
                        Triple(
                            "Не удалось соединить",
                            ContextCompat.getColor(context, R.color.history_badge_failed_bg),
                            ContextCompat.getColor(context, R.color.history_badge_failed_text)
                        )
                    else ->
                        Triple(
                            "Не удалось соединить",
                            ContextCompat.getColor(context, R.color.history_badge_failed_bg),
                            ContextCompat.getColor(context, R.color.history_badge_failed_text)
                        )
                }
                crmBadge.text = badgeText
                crmBadge.setTextColor(badgeTextColor)
                ViewCompat.setBackgroundTintList(
                    crmBadge,
                    ColorStateList.valueOf(badgeBg)
                )

                itemView.contentDescription = "${formatPhoneForDisplay(call.phone)}, ${call.getStatusText()}"
                itemView.setOnClickListener { onItemClick(call) }

                // Разделитель скрываем у последнего элемента списка.
                val isLast = bindingAdapterPosition == (bindingAdapter?.itemCount ?: 0) - 1
                divider.visibility = if (isLast) View.GONE else View.VISIBLE
            }

            // Локальные помощники, чтобы ViewHolder мог использовать те же правила, что и фрагмент.
            private fun isMissed(call: CallHistoryItem): Boolean {
                return call.direction == CallDirection.MISSED ||
                        call.status == CallHistoryItem.CallStatus.NO_ANSWER
            }

            private fun isFailed(call: CallHistoryItem): Boolean {
                return call.status == CallHistoryItem.CallStatus.REJECTED ||
                        call.status == CallHistoryItem.CallStatus.NO_ACTION ||
                        call.status == CallHistoryItem.CallStatus.UNKNOWN
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
    }
}
