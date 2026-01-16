package ru.groupprofi.crmprofi.dialer.queue

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase
import androidx.room.migration.Migration
import androidx.sqlite.db.SupportSQLiteDatabase

/**
 * База данных Room для оффлайн-очереди.
 * Версия 1: начальная схема с таблицей queue_items.
 */
@Database(
    entities = [QueueItem::class],
    version = 1,
    exportSchema = false
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun queueDao(): QueueDao
    
    companion object {
        @Volatile
        private var INSTANCE: AppDatabase? = null
        
        /**
         * Миграция с версии 0 на 1 (для новых установок Room создаст схему автоматически).
         * Для будущих версий здесь будут добавляться новые миграции.
         */
        private val MIGRATION_0_1 = object : Migration(0, 1) {
            override fun migrate(db: SupportSQLiteDatabase) {
                // Создание таблицы queue_items
                db.execSQL("""
                    CREATE TABLE IF NOT EXISTS queue_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT NOT NULL,
                        type TEXT NOT NULL,
                        payload TEXT NOT NULL,
                        endpoint TEXT NOT NULL,
                        method TEXT NOT NULL DEFAULT 'POST',
                        retryCount INTEGER NOT NULL DEFAULT 0,
                        createdAt INTEGER NOT NULL,
                        lastRetryAt INTEGER
                    )
                """.trimIndent())
            }
        }
        
        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "crmprofi_queue_db"
                )
                    .addMigrations(MIGRATION_0_1)
                    // НЕ используем fallbackToDestructiveMigration - это опасно для production
                    // При отсутствии миграции Room выбросит исключение, что безопаснее чем потеря данных
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
