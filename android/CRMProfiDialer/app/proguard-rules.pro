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

# ============================================
# JSON (org.json)
# ============================================
# org.json обычно не требует keep, но если будут warnings - закрыть точечно
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

# Исключения: можно обфусцировать внутренние детали, но сохранить публичные API
-keepclassmembers class ru.groupprofi.crmprofi.dialer.MainActivity { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.CallListenerService { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.OnboardingActivity { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.auth.TokenManager { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.network.ApiClient { *; }
-keepclassmembers class ru.groupprofi.crmprofi.dialer.queue.QueueManager { *; }

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

# ============================================
# Warnings (игнорируем известные безопасные предупреждения)
# ============================================
-dontwarn javax.annotation.**
-dontwarn org.jetbrains.annotations.**
-dontwarn org.conscrypt.**