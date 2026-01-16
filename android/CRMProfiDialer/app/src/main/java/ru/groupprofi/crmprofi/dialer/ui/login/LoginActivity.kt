package ru.groupprofi.crmprofi.dialer.ui.login

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import ru.groupprofi.crmprofi.dialer.MainActivity
import ru.groupprofi.crmprofi.dialer.QRLoginActivity
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.auth.TokenManager

/**
 * Экран входа в приложение.
 * Только QR-код, без логина/пароля.
 */
class LoginActivity : AppCompatActivity() {
    
    private lateinit var tokenManager: TokenManager
    private lateinit var qrLoginButton: Button
    private lateinit var titleText: TextView
    private lateinit var messageText: TextView
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        tokenManager = TokenManager.getInstance(this)
        
        // Если уже авторизован - переходим в главное приложение
        if (tokenManager.hasTokens()) {
            startMainActivity()
            finish()
            return
        }
        
        setContentView(R.layout.activity_login)
        
        initViews()
        setupListeners()
    }
    
    private fun initViews() {
        titleText = findViewById(R.id.loginTitle)
        messageText = findViewById(R.id.loginMessage)
        qrLoginButton = findViewById(R.id.loginQrButton)
    }
    
    private fun setupListeners() {
        qrLoginButton.setOnClickListener {
            startQrLogin()
        }
    }
    
    private fun startQrLogin() {
        val intent = Intent(this, QRLoginActivity::class.java)
        startActivityForResult(intent, REQ_QR_LOGIN)
    }
    
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        
        if (requestCode == REQ_QR_LOGIN) {
            // Проверяем, успешно ли выполнен вход
            if (tokenManager.hasTokens()) {
                startMainActivity()
                finish()
            }
        }
    }
    
    private fun startMainActivity() {
        val intent = Intent(this, MainActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
        startActivity(intent)
        finish()
    }
    
    companion object {
        private const val REQ_QR_LOGIN = 100
    }
}
