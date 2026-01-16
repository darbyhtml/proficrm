package ru.groupprofi.crmprofi.dialer

import android.content.pm.ActivityInfo
import com.journeyapps.barcodescanner.CaptureActivity

/**
 * Кастомная CaptureActivity для zxing, фиксированная в портретной ориентации.
 * Используется для QR-сканера, чтобы камера всегда отображалась правильно.
 */
class PortraitCaptureActivity : CaptureActivity() {
    override fun onResume() {
        super.onResume()
        // Фиксируем портретную ориентацию
        requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
    }
    
    override fun onPause() {
        super.onPause()
        // Не сбрасываем ориентацию, чтобы камера не поворачивалась
    }
}
