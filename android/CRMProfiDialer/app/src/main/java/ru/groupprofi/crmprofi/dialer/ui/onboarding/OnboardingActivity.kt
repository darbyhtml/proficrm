package ru.groupprofi.crmprofi.dialer.ui.onboarding

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import ru.groupprofi.crmprofi.dialer.MainActivity
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.domain.AppReadinessChecker
import ru.groupprofi.crmprofi.dialer.logs.AppLogger
import java.util.concurrent.atomic.AtomicInteger

/**
 * Умный onboarding - мастер первого запуска.
 * Проводит пользователя через необходимые шаги настройки.
 */
class OnboardingActivity : AppCompatActivity() {
    
    private lateinit var titleText: TextView
    private lateinit var messageText: TextView
    private lateinit var primaryButton: Button
    private lateinit var secondaryButton: Button
    
    private var currentStep: OnboardingStep = OnboardingStep.INTRO
    private val permissionRequestCode = AtomicInteger(100)
    
    companion object {
        const val EXTRA_START_STEP = "start_step"
        const val PREFS_NAME = "onboarding_prefs"
        const val KEY_COMPLETED = "onboarding_completed"
        const val KEY_LAST_STEP = "onboarding_last_step"
    }
    
    /**
     * Шаги onboarding.
     */
    enum class OnboardingStep {
        INTRO,          // Зачем приложение
        PERMISSIONS,    // Разрешения
        NOTIFICATIONS,  // Уведомления
        FINAL           // Готово
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Проверяем, нужно ли показывать onboarding
        val startStepName = intent.getStringExtra(EXTRA_START_STEP)
        if (startStepName != null) {
            try {
                currentStep = OnboardingStep.valueOf(startStepName)
            } catch (e: Exception) {
                currentStep = OnboardingStep.INTRO
            }
        } else {
            // Определяем начальный шаг на основе текущего состояния
            currentStep = determineInitialStep()
        }
        
        setContentView(R.layout.activity_onboarding)
        
        initViews()
        updateStep(currentStep)
    }
    
    private fun initViews() {
        titleText = findViewById(R.id.onboardingTitle)
        messageText = findViewById(R.id.onboardingMessage)
        primaryButton = findViewById(R.id.onboardingPrimaryButton)
        secondaryButton = findViewById(R.id.onboardingSecondaryButton)
        
        primaryButton.setOnClickListener { handlePrimaryAction() }
        secondaryButton.setOnClickListener { handleSecondaryAction() }
    }
    
    /**
     * Определить начальный шаг на основе состояния приложения.
     */
    private fun determineInitialStep(): OnboardingStep {
        val checker = AppReadinessChecker(this)
        val state = checker.checkReadiness()
        
        return when (state) {
            AppReadinessChecker.ReadyState.NEEDS_PERMISSIONS -> OnboardingStep.PERMISSIONS
            AppReadinessChecker.ReadyState.NEEDS_NOTIFICATIONS -> OnboardingStep.NOTIFICATIONS
            AppReadinessChecker.ReadyState.READY -> OnboardingStep.FINAL
            else -> OnboardingStep.INTRO
        }
    }
    
    /**
     * Обновить UI для текущего шага.
     */
    private fun updateStep(step: OnboardingStep) {
        currentStep = step
        
        val stepData = getStepData(step)
        
        titleText.text = stepData.title
        messageText.text = stepData.message
        primaryButton.text = stepData.primaryActionText
        secondaryButton.text = stepData.secondaryActionText
        secondaryButton.visibility = if (stepData.showSecondaryButton) android.view.View.VISIBLE else android.view.View.GONE
        
        // Сохраняем текущий шаг
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putString(KEY_LAST_STEP, step.name)
            .apply()
    }
    
    /**
     * Данные для шага.
     */
    private data class StepData(
        val title: String,
        val message: String,
        val primaryActionText: String,
        val secondaryActionText: String,
        val showSecondaryButton: Boolean
    )
    
