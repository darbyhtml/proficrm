package ru.groupprofi.crmprofi.dialer.ui

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.text.Editable
import android.text.TextWatcher
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.appcompat.widget.Toolbar
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.chip.Chip
import com.google.android.material.chip.ChipGroup
import com.google.android.material.textfield.TextInputEditText
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.flow.collectLatest
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.repeatOnLifecycle
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.domain.PendingCall
import java.util.*

/**
 * Экран истории звонков.
 * Показывает список всех звонков с человеческими статусами, фильтрами и поиском.
 */
class CallsHistoryActivity : AppCompatActivity() {
    private lateinit var recyclerView: RecyclerView
    private lateinit var adapter: CallsHistoryAdapter
    private lateinit var callHistoryStore: CallHistoryStore
    private lateinit var periodChipGroup: ChipGroup
    private lateinit var searchEditText: TextInputEditText
    private val statsUseCase = CallStatsUseCase()
    
    private var currentPeriod = CallStatsUseCase.Period.TODAY
    private var searchQuery = ""
    private var searchJob: Job? = null
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_calls_history)
        
        // Используем интерфейс через AppContainer
        callHistoryStore = AppContainer.callHistoryStore
        
        // Настраиваем Toolbar
        val toolbar = findViewById<Toolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        toolbar.setNavigationOnClickListener { finish() }
        
        recyclerView = findViewById(R.id.callsRecyclerView)
        periodChipGroup = findViewById(R.id.periodChipGroup)
        searchEditText = findViewById(R.id.searchEditText)
        
        adapter = CallsHistoryAdapter(emptyList()) { call ->
            // Обработчик действий для звонка
            handleCallAction(call)
        }
        recyclerView.layoutManager = LinearLayoutManager(this)
        recyclerView.adapter = adapter
        
        // Настраиваем фильтры периодов
        setupPeriodFilters()
        
        // Настраиваем поиск
        setupSearch()
        
        // Подписываемся на реактивный поток истории звонков
        setupReactiveSubscription()
    }
    
    /**
     * Настроить фильтры периодов.
     */
    private fun setupPeriodFilters() {
        periodChipGroup.setOnCheckedStateChangeListener { _, checkedIds ->
            val checkedId = checkedIds.firstOrNull() ?: return@setOnCheckedStateChangeListener
            
            currentPeriod = when (checkedId) {
                R.id.chipToday -> CallStatsUseCase.Period.TODAY
                R.id.chip7Days -> CallStatsUseCase.Period.LAST_7_DAYS
                R.id.chipAll -> CallStatsUseCase.Period.ALL
                else -> CallStatsUseCase.Period.TODAY
            }
            
            // Обновляем список
            updateFilteredCalls()
        }
    }
    
    /**
     * Настроить поиск с debounce.
     */
    private fun setupSearch() {
        searchEditText.addTextChangedListener(object : TextWatcher {
            override fun beforeTextChanged(s: CharSequence?, start: Int, count: Int, after: Int) {}
            override fun onTextChanged(s: CharSequence?, start: Int, before: Int, count: Int) {}
            
            override fun afterTextChanged(s: Editable?) {
                searchJob?.cancel()
                searchJob = lifecycleScope.launch {
                    delay(300) // Debounce 300ms
                    searchQuery = s?.toString()?.trim() ?: ""
                    updateFilteredCalls()
                }
            }
        })
    }
    
    /**
     * Настроить реактивную подписку на поток истории звонков.
     */
    private fun setupReactiveSubscription() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.callsFlow.collectLatest { _ ->
                    updateFilteredCalls()
                }
            }
        }
    }
    
    /**
     * Обновить отфильтрованный список звонков.
     */
    private fun updateFilteredCalls() {
        lifecycleScope.launch {
            // Получаем все звонки синхронно (из текущего значения Flow)
            val allCalls = callHistoryStore.callsFlow.value
            
            // Фильтруем по периоду
            val filteredByPeriod = statsUseCase.filterByPeriod(allCalls, currentPeriod)
            
            // Фильтруем по поисковому запросу
            val filtered = if (searchQuery.isBlank()) {
                filteredByPeriod
            } else {
                val normalizedQuery = ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer.normalize(searchQuery)
                filteredByPeriod.filter { call ->
                    val normalizedPhone = ru.groupprofi.crmprofi.dialer.domain.PhoneNumberNormalizer.normalize(call.phone)
                    normalizedPhone.contains(normalizedQuery, ignoreCase = true) ||
                    call.phoneDisplayName?.contains(searchQuery, ignoreCase = true) == true
                }
            }
            
            adapter.updateCalls(filtered)
        }
    }
    
    /**
     * Обработать действие для звонка (перезвонить или скопировать).
     */
    @Suppress("UNUSED_PARAMETER")
    private fun handleCallAction(_call: CallHistoryItem) {
        // Действия обрабатываются в адаптере через callback
    }
    
    /**
     * Адаптер для списка звонков.
     */
    private class CallsHistoryAdapter(
        private var calls: List<CallHistoryItem>,
        private val onActionClick: (CallHistoryItem) -> Unit
    ) : RecyclerView.Adapter<CallsHistoryAdapter.ViewHolder>() {
        
        fun updateCalls(newCalls: List<CallHistoryItem>) {
            calls = newCalls.sortedByDescending { it.startedAt } // Сначала новые
            notifyDataSetChanged()
        }
        
        override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): ViewHolder {
            val view = LayoutInflater.from(parent.context)
                .inflate(R.layout.item_call_history, parent, false)
            return ViewHolder(view, onActionClick)
        }
        
        override fun onBindViewHolder(holder: ViewHolder, position: Int) {
            val call = calls[position]
            holder.bind(call)
        }
        
        override fun getItemCount() = calls.size
        
        class ViewHolder(
            itemView: View,
            private val onActionClick: (CallHistoryItem) -> Unit
        ) : RecyclerView.ViewHolder(itemView) {
            private val phoneText: TextView = itemView.findViewById(R.id.phoneText)
            private val nameText: TextView = itemView.findViewById(R.id.nameText)
            private val statusText: TextView = itemView.findViewById(R.id.statusText)
            private val durationText: TextView = itemView.findViewById(R.id.durationText)
            private val dateText: TextView = itemView.findViewById(R.id.dateText)
            private val crmBadge: TextView = itemView.findViewById(R.id.crmBadge)
            private val copyButton: com.google.android.material.button.MaterialButton = itemView.findViewById(R.id.copyButton)
            private val callButton: com.google.android.material.button.MaterialButton = itemView.findViewById(R.id.callButton)
            
            fun bind(call: CallHistoryItem) {
                phoneText.text = call.phone
                nameText.text = call.phoneDisplayName ?: ""
                nameText.visibility = if (call.phoneDisplayName != null) View.VISIBLE else View.GONE
                
                statusText.text = call.getStatusText()
                durationText.text = call.getDurationText()
                dateText.text = call.getDateTimeText()
                
                crmBadge.text = call.getCrmBadgeText()
                // Цвет бейджа: зеленый если отправлено, оранжевый если ожидает
                val badgeColor = if (call.sentToCrm) {
                    0xFF10B981.toInt() // Зеленый
                } else {
                    0xFFF59E0B.toInt() // Оранжевый
                }
                crmBadge.setTextColor(badgeColor)
                
                // Обработчики действий
                copyButton.text = itemView.context.getString(R.string.action_copy)
                copyButton.setOnClickListener {
                    copyPhoneNumber(call.phone)
                }
                
                callButton.text = itemView.context.getString(R.string.action_call_back)
                callButton.setOnClickListener {
                    // ЭТАП 2: Используем CallFlowCoordinator для отслеживания actionSource = HISTORY
                    val coordinator = CallFlowCoordinator.getInstance(itemView.context)
                    coordinator.handleCallCommandFromHistory(call.phone, call.id)
                }
            }
            
            /**
             * Скопировать номер телефона в буфер обмена.
             */
            private fun copyPhoneNumber(phone: String) {
                val clipboard = itemView.context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                val clip = ClipData.newPlainText("Номер телефона", phone)
                clipboard.setPrimaryClip(clip)
                Toast.makeText(itemView.context, itemView.context.getString(R.string.copied), Toast.LENGTH_SHORT).show()
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
                    itemView.context.startActivity(dialIntent)
                } catch (e: Exception) {
                    Toast.makeText(itemView.context, "Не удалось открыть звонилку", Toast.LENGTH_SHORT).show()
                }
            }
        }
    }
}
