package ru.groupprofi.crmprofi.dialer.ui.help

import android.os.Build
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.fragment.app.Fragment
import ru.groupprofi.crmprofi.dialer.R

/**
 * Фрагмент с инструкциями по настройке работы в фоне для разных OEM (Xiaomi, Huawei, Samsung).
 * Показывается из SettingsFragment или при проблемах с батареей.
 */
class OemHelpFragment : Fragment() {
    
    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_oem_help, container, false)
    }
    
    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        
        val manufacturer = Build.MANUFACTURER.lowercase()
        val instructionsText = view.findViewById<TextView>(R.id.instructionsText)
        
        val instructions = when {
            manufacturer.contains("xiaomi") || manufacturer.contains("redmi") -> getXiaomiInstructions()
            manufacturer.contains("huawei") || manufacturer.contains("honor") -> getHuaweiInstructions()
            manufacturer.contains("samsung") -> getSamsungInstructions()
            else -> getGenericInstructions()
        }
        
        instructionsText.text = instructions
    }
    
    private fun getXiaomiInstructions(): String {
        return """
            Настройка работы в фоне для Xiaomi/MIUI:
            
            1. Настройки → Приложения → Управление разрешениями
            2. Найдите "CRM ПРОФИ" → Автозапуск → Включить
            3. Настройки → Батарея → Оптимизация батареи
            4. Найдите "CRM ПРОФИ" → Не оптимизировать
            5. Настройки → Батарея → Ограничение фоновой активности
            6. Найдите "CRM ПРОФИ" → Без ограничений
            
            Важно: На некоторых версиях MIUI может потребоваться дополнительно:
            - Настройки → Приложения → CRM ПРОФИ → Другие разрешения → Показывать поверх других окон
        """.trimIndent()
    }
    
    private fun getHuaweiInstructions(): String {
        return """
            Настройка работы в фоне для Huawei/Honor:
            
            1. Настройки → Приложения → Запуск приложений
            2. Найдите "CRM ПРОФИ" → Управление вручную
            3. Включите "Автозапуск"
            4. Настройки → Батарея → Запуск приложений
            5. Найдите "CRM ПРОФИ" → Управление вручную
            6. Включите "Автозапуск" и "Фоновые действия"
            
            Дополнительно:
            - Настройки → Батарея → Защищенные приложения
            - Добавьте "CRM ПРОФИ" в список защищенных
        """.trimIndent()
    }
    
    private fun getSamsungInstructions(): String {
        return """
            Настройка работы в фоне для Samsung:
            
            1. Настройки → Приложения → CRM ПРОФИ
            2. Батарея → Не оптимизировать
            3. Настройки → Батарея → Фоновые ограничения
            4. Найдите "CRM ПРОФИ" → Не ограничивать
            
            Для Android 12+:
            - Настройки → Батарея → Фоновые ограничения
            - Найдите "CRM ПРОФИ" → Не ограничивать
        """.trimIndent()
    }
    
    private fun getGenericInstructions(): String {
        return """
            Настройка работы в фоне:
            
            1. Настройки → Батарея → Оптимизация батареи
            2. Найдите "CRM ПРОФИ" в списке
            3. Выберите "Не оптимизировать" или "Разрешить"
            
            Альтернативный путь:
            - Настройки → Приложения → CRM ПРОФИ → Батарея
            - Выберите "Неограниченное использование батареи"
            
            Если пункт не найден:
            - Настройки → Приложения → CRM ПРОФИ
            - Найдите раздел "Батарея" или "Энергопотребление"
            - Отключите оптимизацию для этого приложения
        """.trimIndent()
    }
}
