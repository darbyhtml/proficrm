package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.os.Bundle
import android.widget.Button
import androidx.appcompat.app.AppCompatActivity

/**
 * Экран онбординга, показывается один раз при первом запуске приложения.
 * Объясняет пользователю:
 * - Как работает приложение
 * - Зачем нужны разрешения
 * - Что такое рабочие часы
 */
class OnboardingActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        
        // Проверяем, был ли уже показан онбординг
        val prefs = MainActivity.securePrefs(this)
        val onboardingShown = prefs.getBoolean("onboarding_shown", false)
        
        if (onboardingShown) {
            // Онбординг уже был показан - сразу переходим в MainActivity
            val intent = Intent(this, MainActivity::class.java)
            startActivity(intent)
            finish()
            return
        }
        
        // Показываем онбординг
        setContentView(R.layout.activity_onboarding)

        val continueBtn = findViewById<Button>(R.id.onboardingContinueBtn)
        continueBtn.setOnClickListener {
            // Сохраняем флаг, что онбординг был показан
            prefs.edit()
                .putBoolean("onboarding_shown", true)
                .apply()
            
            // Переходим в MainActivity
            val intent = Intent(this, MainActivity::class.java)
            startActivity(intent)
            finish()
        }
    }
}
