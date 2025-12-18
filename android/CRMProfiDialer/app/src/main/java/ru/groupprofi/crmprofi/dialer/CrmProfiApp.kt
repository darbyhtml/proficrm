package ru.groupprofi.crmprofi.dialer

import android.app.Application
import android.content.Context
import java.io.PrintWriter
import java.io.StringWriter

class CrmProfiApp : Application() {
    override fun onCreate() {
        super.onCreate()

        val prefs = getSharedPreferences(PREFS, Context.MODE_PRIVATE)
        val prev = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            try {
                val sw = StringWriter()
                e.printStackTrace(PrintWriter(sw))
                prefs.edit()
                    .putString(KEY_LAST_CRASH, sw.toString().take(8000))
                    .apply()
            } catch (_: Exception) {
            }
            prev?.uncaughtException(t, e)
        }
    }

    companion object {
        const val PREFS = "crmprofi_dialer"
        const val KEY_LAST_CRASH = "last_crash"
    }
}


