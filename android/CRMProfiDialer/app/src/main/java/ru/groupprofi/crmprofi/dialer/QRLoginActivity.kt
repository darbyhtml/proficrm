package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.journeyapps.barcodescanner.ScanContract
import com.journeyapps.barcodescanner.ScanIntentIntegrator
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
    
    private fun startQrScanner() {
        val options = ScanOptions()
        options.setDesiredBarcodeFormats(ScanIntentIntegrator.QR_CODE)
        options.setPrompt("Наведите камеру на QR-код")
        options.setCameraId(0)
        options.setBeepEnabled(false)
        options.setBarcodeImageEnabled(false)
        options.setOrientationLocked(false)
        
        qrScannerLauncher.launch(options)
    }
    
    private fun handleQrToken(qrToken: String) {
        CoroutineScope(Dispatchers.IO).launch {
            try {
                val result = apiClient.exchangeQrToken(qrToken)
                
                when (result) {
                    is ApiClient.Result.Success -> {
                        val (access, refresh, username) = result.data
                        
                        // Сохраняем токены
                        tokenManager.saveTokens(access, refresh, username)
                        tokenManager.saveDeviceId(deviceId)
                        
                        // Регистрация устройства (не критична)
                        apiClient.registerDevice(deviceId, android.os.Build.MODEL ?: "Android")
                        
                        runOnUiThread {
                            Toast.makeText(this@QRLoginActivity, "Вход выполнен успешно", Toast.LENGTH_SHORT).show()
                            
                            // Возвращаемся в MainActivity с результатом успеха
                            val intent = Intent(this@QRLoginActivity, MainActivity::class.java)
                            intent.flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
                            startActivity(intent)
                            finish()
                        }
                    }
                    is ApiClient.Result.Error -> {
                        runOnUiThread {
                            val errorMsg = result.message ?: "Ошибка обмена QR-кода"
                            showError(errorMsg)
                        }
                    }
                }
            } catch (e: Exception) {
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
