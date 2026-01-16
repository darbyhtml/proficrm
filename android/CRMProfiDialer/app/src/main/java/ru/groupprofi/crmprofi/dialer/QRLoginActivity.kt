package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanOptions
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.network.ApiClient

/**
 * Activity для входа по QR-коду.
 * Сканирует QR-код и обменивает токен на JWT access/refresh.
 */
class QRLoginActivity : AppCompatActivity() {
    private lateinit var tokenManager: TokenManager
    private lateinit var apiClient: ApiClient
    
    private val deviceId: String by lazy {
        android.provider.Settings.Secure.getString(contentResolver, android.provider.Settings.Secure.ANDROID_ID) ?: "unknown"
    }
    
    private val qrScannerLauncher = registerForActivityResult(ScanContract()) { result ->
        if (result.contents == null) {
            // Пользователь отменил сканирование
            finish()
            return@registerForActivityResult
        }
        
        val qrToken = result.contents.trim()
        if (qrToken.isEmpty()) {
            showError("QR-код пустой")
            return@registerForActivityResult
        }
        
        // Обмениваем QR-токен на JWT
        handleQrToken(qrToken)
    }
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Фиксируем вертикальную ориентацию программно ДО setContentView
        requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        
        tokenManager = TokenManager.getInstance(this)
        apiClient = ApiClient.getInstance(this)
        
        // Проверяем, не вошли ли уже
        if (tokenManager.hasTokens()) {
            Toast.makeText(this, "Вы уже вошли в систему", Toast.LENGTH_SHORT).show()
            finish()
            return
        }
        
        // Запускаем сканер QR-кода
        startQrScanner()
    }
    
    override fun onResume() {
        super.onResume()
        // Фиксируем ориентацию при каждом возобновлении
        requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    }
    
    override fun onPause() {
        super.onPause()
        // НЕ сбрасываем ориентацию в onPause, чтобы камера не поворачивалась
    }
    
    override fun onDestroy() {
        super.onDestroy()
        // Сбрасываем ориентацию только при полном закрытии
        requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_UNSPECIFIED
    }
    
    private fun startQrScanner() {
        // Фиксируем ориентацию ДО создания опций сканера
        requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        
        val options = ScanOptions()
        options.setDesiredBarcodeFormats("QR_CODE")
        options.setPrompt("Наведите камеру на QR-код")
        options.setCameraId(0)
        options.setBeepEnabled(false)
        options.setBarcodeImageEnabled(false)
        options.setOrientationLocked(true) // Фиксируем вертикальную ориентацию
        // Используем кастомную PortraitCaptureActivity для правильной ориентации камеры
        options.setCaptureActivity(PortraitCaptureActivity::class.java)
        
        qrScannerLauncher.launch(options)
    }
    
    override fun onConfigurationChanged(newConfig: android.content.res.Configuration) {
        super.onConfigurationChanged(newConfig)
        // Принудительно фиксируем портретную ориентацию при любых изменениях конфигурации
        requestedOrientation = android.content.pm.ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    }
    
    private fun handleQrToken(qrToken: String) {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("QRLoginActivity", "QR token exchange attempt")
                val result = apiClient.exchangeQrToken(qrToken)
                
                when (result) {
                    is ApiClient.Result.Success -> {
                        val qrResult = result.data
                        val access = qrResult.access
                        val refresh = qrResult.refresh
                        val username = qrResult.username
                        val isAdmin = qrResult.isAdmin
                        
                        // Сохраняем токены и роль
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.i("QRLoginActivity", "QR login success: username=$username, isAdmin=$isAdmin")
                        tokenManager.saveTokens(access, refresh, username)
                        tokenManager.saveDeviceId(deviceId)
                        tokenManager.saveIsAdmin(isAdmin)
                        
                        // Регистрация устройства (не критична)
                        apiClient.registerDevice(deviceId, android.os.Build.MODEL ?: "Android")
                        
                        runOnUiThread {
                            Toast.makeText(this@QRLoginActivity, "Вход выполнен успешно", Toast.LENGTH_SHORT).show()
                            
                            // Возвращаемся в MainActivity с результатом успеха
                            // Используем FLAG_ACTIVITY_CLEAR_TOP чтобы пересоздать MainActivity и вызвать onResume
                            val intent = Intent(this@QRLoginActivity, MainActivity::class.java)
                            intent.flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
                            startActivity(intent)
                            finish()
                        }
                    }
                    is ApiClient.Result.Error -> {
                        ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("QRLoginActivity", "QR login failed: ${result.message}")
                        runOnUiThread {
                            val errorMsg = result.message.ifEmpty { "Ошибка обмена QR-кода" }
                            showError(errorMsg)
                        }
                    }
                }
            } catch (e: Exception) {
                ru.groupprofi.crmprofi.dialer.logs.AppLogger.e("QRLoginActivity", "QR login error", e)
                runOnUiThread {
                    showError("Ошибка: ${e.message}")
                }
            }
        }
    }
    
    
    private fun showError(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        // Даем время прочитать сообщение, затем закрываем
        android.os.Handler(android.os.Looper.getMainLooper()).postDelayed({
            finish()
        }, 3000)
    }
}