    /**
     * Получить данные для шага.
     */
    private fun getStepData(step: OnboardingStep): StepData {
        return when (step) {
            OnboardingStep.INTRO -> StepData(
                title = getString(R.string.onboarding_intro_title),
                message = getString(R.string.onboarding_intro_message),
                primaryActionText = getString(R.string.onboarding_button_understand),
                secondaryActionText = "",
                showSecondaryButton = false
            )
            
            OnboardingStep.PERMISSIONS -> {
                val hasCallLog = hasPermission(android.Manifest.permission.READ_CALL_LOG)
                val hasPhoneState = hasPermission(android.Manifest.permission.READ_PHONE_STATE)
                val allGranted = hasCallLog && hasPhoneState
                
                if (allGranted) {
                    // Разрешения уже есть - пропускаем шаг
                    return@getStepData getStepData(OnboardingStep.NOTIFICATIONS)
                }
                
                val deniedPermanently = isPermissionDeniedPermanently(android.Manifest.permission.READ_CALL_LOG) ||
                        isPermissionDeniedPermanently(android.Manifest.permission.READ_PHONE_STATE)
                
                StepData(
                    title = getString(R.string.onboarding_permissions_title),
                    message = getString(R.string.onboarding_permissions_message) + "\n\n" +
                            if (deniedPermanently) {
                                getString(R.string.onboarding_permissions_denied)
                            } else {
                                ""
                            },
                    primaryActionText = if (deniedPermanently) getString(R.string.onboarding_button_open_settings) else getString(R.string.onboarding_button_allow),
                    secondaryActionText = if (deniedPermanently) "" else getString(R.string.onboarding_button_later),
                    showSecondaryButton = !deniedPermanently
                )
            }
            
            OnboardingStep.NOTIFICATIONS -> {
                val notificationsEnabled = NotificationManagerCompat.from(this).areNotificationsEnabled()
                
                if (notificationsEnabled) {
                    // Уведомления включены - пропускаем шаг
                    return@getStepData getStepData(OnboardingStep.FINAL)
                }
                
                StepData(
                    title = getString(R.string.onboarding_notifications_title),
                    message = getString(R.string.onboarding_notifications_message),
                    primaryActionText = getString(R.string.onboarding_notifications_button),
                    secondaryActionText = getString(R.string.onboarding_button_later),
                    showSecondaryButton = true
                )
            }
            
            OnboardingStep.FINAL -> {
                val checker = AppReadinessChecker(this)
                val state = checker.checkReadiness()
                val isReady = state == AppReadinessChecker.ReadyState.READY
                
                StepData(
                    title = if (isReady) getString(R.string.onboarding_final_ready_title) else getString(R.string.onboarding_final_not_ready_title),
                    message = if (isReady) {
                        getString(R.string.onboarding_final_ready_message)
                    } else {
                        getString(R.string.onboarding_final_not_ready_message)
                    },
                    primaryActionText = getString(R.string.onboarding_button_start),
                    secondaryActionText = "",
                    showSecondaryButton = false
                )
            }
        }
    }
    
    /**
     * Обработать действие основной кнопки.
     */
    private fun handlePrimaryAction() {
        when (currentStep) {
            OnboardingStep.INTRO -> {
                // Переходим к проверке разрешений
                val nextStep = if (needsPermissions()) OnboardingStep.PERMISSIONS else OnboardingStep.NOTIFICATIONS
                updateStep(nextStep)
            }
            
            OnboardingStep.PERMISSIONS -> {
                val deniedPermanently = isPermissionDeniedPermanently(android.Manifest.permission.READ_CALL_LOG) ||
                        isPermissionDeniedPermanently(android.Manifest.permission.READ_PHONE_STATE)
                
                if (deniedPermanently) {
                    // Открываем настройки приложения
                    openAppSettings()
                } else {
                    // Запрашиваем разрешения
                    requestPermissions()
                }
            }
            
            OnboardingStep.NOTIFICATIONS -> {
                // Открываем настройки уведомлений
                openNotificationSettings()
            }
            
            OnboardingStep.FINAL -> {
                // Завершаем onboarding
                completeOnboarding()
            }
        }
    }
    
    /**
     * Обработать действие вторичной кнопки.
     */
    private fun handleSecondaryAction() {
        when (currentStep) {
            OnboardingStep.PERMISSIONS -> {
                // Пропускаем разрешения, переходим к уведомлениям
                val nextStep = if (needsNotifications()) OnboardingStep.NOTIFICATIONS else OnboardingStep.FINAL
                updateStep(nextStep)
            }
            
            OnboardingStep.NOTIFICATIONS -> {
                // Пропускаем уведомления, переходим к финалу
                updateStep(OnboardingStep.FINAL)
            }
            
            else -> {
                // Ничего не делаем
            }
        }
    }
    
    /**
     * Проверить, нужны ли разрешения.
     */
    private fun needsPermissions(): Boolean {
        return !hasPermission(android.Manifest.permission.READ_CALL_LOG) ||
                !hasPermission(android.Manifest.permission.READ_PHONE_STATE)
    }
    
    /**
     * Проверить, нужны ли уведомления.
     */
    private fun needsNotifications(): Boolean {
        if (Build.VERSION.SDK_INT >= 33) {
            if (!hasPermission(android.Manifest.permission.POST_NOTIFICATIONS)) {
                return true
            }
        }
        return !NotificationManagerCompat.from(this).areNotificationsEnabled()
    }
    
