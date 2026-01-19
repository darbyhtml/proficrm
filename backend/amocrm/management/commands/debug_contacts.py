"""
Management command для отладки структуры контактов из AmoCRM API.
Показывает все поля, которые приходят из API, чтобы понять структуру данных.
"""
import json
from django.core.management.base import BaseCommand, CommandError
from ui.models import AmoApiConfig
from amocrm.client import AmoClient, AmoApiError


class Command(BaseCommand):
    help = "Показывает структуру контактов из AmoCRM API для отладки"

    def add_arguments(self, parser):
        parser.add_argument(
            "--limit",
            type=int,
            default=5,
            help="Количество контактов для анализа (по умолчанию 5)",
        )
        parser.add_argument(
            "--responsible-user-id",
            type=int,
            help="ID ответственного пользователя для фильтрации контактов",
        )
        parser.add_argument(
            "--output",
            type=str,
            help="Путь к файлу для сохранения JSON (опционально)",
        )

    def handle(self, *args, **options):
        limit = options.get("limit", 5)
        responsible_user_id = options.get("responsible_user_id")
        output_file = options.get("output")

        # Загружаем конфигурацию AmoCRM
        cfg = AmoApiConfig.load()
        if not cfg.domain:
            raise CommandError("AmoCRM domain не настроен. Проверьте настройки в админке.")

        try:
            client = AmoClient(cfg)
        except Exception as e:
            raise CommandError(f"Ошибка создания клиента AmoCRM: {e}")

        self.stdout.write(self.style.SUCCESS(f"Подключение к AmoCRM: {cfg.domain}"))
        self.stdout.write(f"Получаем {limit} контактов...\n")

        # Параметры запроса
        params = {
            "with": "custom_fields,notes,leads,customers,catalog_elements",
            "limit": min(limit, 250),  # Максимум 250 за запрос
        }
        
        if responsible_user_id:
            params["filter[responsible_user_id]"] = responsible_user_id
            self.stdout.write(f"Фильтр по ответственному пользователю: {responsible_user_id}")

        try:
            # Получаем контакты
            contacts = client.get_all_pages(
                "/api/v4/contacts",
                params=params,
                embedded_key="contacts",
                limit=250,
                max_pages=1,  # Только первая страница
            )

            if not contacts:
                self.stdout.write(self.style.WARNING("Контакты не найдены!"))
                return

            self.stdout.write(self.style.SUCCESS(f"Найдено контактов: {len(contacts)}\n"))
            self.stdout.write("=" * 80)

            # Анализируем каждый контакт
            all_contacts_data = []
            for idx, contact in enumerate(contacts[:limit], 1):
                self.stdout.write(f"\n{'=' * 80}")
                self.stdout.write(self.style.SUCCESS(f"КОНТАКТ #{idx} (ID: {contact.get('id', 'N/A')})"))
                self.stdout.write("=" * 80)

                # Стандартные поля
                self.stdout.write("\n📝 СТАНДАРТНЫЕ ПОЛЯ:")
                standard_fields = [
                    "id", "name", "first_name", "last_name",
                    "responsible_user_id", "group_id", "created_by", "updated_by",
                    "created_at", "updated_at", "is_deleted",
                    "phone", "email", "company_id", "closest_task_at", "account_id",
                ]
                for field in standard_fields:
                    value = contact.get(field)
                    if value is not None:
                        self.stdout.write(f"  {field}: {value}")

                # Кастомные поля
                custom_fields = contact.get("custom_fields_values") or []
                self.stdout.write(f"\n📋 КАСТОМНЫЕ ПОЛЯ (custom_fields_values): {len(custom_fields)} полей")
                if custom_fields:
                    for cf_idx, cf in enumerate(custom_fields, 1):
                        self.stdout.write(f"\n  Поле #{cf_idx}:")
                        self.stdout.write(f"    field_id: {cf.get('field_id')}")
                        self.stdout.write(f"    field_name: {cf.get('field_name')}")
                        self.stdout.write(f"    field_code: {cf.get('field_code')}")
                        self.stdout.write(f"    field_type: {cf.get('field_type')}")
                        values = cf.get("values") or []
                        self.stdout.write(f"    values (количество: {len(values)}):")
                        for val_idx, val in enumerate(values, 1):
                            if isinstance(val, dict):
                                self.stdout.write(f"      Значение #{val_idx}:")
                                for key, v in val.items():
                                    self.stdout.write(f"        {key}: {v}")
                            else:
                                self.stdout.write(f"      Значение #{val_idx}: {val}")

                # Вложенные данные
                embedded = contact.get("_embedded") or {}
                self.stdout.write(f"\n🔗 ВЛОЖЕННЫЕ ДАННЫЕ (_embedded):")
                if embedded:
                    for key, value in embedded.items():
                        if isinstance(value, list):
                            self.stdout.write(f"  {key}: {len(value)} элементов")
                            # Показываем первые 3 элемента
                            for item_idx, item in enumerate(value[:3], 1):
                                if isinstance(item, dict):
                                    self.stdout.write(f"    [{item_idx}] {item}")
                                else:
                                    self.stdout.write(f"    [{item_idx}] {item}")
                        else:
                            self.stdout.write(f"  {key}: {value}")
                else:
                    self.stdout.write("  (пусто)")

                # Все ключи контакта (для полноты картины)
                all_keys = list(contact.keys())
                self.stdout.write(f"\n🔑 ВСЕ КЛЮЧИ КОНТАКТА ({len(all_keys)}):")
                self.stdout.write(f"  {', '.join(sorted(all_keys))}")

                # Полная JSON-структура
                self.stdout.write(f"\n📄 ПОЛНАЯ JSON-СТРУКТУРА:")
                json_str = json.dumps(contact, ensure_ascii=False, indent=2)
                # Ограничиваем вывод до 5000 символов
                if len(json_str) > 5000:
                    self.stdout.write(json_str[:5000])
                    self.stdout.write(f"\n  ... (еще {len(json_str) - 5000} символов)")
                else:
                    self.stdout.write(json_str)

                # Сохраняем для вывода в файл
                all_contacts_data.append(contact)

            # Сохраняем в файл, если указан
            if output_file:
                with open(output_file, "w", encoding="utf-8") as f:
                    json.dump(all_contacts_data, f, ensure_ascii=False, indent=2)
                self.stdout.write(self.style.SUCCESS(f"\n\nДанные сохранены в файл: {output_file}"))

            # Итоговая статистика
            self.stdout.write(f"\n\n{'=' * 80}")
            self.stdout.write(self.style.SUCCESS("ИТОГОВАЯ СТАТИСТИКА:"))
            self.stdout.write(f"  Проанализировано контактов: {len(all_contacts_data)}")
            
            # Статистика по кастомным полям
            all_field_types = {}
            all_field_codes = {}
            all_field_names = {}
            for contact in all_contacts_data:
                for cf in contact.get("custom_fields_values") or []:
                    field_type = cf.get("field_type", "unknown")
                    field_code = cf.get("field_code", "no_code")
                    field_name = cf.get("field_name", "no_name")
                    all_field_types[field_type] = all_field_types.get(field_type, 0) + 1
                    all_field_codes[field_code] = all_field_codes.get(field_code, 0) + 1
                    all_field_names[field_name] = all_field_names.get(field_name, 0) + 1

            if all_field_types:
                self.stdout.write(f"\n  Типы кастомных полей:")
                for field_type, count in sorted(all_field_types.items()):
                    self.stdout.write(f"    {field_type}: {count}")

            if all_field_codes:
                self.stdout.write(f"\n  Коды кастомных полей (первые 10):")
                for field_code, count in sorted(all_field_codes.items(), key=lambda x: -x[1])[:10]:
                    self.stdout.write(f"    {field_code}: {count}")

            if all_field_names:
                self.stdout.write(f"\n  Названия кастомных полей (первые 10):")
                for field_name, count in sorted(all_field_names.items(), key=lambda x: -x[1])[:10]:
                    self.stdout.write(f"    {field_name}: {count}")

        except AmoApiError as e:
            raise CommandError(f"Ошибка AmoCRM API: {e}")
        except Exception as e:
            raise CommandError(f"Неожиданная ошибка: {e}")
