package ru.groupprofi.crmprofi.dialer.queue

import android.content.Context
import androidx.room.Database
import androidx.room.Room
import androidx.room.RoomDatabase

/**
 * База данных Room для оффлайн-очереди.
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
        
        fun getDatabase(context: Context): AppDatabase {
            return INSTANCE ?: synchronized(this) {
                val instance = Room.databaseBuilder(
                    context.applicationContext,
                    AppDatabase::class.java,
                    "crmprofi_queue_db"
                )
                    .fallbackToDestructiveMigration() // Для простоты: при изменении схемы пересоздаём БД
                    .build()
                INSTANCE = instance
                instance
            }
        }
    }
}
