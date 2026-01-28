"""
Сброс блокировки импорта amoCRM для указанного пользователя или всех пользователей.

Используется, если блокировка импорта "зависла" и не позволяет запустить новый импорт.
"""
from django.core.cache import cache
from django.core.management.base import BaseCommand
from accounts.models import User


class Command(BaseCommand):
    help = "Сброс блокировки импорта amoCRM (ключ amocrm_import_run:{user_id})"

    def add_arguments(self, parser):
        parser.add_argument(
            "--user-id",
            type=int,
            help="ID пользователя для сброса блокировки (если не указан, сбрасывает для всех)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Сбросить блокировки для всех пользователей",
        )

    def handle(self, *args, **options):
        user_id = options.get("user_id")
        reset_all = options.get("all", False)

        if user_id:
            # Сброс для конкретного пользователя
            lock_key = f"amocrm_import_run:{user_id}"
            try:
                user = User.objects.get(id=user_id)
                cache.delete(lock_key)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"✓ Блокировка импорта сброшена для пользователя: {user.username} (ID: {user_id})"
                    )
                )
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.ERROR(f"✗ Пользователь с ID {user_id} не найден")
                )
            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"✗ Ошибка при сбросе блокировки: {e}")
                )
        elif reset_all:
            # Сброс для всех пользователей
            deleted_count = 0
            # Пробуем удалить блокировки для всех пользователей (до 1000)
            for uid in range(1, 1001):
                lock_key = f"amocrm_import_run:{uid}"
                if cache.get(lock_key) is not None:
                    cache.delete(lock_key)
                    deleted_count += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"✓ Сброшено блокировок импорта: {deleted_count}"
                )
            )
        else:
            # Показываем текущие блокировки
            self.stdout.write("Текущие блокировки импорта:")
            found_any = False
            for uid in range(1, 1001):
                lock_key = f"amocrm_import_run:{uid}"
                raw = cache.get(lock_key)
                if raw is not None:
                    found_any = True
                    try:
                        user = User.objects.get(id=uid)
                        self.stdout.write(
                            f"  - Пользователь: {user.username} (ID: {uid}), ключ: {lock_key}"
                        )
                    except User.DoesNotExist:
                        self.stdout.write(f"  - Пользователь ID: {uid} (не найден), ключ: {lock_key}")
            
            if not found_any:
                self.stdout.write(self.style.SUCCESS("  Блокировок не найдено"))
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "\nДля сброса блокировки используйте:\n"
                        "  python manage.py reset_amocrm_import_lock --user-id <ID>\n"
                        "или для всех:\n"
                        "  python manage.py reset_amocrm_import_lock --all"
                    )
                )
