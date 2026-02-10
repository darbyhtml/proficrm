package ru.groupprofi.crmprofi.dialer.ui.settings

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.PowerManager
import android.provider.Settings
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.fragment.app.Fragment
import com.google.android.material.bottomsheet.BottomSheetDialog
import ru.groupprofi.crmprofi.dialer.BuildConfig
import ru.groupprofi.crmprofi.dialer.R
import ru.groupprofi.crmprofi.dialer.auth.TokenManager
import ru.groupprofi.crmprofi.dialer.ui.support.SupportHealthActivity

/**
 * Фрагмент вкладки "Настройки" — соответствует дизайну CRM Dialer.
 */
class SettingsFragment : Fragment() {

    private lateinit var rowUser: View
    private lateinit var rowLogout: View
    private lateinit var rowNotifications: View
    private lateinit var rowBattery: View
    private lateinit var rowAutostart: View
    private lateinit var rowManufacturer: View
    private lateinit var rowAbout: View
    private lateinit var rowDiagnostics: View

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?
    ): View? {
        return inflater.inflate(R.layout.fragment_settings, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)

        rowUser = view.findViewById(R.id.rowUser)
        rowLogout = view.findViewById(R.id.rowLogout)
        rowNotifications = view.findViewById(R.id.rowNotifications)
        rowBattery = view.findViewById(R.id.rowBattery)
        rowAutostart = view.findViewById(R.id.rowAutostart)
        rowManufacturer = view.findViewById(R.id.rowManufacturer)
        rowAbout = view.findViewById(R.id.rowAbout)
        rowDiagnostics = view.findViewById(R.id.rowDiagnostics)

        setupStaticRows()
        setupClickListeners()
    }

    override fun onResume() {
        super.onResume()
        // После возврата из системных настроек обновляем статусы.
        updateUserRow()
        updateNotificationsRow()
        updateBatteryRow()
        updateAutostartRow()
        updateManufacturerRow()
        updateAboutRow()
    }

    // region Row helpers

    private fun setupStaticRows() {
        // Titles and icons that не зависят от состояния.
        setRowTitle(rowUser, getString(R.string.settings_user_title))

        setRowTitle(rowLogout, getString(R.string.settings_logout_title))
        setRowSubtitle(rowLogout, getString(R.string.settings_logout_subtitle))
        styleLogoutRow(rowLogout)

        setRowTitle(rowNotifications, getString(R.string.settings_notifications_title))
        setRowHint(rowNotifications, getString(R.string.settings_notifications_hint))

        setRowTitle(rowBattery, getString(R.string.settings_battery_title))
        setRowHint(rowBattery, getString(R.string.settings_battery_hint))

        setRowTitle(rowAutostart, getString(R.string.settings_autostart_title))
        setRowHint(rowAutostart, getString(R.string.settings_autostart_hint))

        setRowTitle(rowManufacturer, getString(R.string.settings_manufacturer_title))
        setRowHint(rowManufacturer, getString(R.string.settings_manufacturer_hint))

        setRowTitle(rowAbout, getString(R.string.settings_about_title))
        setRowHint(rowAbout, getString(R.string.settings_about_hint))

        setRowTitle(rowDiagnostics, getString(R.string.settings_diagnostics_title))
        setRowStatus(rowDiagnostics, getString(R.string.settings_diagnostics_status))
        setRowHint(rowDiagnostics, getString(R.string.settings_diagnostics_hint))
    }

    private fun setupClickListeners() {
        rowUser.setOnClickListener { showUserDialog() }

        rowLogout.setOnClickListener {
            (activity as? ru.groupprofi.crmprofi.dialer.MainActivity)?.showLogoutConfirmation()
        }

        rowNotifications.setOnClickListener { openNotificationSettings() }

        rowBattery.setOnClickListener {
            (activity as? ru.groupprofi.crmprofi.dialer.MainActivity)?.openBatteryOptimizationSettings()
        }

        rowAutostart.setOnClickListener { openAutostartSettings() }

        rowManufacturer.setOnClickListener { showManufacturerInstructions() }

        rowAbout.setOnClickListener { showAboutDialog() }

        rowDiagnostics.setOnClickListener {
            startActivity(Intent(requireContext(), SupportHealthActivity::class.java))
        }
    }

    private fun updateUserRow() {
        val tokenManager = TokenManager.getInstanceOrNull()
        val hasTokens = tokenManager?.hasTokens() == true
        val email = tokenManager?.getUsername() ?: getString(R.string.settings_user_email_placeholder)

        setRowStatus(rowUser, email)
        val hintRes = if (hasTokens) {
            R.string.settings_user_status_authorized
        } else {
            R.string.settings_user_status_unauthorized
        }
        setRowHint(rowUser, getString(hintRes))
    }

    private fun updateNotificationsRow() {
        val context = requireContext()
        val notificationsEnabled = NotificationManagerCompat.from(context).areNotificationsEnabled()
        val hasPermission = if (Build.VERSION.SDK_INT >= 33) {
            ContextCompat.checkSelfPermission(
                context,
                android.Manifest.permission.POST_NOTIFICATIONS
            ) == android.content.pm.PackageManager.PERMISSION_GRANTED
        } else {
            true
        }

        val enabled = notificationsEnabled && hasPermission
        val status = if (enabled) {
            getString(R.string.settings_notifications_status_enabled)
        } else {
            getString(R.string.settings_notifications_status_disabled)
        }
        setRowStatus(rowNotifications, status)
    }

    private fun updateBatteryRow() {
        val optimizationEnabled = isBatteryOptimizationEnabled()
        if (optimizationEnabled) {
            setRowStatus(rowBattery, getString(R.string.settings_battery_status_needed))
            showRowWarning(rowBattery, true)
        } else {
            setRowStatus(rowBattery, getString(R.string.settings_battery_status_ok))
            showRowWarning(rowBattery, false)
        }
    }

    private fun updateAutostartRow() {
        val manufacturer = Build.MANUFACTURER.lowercase()
        val isProblematic = listOf(
            "xiaomi", "redmi", "poco",
            "huawei", "honor",
            "oppo", "vivo", "realme",
            "samsung", "oneplus"
        ).any { manufacturer.contains(it) }

        val statusRes = if (isProblematic) {
            R.string.settings_autostart_status_recommended
        } else {
            R.string.settings_autostart_status_generic
        }
        setRowStatus(rowAutostart, getString(statusRes))
    }

    private fun updateManufacturerRow() {
        val manufacturer = Build.MANUFACTURER.ifBlank { "Android" }
        val pretty = manufacturer.replaceFirstChar { if (it.isLowerCase()) it.titlecase() else it.toString() }
        setRowStatus(rowManufacturer, pretty)
    }

    private fun updateAboutRow() {
        val status = getString(
            R.string.settings_about_status,
            BuildConfig.VERSION_NAME,
            BuildConfig.VERSION_CODE
        )
        setRowStatus(rowAbout, status)
    }

    // endregion

    // region Row view helpers

    private fun setRowTitle(row: View, title: String) {
        row.findViewById<TextView>(R.id.settingsRowTitle).text = title
    }

    private fun setRowStatus(row: View, status: String) {
        row.findViewById<TextView>(R.id.settingsRowStatus).text = status
    }

    private fun setRowHint(row: View, hint: String) {
        row.findViewById<TextView>(R.id.settingsRowHint).text = hint
    }

    private fun setRowSubtitle(row: View, subtitle: String) {
        setRowStatus(row, subtitle)
    }

    private fun showRowWarning(row: View, show: Boolean) {
        val icon = row.findViewById<View>(R.id.settingsRowWarning)
        icon.visibility = if (show) View.VISIBLE else View.GONE
    }

    private fun styleLogoutRow(row: View) {
        row.setBackgroundColor(ContextCompat.getColor(requireContext(), R.color.settings_row_logout_bg))
        val titleView = row.findViewById<TextView>(R.id.settingsRowTitle)
        val statusView = row.findViewById<TextView>(R.id.settingsRowStatus)
        titleView.setTextColor(ContextCompat.getColor(requireContext(), R.color.error))
        statusView.setTextColor(ContextCompat.getColor(requireContext(), R.color.error))
    }

    // endregion

    // region Dialogs / navigation

    private fun showUserDialog() {
        val tokenManager = TokenManager.getInstanceOrNull()
        val email = tokenManager?.getUsername() ?: getString(R.string.settings_user_email_placeholder)

        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle(getString(R.string.settings_user_dialog_title))
            .setMessage(email)
            .setPositiveButton(getString(R.string.settings_user_dialog_copy)) { _, _ ->
                val clipboard = requireContext().getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                clipboard.setPrimaryClip(ClipData.newPlainText("email", email))
                Toast.makeText(requireContext(), "Скопировано", Toast.LENGTH_SHORT).show()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun openNotificationSettings() {
        val context = requireContext()
        try {
            val intent = Intent().apply {
                action = Settings.ACTION_APP_NOTIFICATION_SETTINGS
                putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName)
            }
            startActivity(intent)
        } catch (_: Exception) {
            // Fallback: настройки приложения
            try {
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:${context.packageName}")
                }
                startActivity(intent)
            } catch (_: Exception) {
                Toast.makeText(context, "Откройте настройки приложения вручную", Toast.LENGTH_LONG).show()
            }
        }
    }

    private fun openAutostartSettings() {
        val context = requireContext()
        val manufacturer = Build.MANUFACTURER.lowercase()

        fun tryStart(intent: Intent): Boolean {
            return try {
                startActivity(intent)
                true
            } catch (_: Exception) {
                false
            }
        }

        var opened = false

        if (manufacturer.contains("xiaomi") || manufacturer.contains("redmi") || manufacturer.contains("poco")) {
            opened = opened || tryStart(Intent("miui.intent.action.APP_PERM_EDITOR").apply {
                setClassName("com.miui.securitycenter", "com.miui.permcenter.permissions.PermissionsEditorActivity")
                putExtra("extra_pkgname", context.packageName)
            })
        }

        if (!opened && (manufacturer.contains("huawei") || manufacturer.contains("honor"))) {
            opened = opened || tryStart(Intent().apply {
                setClassName("com.huawei.systemmanager", "com.huawei.systemmanager.startupmgr.ui.StartupNormalAppListActivity")
            })
        }

        if (!opened && manufacturer.contains("samsung")) {
            opened = opened || tryStart(Intent().apply {
                setClassName("com.samsung.android.lool", "com.samsung.android.sm.battery.ui.BatteryActivity")
            })
        }

        // Fallback: настройки приложения, если ничего не открыли.
        if (!opened) {
            try {
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:${context.packageName}")
                }
                startActivity(intent)
            } catch (_: Exception) {
                // ignore
            }
        }

        Toast.makeText(
            context,
            getString(R.string.settings_autostart_toast),
            Toast.LENGTH_LONG
        ).show()
    }

    private fun showManufacturerInstructions() {
        val manufacturer = android.os.Build.MANUFACTURER.lowercase()
        val instructions = when {
            manufacturer.contains("xiaomi") || manufacturer.contains("redmi") -> getXiaomiInstructions()
            manufacturer.contains("huawei") || manufacturer.contains("honor") -> getHuaweiInstructions()
            manufacturer.contains("samsung") -> getSamsungInstructions()
            else -> getGenericInstructions()
        }

        val dialog = BottomSheetDialog(requireContext())
        val view = layoutInflater.inflate(R.layout.dialog_text_with_button, null, false)
        val titleView = view.findViewById<TextView>(R.id.dialogTitle)
        val messageView = view.findViewById<TextView>(R.id.dialogMessage)
        val button = view.findViewById<android.widget.Button>(R.id.dialogButton)

        titleView.text = getString(R.string.settings_manufacturer_dialog_title)
        messageView.text = instructions
        button.text = getString(R.string.settings_manufacturer_open_app_settings)
        button.setOnClickListener {
            dialog.dismiss()
            try {
                val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS).apply {
                    data = Uri.parse("package:${requireContext().packageName}")
                }
                startActivity(intent)
            } catch (_: Exception) {
                Toast.makeText(requireContext(), "Откройте настройки приложения вручную", Toast.LENGTH_LONG).show()
            }
        }

        dialog.setContentView(view)
        dialog.show()
    }
    
    private fun getXiaomiInstructions(): String {
        return "Настройка для Xiaomi/MIUI:\n\n" +
                "1. Настройки → Приложения → Управление разрешениями\n" +
                "2. Найдите \"CRM ПРОФИ\" → Автозапуск → Включить\n" +
                "3. Настройки → Батарея → Оптимизация батареи\n" +
                "4. Найдите \"CRM ПРОФИ\" → Не оптимизировать\n" +
                "5. Настройки → Батарея → Ограничение фоновой активности\n" +
                "6. Найдите \"CRM ПРОФИ\" → Без ограничений"
    }
    
    private fun getHuaweiInstructions(): String {
        return "Настройка для Huawei/Honor:\n\n" +
                "1. Настройки → Приложения → Запуск приложений\n" +
                "2. Найдите \"CRM ПРОФИ\" → Управление вручную → Включить \"Автозапуск\"\n" +
                "3. Настройки → Батарея → Запуск приложений\n" +
                "4. Найдите \"CRM ПРОФИ\" → Включить \"Автозапуск\" и \"Фоновые действия\"\n" +
                "5. Настройки → Батарея → Защищенные приложения\n" +
                "6. Добавьте \"CRM ПРОФИ\" в список защищенных"
    }
    
    private fun getSamsungInstructions(): String {
        return "Настройка для Samsung:\n\n" +
                "1. Настройки → Приложения → CRM ПРОФИ → Батарея → Не оптимизировать\n" +
                "2. Настройки → Батарея → Фоновые ограничения\n" +
                "3. Найдите \"CRM ПРОФИ\" → Не ограничивать"
    }
    
    private fun getGenericInstructions(): String {
        return "Настройка работы в фоне:\n\n" +
                "1. Настройки → Батарея → Оптимизация батареи\n" +
                "2. Найдите \"CRM ПРОФИ\" → Не оптимизировать\n\n" +
                "Альтернативно:\n" +
                "Настройки → Приложения → CRM ПРОФИ → Батарея → Неограниченное использование"
    }

    private fun showAboutDialog() {
        val version = getString(
            R.string.settings_about_status,
            BuildConfig.VERSION_NAME,
            BuildConfig.VERSION_CODE
        )

        androidx.appcompat.app.AlertDialog.Builder(requireContext())
            .setTitle(getString(R.string.settings_about_dialog_title))
            .setMessage(version)
            .setPositiveButton(getString(R.string.settings_about_play)) { _, _ ->
                openInPlayStore()
            }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun openInPlayStore() {
        val context = requireContext()
        val appPackageName = context.packageName
        try {
            startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("market://details?id=$appPackageName")))
        } catch (_: Exception) {
            try {
                startActivity(
                    Intent(
                        Intent.ACTION_VIEW,
                        Uri.parse("https://play.google.com/store/apps/details?id=$appPackageName")
                    )
                )
            } catch (_: Exception) {
                Toast.makeText(context, getString(R.string.settings_about_no_play), Toast.LENGTH_LONG).show()
            }
        }
    }

    // endregion

    private fun isBatteryOptimizationEnabled(): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.M) return false
        return try {
            val pm = requireContext().getSystemService(PowerManager::class.java)
            pm?.isIgnoringBatteryOptimizations(requireContext().packageName) != true
        } catch (_: Exception) {
            false
        }
    }
}
