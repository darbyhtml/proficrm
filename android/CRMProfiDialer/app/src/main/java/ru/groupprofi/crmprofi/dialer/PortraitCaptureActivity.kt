package ru.groupprofi.crmprofi.dialer

import android.content.pm.ActivityInfo
import com.journeyapps.barcodescanner.CaptureActivity
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Кастомная CaptureActivity для zxing, фиксированная в портретной ориентации.
 * Используется для QR-сканера, чтобы камера всегда отображалась правильно.
 *
 * Защита от повторной инициализации:
 * - Ориентация устанавливается один раз за lifecycle (orientationSet), чтобы не дергать
 *   setRequestedOrientation при каждом onResume и не провоцировать лишний init камеры.
 * - При onDestroy сбрасываем флаги, чтобы при следующем открытии Activity камера инициализировалась корректно.
 */
class PortraitCaptureActivity : CaptureActivity() {
    private val orientationSet = AtomicBoolean(false)

    override fun onResume() {
        super.onResume()
        // Фиксируем портретную ориентацию только один раз за lifecycle (не при каждом onResume)
        if (!orientationSet.getAndSet(true)) {
            requestedOrientation = ActivityInfo.SCREEN_ORIENTATION_PORTRAIT
        }
    }

    override fun onPause() {
        super.onPause()
    }

    override fun onDestroy() {
        orientationSet.set(false)
        super.onDestroy()
    }
}
