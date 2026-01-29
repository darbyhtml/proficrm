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
        
        // Фиксируем портретную ориентацию только один раз за lifecycle
        // НЕ сбрасываем флаг в onPause(), чтобы избежать повторной инициализации при быстрых переходах
        if (!orientationSet.getAndSet(true)) {
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
    }
    
    override fun onPause() {
        super.onPause()
        // НЕ сбрасываем флаг здесь, чтобы избежать повторной инициализации при быстрых переходах
        // Флаг сбрасывается только в onDestroy()
    }
    
    override fun onDestroy() {
        super.onDestroy()
        // Гарантируем сброс флага при уничтожении Activity
        orientationSet.set(false)
    }
}
