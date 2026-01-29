package ru.groupprofi.crmprofi.dialer.core

import android.content.Context
import ru.groupprofi.crmprofi.dialer.data.CallHistoryRepository
import ru.groupprofi.crmprofi.dialer.data.PendingCallManager
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessProvider
import ru.groupprofi.crmprofi.dialer.domain.CallHistoryStore
import ru.groupprofi.crmprofi.dialer.domain.PendingCallStore
import ru.groupprofi.crmprofi.dialer.network.ApiClient
import ru.groupprofi.crmprofi.dialer.notifications.AppNotificationManager
import ru.groupprofi.crmprofi.dialer.recovery.AutoRecoveryManager
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.core.CallFlowCoordinator

/**
 * Контейнер зависимостей приложения (простой Service Locator без DI-фреймворка).
 * Предоставляет единую точку доступа к основным компонентам приложения.
 * UI использует только интерфейсы из domain, не зная о реализациях.
 */
object AppContainer {
    @Volatile
    private var initialized = false
    
    private lateinit var appContext: Context
    
    // Domain интерфейсы (UI использует только их)
    lateinit var callHistoryStore: CallHistoryStore
    lateinit var pendingCallStore: PendingCallStore
    lateinit var readinessProvider: AppReadinessProvider
    
    // Data реализации (внутренние, UI не должен знать о них напрямую)
    private lateinit var callHistoryRepository: CallHistoryRepository
    private lateinit var pendingCallManager: PendingCallManager
    private lateinit var appReadinessChecker: AppReadinessChecker
    
    // Инфраструктура
    lateinit var apiClient: ApiClient
    lateinit var tokenManager: TokenManager
    lateinit var notificationManager: AppNotificationManager
    lateinit var autoRecoveryManager: AutoRecoveryManager
    lateinit var callFlowCoordinator: CallFlowCoordinator
    
    /**
     * Инициализировать контейнер (вызывается из Application).
     */
    fun init(context: Context) {
        if (initialized) {
            return
        }
        
        appContext = context.applicationContext

        // Инициализируем инфраструктуру (TokenManager уже инициализирован в Application)
        tokenManager = TokenManager.getInstance()
        apiClient = ApiClient.getInstance(appContext)
        notificationManager = AppNotificationManager.getInstance(appContext)
        
        // Инициализируем data реализации
        callHistoryRepository = CallHistoryRepository.getInstance(appContext)
        pendingCallManager = PendingCallManager.getInstance(appContext)
        appReadinessChecker = AppReadinessChecker(appContext)
        
        // Инициализируем domain интерфейсы (UI использует только их)
        callHistoryStore = callHistoryRepository
        pendingCallStore = pendingCallManager
        readinessProvider = appReadinessChecker
        
        // Инициализируем координаторы и менеджеры
        autoRecoveryManager = AutoRecoveryManager.getInstance(appContext)
        callFlowCoordinator = CallFlowCoordinator.getInstance(appContext)
        
        initialized = true
    }
    
    /**
     * Проверить, инициализирован ли контейнер.
     */
    fun isInitialized(): Boolean = initialized
}
