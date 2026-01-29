package ru.groupprofi.crmprofi.dialer

import android.app.Application
import android.os.Build
import android.os.StrictMode
import android.os.Trace
import android.util.Log
import android.view.Choreographer
import java.util.concurrent.Executors
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.logs.LogCollector
import ru.groupprofi.crmprofi.dialer.logs.LogInterceptor
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import ru.groupprofi.crmprofi.dialer.support.CrashLogStore
import ru.groupprofi.crmprofi.dialer.BuildConfig

/**
 * Application класс для хранения глобальных объектов (LogCollector).
 */
class CRMApplication : Application() {
    val logCollector = LogCollector()
    private val applicationScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    
    override fun onCreate() {
        super.onCreate()
        
        // В debug режиме включаем StrictMode для обнаружения блокировок main thread
        if (BuildConfig.DEBUG) {
            enableStrictMode()
        }
        
        Trace.beginSection("CRMApplication.onCreate")
        
        try {
            // Устанавливаем глобальный обработчик необработанных исключений (легкая операция)
            setupCrashHandler()
            
            // Настраиваем LogInterceptor для автоматического сбора логов (легкая операция)
            LogInterceptor.setCollector(logCollector)
            
            // Инициализируем единый AppLogger (легкая операция - только настройка)
            AppLogger.initialize(this, enableFileLogging = true)
            
            // Тяжелые операции откладываем на фоновый поток после первого кадра (Dispatchers.IO)
            Choreographer.getInstance().postFrameCallback {
                Trace.beginSection("CRMApplication.initBackground")
                applicationScope.launch(Dispatchers.IO) {
                    try {
                        // Сначала TokenManager (EncryptedSharedPreferences / Tink) — только на IO
                        ru.groupprofi.crmprofi.dialer.auth.TokenManager.init(this@CRMApplication)
                        AppLogger.i("CRMApplication", "TokenManager initialized on background thread")
                        // Затем контейнер зависимостей (использует TokenManager.getInstance())
                        ru.groupprofi.crmprofi.dialer.core.AppContainer.init(this@CRMApplication)
                        AppLogger.i("CRMApplication", "AppContainer initialized on background thread")
                    } catch (e: Exception) {
                        AppLogger.e("CRMApplication", "Failed to initialize: ${e.message}", e)
                    } finally {
                        Trace.endSection()
                    }
                }
                Trace.endSection()
            }
            
            AppLogger.i("CRMApplication", "Application started, AppLogger initialized")
        } finally {
            Trace.endSection()
        }
    }
    
    /**
     * Включить StrictMode в debug режиме для обнаружения блокировок main thread.
     * Нарушения от OEM/вендора (например com.transsion.* при Surface/ViewRoot) не логируем,
     * чтобы не гоняться за ложными целями — только наш пакет ru.groupprofi.
     */
    private fun enableStrictMode() {
        val ourPackage = "ru.groupprofi"
        val executor = Executors.newSingleThreadExecutor()
        StrictMode.setThreadPolicy(
            StrictMode.ThreadPolicy.Builder()
                .detectDiskReads()
                .detectDiskWrites()
                .detectNetwork()
                .penaltyListener(executor) { violation ->
                    val fromOurCode = violation.stackTrace.any { element ->
                        element.className?.startsWith(ourPackage) == true
                    }
                    if (fromOurCode) {
                        Log.w("StrictMode", "Policy violation (our code)", violation)
                    }
                    // OEM/системные нарушения (transsion, surfaceflinger и т.д.) не логируем
                }
                .build()
        )

        StrictMode.setVmPolicy(
            StrictMode.VmPolicy.Builder()
                .detectLeakedSqlLiteObjects()
                .detectLeakedClosableObjects()
                .penaltyLog()
                .build()
        )
    }
    
    /**
     * Настроить глобальный обработчик необработанных исключений.
     */
    private fun setupCrashHandler() {
        val defaultHandler = Thread.getDefaultUncaughtExceptionHandler()
        
        Thread.setDefaultUncaughtExceptionHandler { thread, exception ->
            try {
                // Собираем информацию о сбое (лёгкие операции на потоке краша)
                val exceptionClass = exception.javaClass.simpleName
                val message = exception.message
                val stacktrace = exception.stackTraceToString()
                
                // Сохраняем в CrashLogStore в фоне, чтобы не блокировать поток краша (disk I/O)
                val appRef = this
                Executors.newSingleThreadExecutor().execute {
                    try {
                        CrashLogStore.saveCrash(appRef, exceptionClass, message, stacktrace)
                    } catch (_: Exception) { /* игнорируем */ }
                }
                
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