    /**
     * Проверить, есть ли разрешение.
     */
    private fun hasPermission(permission: String): Boolean {
        return ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED
    }
    
    /**
     * Проверить, запрещено ли разрешение навсегда.
     */
    private fun isPermissionDeniedPermanently(permission: String): Boolean {
        if (hasPermission(permission)) {
            return false
        }
        
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
            return !shouldShowRequestPermissionRationale(permission)
        }
        
        return false
    }
    
    /**
     * Запросить разрешения.
     */
    private fun requestPermissions() {
        val needed = mutableListOf<String>()
        
        if (!hasPermission(android.Manifest.permission.READ_CALL_LOG)) {
            needed.add(android.Manifest.permission.READ_CALL_LOG)
        }
        if (!hasPermission(android.Manifest.permission.READ_PHONE_STATE)) {
            needed.add(android.Manifest.permission.READ_PHONE_STATE)
        }
        
        if (Build.VERSION.SDK_INT >= 33) {
            if (!hasPermission(android.Manifest.permission.POST_NOTIFICATIONS)) {
                needed.add(android.Manifest.permission.POST_NOTIFICATIONS)
            }
        }
        
        if (needed.isNotEmpty()) {
            ActivityCompat.requestPermissions(
                this,
                needed.toTypedArray(),
                permissionRequestCode.getAndIncrement()
            )
        } else {
            // Все разрешения уже есть - переходим дальше
            val nextStep = if (needsNotifications()) OnboardingStep.NOTIFICATIONS else OnboardingStep.FINAL
            updateStep(nextStep)
        }
    }
    
    /**
     * Открыть настройки приложения.
     */
    private fun openAppSettings() {
        try {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                data = Uri.fromParts("package", packageName, null)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
        } catch (e: Exception) {
            AppLogger.e("OnboardingActivity", "Ошибка открытия настроек: ${e.message}", e)
            android.widget.Toast.makeText(this, "Откройте настройки приложения вручную", android.widget.Toast.LENGTH_LONG).show()
        }
    }
    
    /**
     * Открыть настройки уведомлений.
     */
    private fun openNotificationSettings() {
        try {
            val intent = Intent().apply {
                action = Settings.ACTION_APP_NOTIFICATION_SETTINGS
                putExtra(Settings.EXTRA_APP_PACKAGE, packageName)
                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            startActivity(intent)
        } catch (e: Exception) {
            AppLogger.e("OnboardingActivity", "Ошибка открытия настроек уведомлений: ${e.message}", e)
            android.widget.Toast.makeText(this, "Откройте настройки уведомлений вручную", android.widget.Toast.LENGTH_LONG).show()
        }
    }
    
    /**
     * Завершить onboarding.
     */
    private fun completeOnboarding() {
        getSharedPreferences(PREFS_NAME, MODE_PRIVATE)
            .edit()
            .putBoolean(KEY_COMPLETED, true)
            .putString(KEY_LAST_STEP, OnboardingStep.FINAL.name)
            .apply()
        
        AppLogger.i("OnboardingActivity", "Onboarding завершён")
        
        // Переходим в MainActivity
        val intent = Intent(this, MainActivity::class.java)
        startActivity(intent)
        finish()
    }
    
    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        
        val allGranted = grantResults.isNotEmpty() && grantResults.all { it == PackageManager.PERMISSION_GRANTED }
        
        if (allGranted) {
            // Разрешения получены - переходим дальше
            val nextStep = if (needsNotifications()) OnboardingStep.NOTIFICATIONS else OnboardingStep.FINAL
            updateStep(nextStep)
        } else {
            // Разрешения не получены - обновляем шаг (показываем кнопку "Открыть настройки" если нужно)
            updateStep(OnboardingStep.PERMISSIONS)
        }
    }
    
    override fun onResume() {
        super.onResume()
        
        // При возврате из настроек проверяем состояние и обновляем шаг
        when (currentStep) {
            OnboardingStep.PERMISSIONS -> {
                if (!needsPermissions()) {
                    // Разрешения получены - переходим дальше
                    val nextStep = if (needsNotifications()) OnboardingStep.NOTIFICATIONS else OnboardingStep.FINAL
                    updateStep(nextStep)
                } else {
                    // Обновляем UI (может быть теперь нужна кнопка "Открыть настройки")
                    updateStep(OnboardingStep.PERMISSIONS)
                }
            }
            
            OnboardingStep.NOTIFICATIONS -> {
                if (!needsNotifications()) {
                    // Уведомления включены - переходим к финалу
                    updateStep(OnboardingStep.FINAL)
                }
            }
            
            else -> {
                // Ничего не делаем
            }
        }
    }
}
