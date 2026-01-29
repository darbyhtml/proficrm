package ru.groupprofi.crmprofi.dialer.ui.login

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import android.widget.TextView
import androidx.activity.result.ActivityResultLauncher
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import ru.groupprofi.crmprofi.dialer.MainActivity
import ru.groupprofi.crmprofi.dialer.QRLoginActivity
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.auth.TokenManager

/**
 * Экран входа в приложение.
 * Только QR-код, без логина/пароля.
 * TokenManager не инициализируется на main thread — только в coroutine на IO.
 */
class LoginActivity : AppCompatActivity() {

    private lateinit var qrLoginButton: Button
    private lateinit var titleText: TextView
    private lateinit var messageText: TextView
    private lateinit var qrLoginLauncher: ActivityResultLauncher<Intent>

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_login)

        qrLoginLauncher = registerForActivityResult(ActivityResultContracts.StartActivityForResult()) {
            if (TokenManager.getInstanceOrNull()?.hasTokens() == true) {
                startMainActivity()
                finish()
            }
        }

        initViews()
        setupListeners()

        // Инициализация TokenManager и проверка токенов только в фоне — без disk I/O на main thread
        lifecycleScope.launch {
            val tm = TokenManager.init(applicationContext)
            withContext(Dispatchers.Main) {
                if (tm.hasTokens()) {
                    startMainActivity()
                    finish()
                    return@withContext
                }
                // Уже отрисован экран логина (initViews/setupListeners выше)
            }
        }
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
        qrLoginLauncher.launch(intent)
    }

    private fun startMainActivity() {
        val intent = Intent(this, MainActivity::class.java)
        intent.flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_NEW_TASK
        startActivity(intent)
        finish()
    }

    companion object {
        // requestCode не нужен: Activity Result API
    }
}
