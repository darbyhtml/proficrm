package ru.groupprofi.crmprofi.dialer.ui.home

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.Button
import android.widget.TextView
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.Lifecycle
import com.google.android.material.card.MaterialCardView
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.network.PullCallMetrics
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase

/**
 * Фрагмент главной вкладки - статус готовности и статистика.
 */
class HomeFragment : Fragment() {
    private lateinit var statusCard: MaterialCardView
    private lateinit var statusIcon: TextView
    private lateinit var statusText: TextView
    private lateinit var statusExplanation: TextView
    private lateinit var lastCommandText: TextView
    private lateinit var fixButton: Button
    
    private lateinit var todayTotal: TextView
    private lateinit var todaySuccess: TextView
    private lateinit var todayNoAnswer: TextView
    private lateinit var todayDropped: TextView
    private lateinit var todayPendingCrm: TextView
    private lateinit var todayStatsSkeleton: View
    
    private val readinessProvider = AppContainer.readinessProvider
    private val callHistoryStore = AppContainer.callHistoryStore
    private val pendingCallStore = AppContainer.pendingCallStore
    private val statsUseCase = CallStatsUseCase()
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_home, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        statusCard = view.findViewById(R.id.statusCard)
        statusIcon = view.findViewById(R.id.statusIcon)
        statusText = view.findViewById(R.id.statusText)
        statusExplanation = view.findViewById(R.id.statusExplanation)
        lastCommandText = view.findViewById(R.id.lastCommandText)
        fixButton = view.findViewById(R.id.fixButton)
        
        todayTotal = view.findViewById(R.id.todayTotal)
        todaySuccess = view.findViewById(R.id.todaySuccess)
        todayNoAnswer = view.findViewById(R.id.todayNoAnswer)
        todayDropped = view.findViewById(R.id.todayDropped)
        todayPendingCrm = view.findViewById(R.id.todayPendingCrm)
        todayStatsSkeleton = view.findViewById(R.id.todayStatsSkeleton)
        
        setupClickListeners()
        setupReactiveSubscriptions()
        startLastCommandUpdates()
        updateReadinessStatus()
    }

    override fun onResume() {
        super.onResume()
        // После возврата из настроек системы (батарея/разрешения) обновляем статус, чтобы кнопка «Исправить» скрылась
        updateReadinessStatus()
    }

    private fun startLastCommandUpdates() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    updateLastCommandText()
                    delay(5000)
                }
            }
        }
    }

    private fun updateLastCommandText() {
        val seconds = PullCallMetrics.getSecondsSinceLastCommand()
        if (seconds != null && readinessProvider.getState() == AppReadinessChecker.ReadyState.READY) {
            lastCommandText.visibility = View.VISIBLE
            lastCommandText.text = when {
                seconds < 60 -> getString(R.string.status_last_command, "${seconds} с назад")
                seconds < 3600 -> getString(R.string.status_last_command, "${seconds / 60} мин назад")
                else -> getString(R.string.status_last_command, "${seconds / 3600} ч назад")
            }
        } else {
            if (readinessProvider.getState() == AppReadinessChecker.ReadyState.READY) {
                lastCommandText.visibility = View.VISIBLE
                lastCommandText.text = getString(R.string.status_last_command_never)
            } else {
                lastCommandText.visibility = View.GONE
            }
        }
    }
    
    private fun setupClickListeners() {
        fixButton.setOnClickListener {
            val action = readinessProvider.getUiModel().fixActionType
            (activity as? ru.groupprofi.crmprofi.dialer.MainActivity)?.handleFixAction(action)
        }
    }
    
    private fun setupReactiveSubscriptions() {
        // Подписка на историю звонков для статистики
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.callsFlow.collectLatest { calls ->
                    updateTodayStats(calls)
                }
            }
        }
        
        // Подписка на активные ожидаемые звонки
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                pendingCallStore.hasActivePendingCallsFlow.collectLatest { _ ->
                    updateReadinessStatus()
                }
            }
        }
    }
    
    private fun updateReadinessStatus() {
        val state = readinessProvider.getState()
        val uiModel = readinessProvider.getUiModel()
        
        val hasResolvingCalls = pendingCallStore.hasActivePendingCallsFlow.value
        
        if (hasResolvingCalls && state == AppReadinessChecker.ReadyState.READY) {
            statusIcon.text = "🟡"
            statusIcon.contentDescription = getString(R.string.status_resolving)
            statusText.text = getString(R.string.status_resolving)
            statusExplanation.text = getString(R.string.status_explanation_resolving)
            lastCommandText.visibility = View.GONE
            fixButton.visibility = View.GONE
        } else {
            statusIcon.text = uiModel.iconEmoji
            statusIcon.contentDescription = uiModel.title
            statusText.text = uiModel.title
            statusExplanation.text = uiModel.message
            fixButton.visibility = if (uiModel.showFixButton) View.VISIBLE else View.GONE
            updateLastCommandText()
        }
    }
    
    private fun updateTodayStats(calls: List<ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem>) {
        todayStatsSkeleton.visibility = View.GONE
        val stats = statsUseCase.calculate(calls, CallStatsUseCase.Period.TODAY)
        todayTotal.text = stats.total.toString()
        todaySuccess.text = stats.success.toString()
        todayNoAnswer.text = stats.noAnswer.toString()
        todayDropped.text = stats.dropped.toString()
        if (stats.pendingCrm > 0) {
            todayPendingCrm.text = getString(R.string.stats_pending_crm, stats.pendingCrm)
            todayPendingCrm.visibility = View.VISIBLE
        } else {
            todayPendingCrm.visibility = View.GONE
        }
    }
}
