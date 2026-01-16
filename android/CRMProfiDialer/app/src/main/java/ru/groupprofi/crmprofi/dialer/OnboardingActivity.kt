package ru.groupprofi.crmprofi.dialer

import android.content.Intent
import android.content.SharedPreferences
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
        
        try {
            android.util.Log.d("OnboardingActivity", "onCreate: starting")
            
            // Проверяем, был ли уже показан онбординг
            val prefs: SharedPreferences = getSharedPreferences("onboarding", MODE_PRIVATE)
            val onboardingShown = prefs.getBoolean("onboarding_shown", false)
            android.util.Log.d("OnboardingActivity", "onCreate: onboardingShown=$onboardingShown")
            
            if (onboardingShown) {
                // Онбординг уже был показан - сразу переходим в MainActivity
                android.util.Log.d("OnboardingActivity", "onCreate: redirecting to MainActivity")
                val intent = Intent(this, MainActivity::class.java)
                startActivity(intent)
                finish()
                return
            }
            
            // Показываем онбординг
            android.util.Log.d("OnboardingActivity", "onCreate: showing onboarding")
            setContentView(R.layout.activity_onboarding)

            val continueBtn = findViewById<Button>(R.id.onboardingContinueBtn)
            if (continueBtn == null) {
                android.util.Log.e("OnboardingActivity", "onCreate: continueBtn is null!")
                finish()
                return
            }
            
            continueBtn.setOnClickListener {
                try {
                    android.util.Log.d("OnboardingActivity", "onClick: saving onboarding flag")
                    // Сохраняем флаг, что онбординг был показан
                    prefs.edit()
                        .putBoolean("onboarding_shown", true)
                        .apply()
                    
                    // Переходим в MainActivity
                    android.util.Log.d("OnboardingActivity", "onClick: starting MainActivity")
                    val intent = Intent(this, MainActivity::class.java)
                    startActivity(intent)
                    finish()
                } catch (e: Exception) {
                    android.util.Log.e("OnboardingActivity", "onClick error: ${e.message}", e)
                }
            }
        } catch (e: Exception) {
            android.util.Log.e("OnboardingActivity", "onCreate error: ${e.message}", e)
            android.widget.Toast.makeText(this, "Ошибка: ${e.message}", android.widget.Toast.LENGTH_LONG).show()
            finish()
        }
    }
}
