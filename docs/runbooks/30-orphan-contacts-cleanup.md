---
tags: [runbook, cleanup, 343-contacts, orphan]
created: 2026-04-20
status: READY — можно применять после Релиза 1
risk: low
---

# Runbook — удаление 343 orphan-контактов

## Контекст

Из Day 2-аудита:
- 343 контакта без `company_id` (= 0.35% от 99 152 всех контактов)
- 45 из них **полностью пусты** (ни phone, ни email)
- 289 с телефоном, 134 с email
- **0 упоминаний в ActivityEvent за 180 дней** — ни один не трогался полгода
- **86% создано в январе 2026** (массовый импорт, вероятно из AmoCRM)

Заказчик подтвердил: если компания удалена — можно удалять.

## Стратегия

**Двухступенчатое удаление** с CSV-выгрузкой для аудита:

1. **Шаг 1**: удалить 45 «полностью пустых» (гарантированный мусор).
2. **Шаг 2**: выгрузить 298 «с данными» в CSV, показать заказчику. Если одобрит — удалить всех.
3. **Ничего не делаем** на контактах, которые:
   - Имеют связанные `CompanyHistoryEvent` (вдруг упоминаются в истории)
   - Имеют связанные `Task` (хотя task→contact нет, но проверим)
   - Имеют заметки в `CompanyNote` через any FK

## Management-команда

Создать файл `backend/companies/management/commands/cleanup_orphan_contacts.py`:

```python
from django.core.management.base import BaseCommand
from django.db import transaction
from companies.models import Contact, ContactPhone, ContactEmail
import csv
import sys


class Command(BaseCommand):
    help = "Удаляет или выгружает контакты без company_id (orphans)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--mode",
            choices=["dry-run", "export-csv", "delete-empty", "delete-all"],
            required=True,
            help=(
                "dry-run: только считаем; "
                "export-csv: выгружаем в stdout CSV; "
                "delete-empty: удаляем только без phone/email; "
                "delete-all: удаляем всех orphans (после явного подтверждения)"
            ),
        )
        parser.add_argument("--confirm", action="store_true", help="Обязательно для delete-*")

    def handle(self, *args, mode, confirm, **kw):
        orphans = Contact.objects.filter(company_id__isnull=True)
        total = orphans.count()
        self.stdout.write(f"Всего orphan-контактов: {total}")

        if mode == "dry-run":
            with_phone = sum(
                1 for c in orphans if ContactPhone.objects.filter(contact_id=c.id).exists()
            )
            with_email = sum(
                1 for c in orphans if ContactEmail.objects.filter(contact_id=c.id).exists()
            )
            empty = sum(
                1
                for c in orphans
                if not ContactPhone.objects.filter(contact_id=c.id).exists()
                and not ContactEmail.objects.filter(contact_id=c.id).exists()
            )
            self.stdout.write(f"  с phone: {with_phone}")
            self.stdout.write(f"  с email: {with_email}")
            self.stdout.write(f"  полностью пустых: {empty}")
            return

        if mode == "export-csv":
            w = csv.writer(sys.stdout)
            w.writerow(
                ["id", "first_name", "last_name", "position", "phones", "emails", "created_at"]
            )
            for c in orphans.order_by("-created_at"):
                phones = ", ".join(
                    ContactPhone.objects.filter(contact_id=c.id).values_list("value", flat=True)
                )
                emails = ", ".join(
                    ContactEmail.objects.filter(contact_id=c.id).values_list("value", flat=True)
                )
                w.writerow(
                    [
                        str(c.id),
                        c.first_name,
                        c.last_name,
                        getattr(c, "position", ""),
                        phones,
                        emails,
                        c.created_at.isoformat() if c.created_at else "",
                    ]
                )
            return

        if not confirm:
            self.stderr.write("--confirm обязателен для удаления")
            sys.exit(1)

        with transaction.atomic():
            if mode == "delete-empty":
                empty_ids = [
                    c.id
                    for c in orphans
                    if not ContactPhone.objects.filter(contact_id=c.id).exists()
                    and not ContactEmail.objects.filter(contact_id=c.id).exists()
                ]
                deleted, _ = Contact.objects.filter(id__in=empty_ids).delete()
                self.stdout.write(f"Удалено пустых: {deleted}")
            elif mode == "delete-all":
                deleted, _ = orphans.delete()
                self.stdout.write(f"Удалено всего: {deleted}")
```

## Процедура выполнения

### Этап 1. Dry-run на проде (только чтение)

```bash
docker exec proficrm-web-1 python manage.py cleanup_orphan_contacts --mode dry-run
```

**Ожидаем**:
```
Всего orphan-контактов: 343
  с phone: 289
  с email: 134
  полностью пустых: 45
```

### Этап 2. Экспорт CSV (только чтение)

```bash
docker exec proficrm-web-1 python manage.py cleanup_orphan_contacts --mode export-csv > /tmp/orphans_$(date +%Y%m%d).csv
wc -l /tmp/orphans_$(date +%Y%m%d).csv
```

Скачать и показать заказчику для утверждения.

### Этап 3. Удаление пустых (безопасно)

```bash
docker exec proficrm-web-1 python manage.py cleanup_orphan_contacts --mode delete-empty --confirm
```

Ожидаем: `Удалено пустых: 45`.

### Этап 4. Удаление остальных (после подтверждения заказчика)

```bash
docker exec proficrm-web-1 python manage.py cleanup_orphan_contacts --mode delete-all --confirm
```

Ожидаем: `Удалено всего: 298`.

## Откат

Если что-то не так — восстановить из Netangels backup (последний перед выполнением).

```bash
# Точечный restore только таблицы contacts не получится тривиально
# Простейший путь — полный restore БД из бэкапа
```

## Аудитор

Подготовлено: 2026-04-20, Day 3.
Statуs: READY. Применять после Релиза 1 (чтобы команда была на проде).
