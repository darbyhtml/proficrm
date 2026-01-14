package ru.groupprofi.crmprofi.dialer.logs

import android.util.Log

/**
 * Перехватчик логов для сбора в LogCollector.
 * Используется как обертка над android.util.Log.
 * 
 * Для автоматического сбора всех логов используйте функции из этого объекта
 * вместо прямых вызовов android.util.Log.
 */
object LogInterceptor {
    private var collector: LogCollector? = null
    
    fun setCollector(collector: LogCollector) {
        this.collector = collector
    }
    
    fun v(tag: String, msg: String): Int {
        collector?.addLog(Log.VERBOSE, tag, msg)
        return Log.v(tag, msg)
    }
    
    fun d(tag: String, msg: String): Int {
        collector?.addLog(Log.DEBUG, tag, msg)
        return Log.d(tag, msg)
    }
    
    fun i(tag: String, msg: String): Int {
        collector?.addLog(Log.INFO, tag, msg)
        return Log.i(tag, msg)
    }
    
    fun w(tag: String, msg: String): Int {
        collector?.addLog(Log.WARN, tag, msg)
        return Log.w(tag, msg)
    }
    
    fun e(tag: String, msg: String): Int {
        collector?.addLog(Log.ERROR, tag, msg)
        return Log.e(tag, msg)
    }
    
    fun e(tag: String, msg: String, tr: Throwable): Int {
        collector?.addLog(Log.ERROR, tag, "$msg\n${tr.stackTraceToString()}")
        return Log.e(tag, msg, tr)
    }
    
    /**
     * Явно добавить лог в коллектор (для случаев, когда используется android.util.Log напрямую).
     */
    fun addLog(level: Int, tag: String, message: String) {
        collector?.addLog(level, tag, message)
    }
}
