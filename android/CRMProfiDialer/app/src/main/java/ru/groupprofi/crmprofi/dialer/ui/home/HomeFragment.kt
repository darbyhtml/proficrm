package ru.groupprofi.crmprofi.dialer.ui.home

import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.lifecycleScope
import androidx.lifecycle.repeatOnLifecycle
import com.google.android.material.button.MaterialButton
import com.google.android.material.card.MaterialCardView
import com.google.android.material.dialog.MaterialAlertDialogBuilder
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.core.AppContainer
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryItem
import ru.groupprofi.crmprofi.dialer.domain.CallStatsUseCase
import ru.groupprofi.crmprofi.dialer.network.PullCallMetrics
import java.util.concurrent.TimeUnit

/**
 * Фрагмент главной вкладки — дашборд состояния и статистики.
 */
class HomeFragment : Fragment() {
    // Warning‑баннер и карточка батареи
    private lateinit var warningBanner: View
    private lateinit var warningSubtitle: TextView
    private lateinit var batteryCard: MaterialCardView
    private lateinit var batteryButton: MaterialButton

    // Статистика
    private lateinit var statsPeriodButton: MaterialButton
    private lateinit var statsTotalValue: TextView
    private lateinit var statsSuccessValue: TextView
    private lateinit var statsNotCompletedValue: TextView
    private lateinit var statsSystemIssuesValue: TextView
    private lateinit var statsTalkTimeValue: TextView

    // Синхронизация
    private lateinit var syncText: TextView

    private val callHistoryStore = AppContainer.callHistoryStore
    private val statsUseCase = CallStatsUseCase()

    private var latestCalls: List<CallHistoryItem> = emptyList()
    private var latestExtendedStats: CallStatsUseCase.ExtendedCallStats =
        CallStatsUseCase.ExtendedCallStats.EMPTY
    private var currentPeriod: CallStatsUseCase.Period = CallStatsUseCase.Period.TODAY
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_home, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        warningBanner = view.findViewById(R.id.homeWarningBanner)
        warningSubtitle = view.findViewById(R.id.homeWarningSubtitle)
        batteryCard = view.findViewById(R.id.batteryCard)
        batteryButton = view.findViewById(R.id.batterySettingsButton)

        statsPeriodButton = view.findViewById(R.id.statsPeriodInput)
        statsTotalValue = view.findViewById(R.id.statsTotalValue)
        statsSuccessValue = view.findViewById(R.id.statsSuccessValue)
        statsNotCompletedValue = view.findViewById(R.id.statsNotCompletedValue)
        statsSystemIssuesValue = view.findViewById(R.id.statsSystemIssuesValue)
        statsTalkTimeValue = view.findViewById(R.id.statsTalkTimeValue)

        syncText = view.findViewById(R.id.syncText)

        setupBatteryCard()
        setupStatsPeriodDropdown()
        setupReactiveSubscriptions()
        startSyncRowUpdates()
        updateBatteryUi()
    }

    override fun onResume() {
        super.onResume()
        // После возврата из системных настроек актуализируем состояние батареи.
        updateBatteryUi()
    }

    /**
     * Периодически обновляем строку «Синхронизировано с CRM · N мин назад».
     */
    private fun startSyncRowUpdates() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                while (true) {
                    updateSyncText()
                    delay(5000)
                }
            }
        }
    }

    private fun setupReactiveSubscriptions() {
        lifecycleScope.launch {
            repeatOnLifecycle(Lifecycle.State.STARTED) {
                callHistoryStore.callsFlow.collectLatest { calls ->
                    latestCalls = calls
                    updateStats()
                }
            }
        }
    }

    private fun setupBatteryCard() {
        batteryButton.setOnClickListener {
            (activity as? ru.groupprofi.crmprofi.dialer.MainActivity)?.openBatteryOptimizationSettings()
        }
    }

    private fun setupStatsPeriodDropdown() {
        val items = listOf(
            getString(R.string.stats_period_today),
            getString(R.string.stats_period_week),
            getString(R.string.stats_period_month)
        )

        // Текущий период всегда отображаем текстом на кнопке.
        fun labelFor(period: CallStatsUseCase.Period): String = when (period) {
            CallStatsUseCase.Period.TODAY -> items[0]
            CallStatsUseCase.Period.LAST_7_DAYS -> items[1]
            CallStatsUseCase.Period.LAST_30_DAYS -> items[2]
            CallStatsUseCase.Period.ALL -> items[0]
        }
        statsPeriodButton.text = labelFor(currentPeriod)

        statsPeriodButton.setOnClickListener {
            MaterialAlertDialogBuilder(requireContext())
                .setItems(items.toTypedArray()) { _, position ->
                    currentPeriod = when (position) {
                        0 -> CallStatsUseCase.Period.TODAY
                        1 -> CallStatsUseCase.Period.LAST_7_DAYS
                        2 -> CallStatsUseCase.Period.LAST_30_DAYS
                        else -> CallStatsUseCase.Period.TODAY
                    }
                    statsPeriodButton.text = items[position]
                    updateStats()
                }
                .show()
        }
    }

    private fun updateStats() {
        val extended = statsUseCase.calculateExtended(latestCalls, currentPeriod)
        latestExtendedStats = extended

        statsTotalValue.text = extended.totalCalls.toString()
        statsSuccessValue.text = extended.successfulCalls.toString()
        statsNotCompletedValue.text = extended.notCompletedCalls.toString()
        statsSystemIssuesValue.text = extended.systemIssuesCalls.toString()
        statsTalkTimeValue.text = formatDuration(extended.totalTalkDurationSec)
    }

    private fun formatDuration(totalSeconds: Int): String {
        if (totalSeconds <= 0) return "0ч 0м"
        val hours = TimeUnit.SECONDS.toHours(totalSeconds.toLong()).toInt()
        val minutes = TimeUnit.SECONDS.toMinutes(totalSeconds.toLong()).toInt() % 60
        return "${hours}ч ${minutes}м"
    }

    private fun updateBatteryUi() {
        val optimizationEnabled = isBatteryOptimizationEnabled()
        if (optimizationEnabled) {
            warningBanner.visibility = View.VISIBLE
            batteryCard.visibility = View.VISIBLE
            warningSubtitle.text = getString(R.string.home_warning_problems_count, 1)
        } else {
            warningBanner.visibility = View.GONE
            batteryCard.visibility = View.GONE
        }
    }

    private fun isBatteryOptimizationEnabled(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false
        return try {
            val pm = requireContext().getSystemService(PowerManager::class.java)
            // Если НЕ игнорируем оптимизацию — значит, она включена и нам есть, что настроить.
            pm?.isIgnoringBatteryOptimizations(requireContext().packageName) != true
        } catch (_: Exception) {
            false
        }
    }

    private fun updateSyncText() {
        val seconds = PullCallMetrics.getSecondsSinceLastCommand()
        val text = if (seconds == null) {
            getString(R.string.status_last_command_never)
        } else {
            val human = when {
                seconds < 60 -> "${seconds} сек назад"
                seconds < 3600 -> "${seconds / 60} мин назад"
                else -> "${seconds / 3600} ч назад"
            }
            getString(R.string.status_last_command, human)
        }
        syncText.text = text
    }

    // Экспорт статистики больше не используется — данные смотрим в CRM.
}
