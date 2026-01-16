package ru.groupprofi.crmprofi.dialer

import android.app.Application
import ru.groupprofi.crmprofi.dialer.logs.LogCollector
import ru.groupprofi.crmprofi.dialer.logs.LogInterceptor
import ru.groupprofi.crmprofi.dialer.logs.AppLogger

/**
 * Application класс для хранения глобальных объектов (LogCollector).
 */
class CRMApplication : Application() {
    val logCollector = LogCollector()
    
    override fun onCreate() {
        super.onCreate()
        // Настраиваем LogInterceptor для автоматического сбора логов
        LogInterceptor.setCollector(logCollector)
        
        // Инициализируем единый AppLogger
        AppLogger.initialize(this, enableFileLogging = true)
        
        AppLogger.i("CRMApplication", "Application started, AppLogger initialized")
    }
}
