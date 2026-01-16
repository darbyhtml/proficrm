package ru.groupprofi.crmprofi.dialer

import android.app.Application
import ru.groupprofi.crmprofi.dialer.logs.LogCollector
import ru.groupprofi.crmprofi.dialer.logs.LogInterceptor
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.support.CrashLogStore

/**
 * Application класс для хранения глобальных объектов (LogCollector).
 */
class CRMApplication : Application() {
    val logCollector = LogCollector()
    
    override fun onCreate() {
        super.onCreate()
        
        // Устанавливаем глобальный обработчик необработанных исключений
        setupCrashHandler()
        
        // Настраиваем LogInterceptor для автоматического сбора логов
        LogInterceptor.setCollector(logCollector)
        
        // Инициализируем единый AppLogger
        AppLogger.initialize(this, enableFileLogging = true)
        
        // Инициализируем контейнер зависимостей
        ru.groupprofi.crmprofi.dialer.core.AppContainer.init(this)
        
        AppLogger.i("CRMApplication", "Application started, AppLogger initialized, AppContainer initialized")
    }
    
    /**
     * Настроить глобальный обработчик необработанных исключений.
     */
    private fun setupCrashHandler() {
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        
        Thread.setDefaultUncaughtExceptionHandler { thread, exception ->
            try {
                // Собираем информацию о сбое
                val exceptionClass = exception.javaClass.simpleName
                val message = exception.message
                val stacktrace = exception.stackTraceToString()
                
                // Сохраняем в CrashLogStore
                CrashLogStore.saveCrash(this, exceptionClass, message, stacktrace)
                
                // Логируем через AppLogger (если он уже инициализирован)
                try {
                    AppLogger.e("CRMApplication", "Необработанное исключение: $exceptionClass", exception)
                } catch (e: Exception) {
                    // Если AppLogger ещё не готов, используем системный Log
                    android.util.Log.e("CRMApplication", "Необработанное исключение: $exceptionClass", exception)
                }
            } catch (e: Exception) {
                // Если что-то пошло не так при сохранении сбоя, просто логируем
                android.util.Log.e("CRMApplication", "Ошибка при сохранении информации о сбое: ${e.message}", e)
            } finally {
                // Вызываем стандартный handler для стандартного поведения ОС
                defaultHandler?.uncaughtException(thread, exception)
            }
        }
    }
}
