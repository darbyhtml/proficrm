"""
Django management command для smoke-тестирования скорости импорта AmoCRM.

Проверяет:
- Корректность работы bulk-методов
- Соблюдение rate limit (7 rps)
- Метрики производительности
"""

from django.core.management.base import BaseCommand, CommandError
from django.contrib.auth import get_user_model
from ui.models import AmoApiConfig
from amocrm.client import AmoClient
from amocrm.migrate import migrate_filtered
import time

User = get_user_model()


class Command(BaseCommand):
    help = "Smoke-тест импорта AmoCRM: проверка скорости и метрик"

    def add_arguments(self, parser):
        parser.add_argument(
            "--responsible-user-id",
            type=int,
            required=True,
            help="ID ответственного пользователя в AmoCRM",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Количество компаний для импорта (по умолчанию 5)",
        )
        parser.add_argument(
            "--custom-field-id",
            type=int,
            help="ID кастомного поля для фильтрации (опционально)",
        )
        parser.add_argument(
            "--custom-value",
            type=str,
            default="Новая CRM",
            help="Значение кастомного поля (по умолчанию 'Новая CRM')",
        )
        parser.add_argument(
            "--skip-field-filter",
            action="store_true",
            help="Импортировать все компании ответственного без фильтра по полю",
        )

    def handle(self, *args, **options):
        responsible_user_id = options["responsible_user_id"]
        limit = options["limit"]
        custom_field_id = options.get("custom_field_id") or 0
        custom_value = options.get("custom_value") or "Новая CRM"
        skip_field_filter = options.get("skip_field_filter", False)

        # Загружаем конфигурацию AmoCRM
        cfg = AmoApiConfig.load()
        if not cfg.domain:
            raise CommandError("AmoCRM domain не настроен. Проверьте настройки в админке.")

        try:
            client = AmoClient(cfg)
        except Exception as e:
            raise CommandError(f"Ошибка создания клиента AmoCRM: {e}")

        # Получаем первого пользователя для actor
        actor = User.objects.first()
        if not actor:
            raise CommandError("В системе нет пользователей. Создайте хотя бы одного пользователя.")

        self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
        self.stdout.write(self.style.SUCCESS("SMOKE-ТЕСТ ИМПОРТА AMOCRM"))
        self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))
        self.stdout.write(f"Параметры теста:")
        self.stdout.write(f"  - Ответственный (amo): {responsible_user_id}")
        self.stdout.write(f"  - Лимит компаний: {limit}")
        self.stdout.write(f"  - Кастомное поле: {custom_field_id if custom_field_id else 'не используется'}")
        self.stdout.write(f"  - Значение поля: {custom_value}")
        self.stdout.write(f"  - Без фильтра по полю: {skip_field_filter}")
        self.stdout.write(f"  - Actor: {actor.username} (id={actor.id})\n")

        # Получаем метаданные полей
        try:
            from amocrm.migrate import fetch_company_custom_fields
            fields = fetch_company_custom_fields(client)
            self.stdout.write(f"Получено {len(fields)} кастомных полей компаний\n")
        except Exception as e:
            self.stdout.write(self.style.WARNING(f"Не удалось получить кастомные поля: {e}"))
            fields = []

        # Запускаем dry-run импорт
        start_time = time.time()
        self.stdout.write(self.style.SUCCESS("Запускаем DRY-RUN импорт...\n"))

        try:
            result = migrate_filtered(
                client=client,
                actor=actor,
                responsible_user_id=responsible_user_id,
                sphere_field_id=custom_field_id,
                sphere_option_id=None,
                sphere_label=custom_value if custom_field_id else None,
                limit_companies=limit,
                offset=0,
                dry_run=True,  # Только проверка, без записи в БД
                import_tasks=True,
                import_notes=True,
                import_contacts=True,  # Включаем контакты для полного теста
                company_fields_meta=fields,
                skip_field_filter=skip_field_filter,
            )

            elapsed_time = time.time() - start_time
            metrics = client.get_metrics()

            # Выводим результаты
            self.stdout.write(self.style.SUCCESS(f"\n{'=' * 80}"))
            self.stdout.write(self.style.SUCCESS("РЕЗУЛЬТАТЫ ТЕСТА"))
            self.stdout.write(self.style.SUCCESS(f"{'=' * 80}\n"))

            self.stdout.write(f"⏱ Время выполнения: {elapsed_time:.2f} сек\n")

            self.stdout.write(f"📊 API-метрики:")
            self.stdout.write(f"  - Всего запросов: {metrics['request_count']}")
            self.stdout.write(f"  - Средний RPS: {metrics['avg_rps']:.2f}")
            if metrics['avg_rps'] > 7.5:
                self.stdout.write(self.style.WARNING(f"  ⚠️ RPS превышает лимит 7 rps!"))
            else:
                self.stdout.write(self.style.SUCCESS(f"  ✅ RPS в пределах лимита\n"))

            self.stdout.write(f"🏢 Компании:")
            self.stdout.write(f"  - Найдено: {result.companies_seen}")
            self.stdout.write(f"  - Соответствует фильтру: {result.companies_matched}")
            self.stdout.write(f"  - В пачке: {result.companies_batch}")
            self.stdout.write(f"  - Будет создано: {result.companies_created}")
            self.stdout.write(f"  - Будет обновлено: {result.companies_updated}\n")

            self.stdout.write(f"📋 Задачи:")
            self.stdout.write(f"  - Найдено: {result.tasks_seen}")
            self.stdout.write(f"  - Будет создано: {result.tasks_created}")
            self.stdout.write(f"  - Будет обновлено: {result.tasks_updated}")
            self.stdout.write(f"  - Пропущено (старые): {result.tasks_skipped_old}")
            self.stdout.write(f"  - Пропущено (существующие): {result.tasks_skipped_existing}\n")

            self.stdout.write(f"📝 Заметки:")
            self.stdout.write(f"  - Найдено: {result.notes_seen}")
            self.stdout.write(f"  - Будет создано: {result.notes_created}")
            self.stdout.write(f"  - Будет обновлено: {result.notes_updated}")
            self.stdout.write(f"  - Пропущено (существующие): {result.notes_skipped_existing}\n")

            self.stdout.write(f"👤 Контакты:")
            self.stdout.write(f"  - Найдено: {result.contacts_seen}")
            self.stdout.write(f"  - Будет создано: {result.contacts_created}\n")

            if result.error:
                self.stdout.write(self.style.ERROR(f"\n❌ Ошибка импорта: {result.error}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"\n✅ Импорт выполнен успешно!"))

            # Проверяем, что bulk-методы использовались
            if metrics['request_count'] > 0:
                companies_per_request = result.companies_seen / metrics['request_count'] if metrics['request_count'] > 0 else 0
                self.stdout.write(f"\n📈 Эффективность:")
                self.stdout.write(f"  - Компаний на запрос: {companies_per_request:.2f}")
                if companies_per_request < 1.0 and result.companies_seen > 10:
                    self.stdout.write(self.style.WARNING(f"  ⚠️ Много запросов на компанию - возможно, bulk-методы не используются"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"  ✅ Bulk-методы работают эффективно"))

        except Exception as e:
            import traceback
            self.stdout.write(self.style.ERROR(f"\n❌ Ошибка при выполнении теста: {e}"))
            self.stdout.write(self.style.ERROR(f"Traceback:\n{traceback.format_exc()}"))
            raise CommandError(f"Тест не пройден: {e}")
