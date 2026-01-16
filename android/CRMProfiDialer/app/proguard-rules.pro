# ============================================
# ProGuard rules for CRMProfiDialer
# ============================================

# ============================================
# Room Database
# ============================================
# Room использует reflection для создания DAO и работы с Entity
-keep class * extends androidx.room.RoomDatabase { *; }
-keep @androidx.room.Entity class * { *; }
-keep @androidx.room.Dao class * { *; }
-keep @androidx.room.Database class * { *; }

# Сохраняем все поля и методы в Entity и DAO
-keepclassmembers class * {
    @androidx.room.* <fields>;
    @androidx.room.* <methods>;
}

# Room утилиты (используются для миграций и валидации)
-keep class androidx.room.util.** { *; }
-dontwarn androidx.room.**

# Наши Room классы (явно указываем для надежности)
-keep class ru.groupprofi.crmprofi.dialer.queue.** { *; }
-keep class ru.groupprofi.crmprofi.dialer.queue.QueueItem { *; }
-keep class ru.groupprofi.crmprofi.dialer.queue.QueueDao { *; }
-keep class ru.groupprofi.crmprofi.dialer.queue.AppDatabase { *; }

# Room: keep генерируемые классы
-keep class * extends androidx.room.RoomDatabase_Impl { *; }
-keep class * extends androidx.room.RoomDatabase_Impl$* { *; }

# ============================================
# Security Crypto (EncryptedSharedPreferences / Tink)
# ============================================
# androidx.security.crypto использует Google Tink для шифрования
-keep class androidx.security.crypto.** { *; }
-keep class com.google.crypto.tink.** { *; }
-dontwarn com.google.crypto.tink.**
-dontwarn androidx.security.crypto.**

# ============================================
# OkHttp / Okio
# ============================================
# OkHttp обычно не требует keep rules, но на всякий случай
-dontwarn okhttp3.**
-dontwarn okio.**
-dontwarn javax.annotation.**
-dontwarn org.conscrypt.**

# OkHttp Interceptors
-keep class ru.groupprofi.crmprofi.dialer.network.** { *; }

# ============================================
# Kotlin
# ============================================
# Kotlin metadata нужен для reflection и coroutines
-keep class kotlin.Metadata { *; }
-keep class kotlin.coroutines.** { *; }
-keep class kotlinx.coroutines.** { *; }
-dontwarn kotlinx.coroutines.**

# Kotlin data classes (используются в Room Entity)
-keepclassmembers class * {
    *** component*();
}

# Kotlin Coroutines (дополнительно)
-keepnames class kotlinx.coroutines.internal.MainDispatcherFactory
-keepnames class kotlinx.coroutines.CoroutineExceptionHandler
-keepclassmembernames class kotlinx.** {
    volatile <fields>;
}
-keepclassmembernames class kotlinx.coroutines.** {
    volatile <fields>;
}

# ============================================
# JSON (org.json)
# ============================================
# org.json обычно не требует keep, но если будут warnings - закрыть точечно
-keep class org.json.** { *; }
-dontwarn org.json.**

# ============================================
# AndroidX / Support Libraries
# ============================================
-keep class androidx.** { *; }
-keep interface androidx.** { *; }
-dontwarn androidx.**

# ============================================
# Наши классы (для надежности)
# ============================================
# Сохраняем наши основные классы от обфускации (для отладки и стабильности)
-keep class ru.groupprofi.crmprofi.dialer.** { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.** {
    *;
}

# Keep Application класс
-keep class ru.groupprofi.crmprofi.dialer.CRMApplication { *; }

# Keep все Activity, Service, Receiver
-keep public class * extends android.app.Activity
-keep public class * extends android.app.Service
-keep public class * extends android.content.BroadcastReceiver
-keep public class * extends android.content.ContentProvider

# Keep Intent actions (ACTION_START, ACTION_STOP и т.д.)
-keepclassmembers class ru.groupprofi.crmprofi.dialer.CallListenerService {
    public static final java.lang.String ACTION_START;
    public static final java.lang.String ACTION_STOP;
}

# Keep ContentObserver (для CallLogObserverManager)
-keep class ru.groupprofi.crmprofi.dialer.data.CallLogObserverManager { *; }
-keep class ru.groupprofi.crmprofi.dialer.data.CallLogObserverManager$* { *; }

# Keep NotificationManager
-keep class ru.groupprofi.crmprofi.dialer.notifications.** { *; }

# Keep domain models
-keep class ru.groupprofi.crmprofi.dialer.domain.** { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.domain.** {
    <fields>;
    <methods>;
}

# ============================================
# BuildConfig
# ============================================
# BuildConfig используется для BASE_URL и DEBUG флагов
-keep class ru.groupprofi.crmprofi.dialer.BuildConfig { *; }

# ============================================
# Общие правила
# ============================================
# Сохраняем аннотации (Room, Parcelize и т.д.)
-keepattributes *Annotation*
-keepattributes Signature
-keepattributes Exceptions
-keepattributes InnerClasses
-keepattributes EnclosingMethod

# Сохраняем строки для Room SQL запросов
-keepclassmembers class * {
    @androidx.room.Query <methods>;
}

# Не обфусцируем имена классов в исключениях (для отладки)
-keepnames class * extends java.lang.Exception

# Сохраняем нативные методы
-keepclasseswithmembernames class * {
    native <methods>;
}

# Keep Parcelable
-keep class * implements android.os.Parcelable {
    public static final android.os.Parcelable$Creator *;
}

# Keep Serializable
-keepclassmembers class * implements java.io.Serializable {
    static final long serialVersionUID;
    private static final java.io.ObjectStreamField[] serialPersistentFields;
    private void writeObject(java.io.ObjectOutputStream);
    private void readObject(java.io.ObjectInputStream);
    java.lang.Object writeReplace();
    java.lang.Object readResolve();
}

# Keep все классы с аннотациями Android
-keep @androidx.annotation.Keep class *
-keepclassmembers class * {
    @androidx.annotation.Keep *;
}

# ============================================
# zxing (QR scanner)
# ============================================
-keep class com.journeyapps.barcodescanner.** { *; }
-dontwarn com.journeyapps.barcodescanner.**

# ============================================
# Remove logging в release (опционально)
# ============================================
# Удаляем системные логи, но оставляем AppLogger (он маскирует данные)
-assumenosideeffects class android.util.Log {
    public static *** d(...);
    public static *** v(...);
    public static *** i(...);
}

# Но оставляем AppLogger (он маскирует данные)
-keep class ru.groupprofi.crmprofi.dialer.logs.AppLogger { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.logs.AppLogger { *; }

# ============================================
# Warnings (игнорируем известные безопасные предупреждения)
# ============================================
-dontwarn javax.annotation.**
-dontwarn org.jetbrains.annotations.**
-dontwarn org.conscrypt.**
