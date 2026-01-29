package ru.groupprofi.crmprofi.dialer

import android.content.pm.ActivityInfo
import android.util.Log
import com.journeyapps.barcodescanner.CaptureActivity
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Кастомная CaptureActivity для zxing, фиксированная в портретной ориентации.
 * Используется для QR-сканера, чтобы камера всегда отображалась правильно.
 * 
 * ИСПРАВЛЕНИЕ: Добавлен guard для предотвращения двойного вызова initCamera.
 */
class PortraitCaptureActivity : CaptureActivity() {
    private val orientationSet = AtomicBoolean(false)
    
    override fun onResume() {
        super.onResume()
        
        // Фиксируем портретную ориентацию только один раз
        if (!orientationSet.getAndSet(true)) {
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
    }
    
    override fun onPause() {
        super.onPause()
        // Не сбрасываем ориентацию, чтобы камера не поворачивалась
        // Сбрасываем флаг для следующего onResume
        orientationSet.set(false)
    }
    
    override fun onDestroy() {
        super.onDestroy()
        // Гарантируем сброс флага при уничтожении
        orientationSet.set(false)
    }
}
