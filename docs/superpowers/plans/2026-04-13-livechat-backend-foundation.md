# Live-chat Backend Foundation — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Построить backend-фундамент для live-chat UX improvements: автороутинг новых диалогов по региону клиента в нужный филиал, ролевая видимость диалогов, модель передачи с аудитом, приватные заметки, heartbeat онлайн-статуса.

**Architecture:** Расширяем существующий `messenger/` модуль. НЕ ломаем `Inbox.branch` (одиночный FK) и `InboxRoundRobinService` — добавляем слой `MultiBranchRouter` поверх: он выбирает целевой филиал по региону клиента, затем выбирает онлайн-менеджера с минимальной нагрузкой. Новый справочник `BranchRegion` с fixture из PDF «Положение о распределении регионов 2025-2026». Видимость диалогов расширяется в `messenger/selectors.py` через ролевые Q-фильтры.

**Tech Stack:** Django 6.0.1, Python 3.13, PostgreSQL 16, Celery 5.4, Redis 7, DRF. Тесты через `scripts/test.sh` (Django TestCase, PostgreSQL 5433 в `docker-compose.test.yml`).

**Spec reference:** `docs/superpowers/specs/2026-04-13-livechat-ux-completion-design.md` §4, §5, §6.4 (private notes backend), §7.6 (heartbeat)

**Deviations from spec:**
- `Inbox.branches` НЕ становится M2M (в spec §4.1.2). Причина — в коде уже есть `InboxRoundRobinService` на `Inbox.branch_id`. Вместо этого `MultiBranchRouter` выбирает branch → затем пользует `RoundRobinService` внутри этого branch через отдельный ключ Redis (per-branch очередь, не per-inbox).
- UI-статусы мапятся на **3 DB-статуса** (`OPEN`/`RESOLVED`/`CLOSED`), не 5. Логика 🔴/🟡/🔵 — все поверх `OPEN` через `assignee IS NULL` и сравнение timestamps. Это не меняет backend-модель.

---

## File Structure

**Новые файлы:**
- `backend/messenger/assignment_services/region_router.py` — `MultiBranchRouter.route(conversation) -> Branch`
- `backend/messenger/assignment_services/branch_load_balancer.py` — выбор наименее загруженного онлайн-менеджера филиала
- `backend/messenger/assignment_services/auto_assign.py` — оркестратор: regions → branch → manager → fallback pool
- `backend/messenger/geoip_region.py` — утилита определения региона по IP (существующий `geoip.py` для страны, новый — для региона РФ)
- `backend/accounts/models_region.py` — модель `BranchRegion` (отдельный файл, чтобы не раздувать `accounts/models.py`)
- `backend/accounts/fixtures/branch_regions_2025_2026.json` — fixture из PDF
- `backend/accounts/management/commands/load_branch_regions.py` — management-команда для загрузки fixture
- `backend/messenger/tests/test_auto_assign.py` — unit-тесты auto-assignment pipeline
- `backend/messenger/tests/test_visibility.py` — тесты ролевой видимости
- `backend/messenger/tests/test_transfer.py` — тесты `ConversationTransfer` + API endpoint
- `backend/messenger/tests/test_private_messages.py` — тесты фильтрации private в widget API
- `backend/messenger/tests/test_heartbeat.py` — тесты heartbeat endpoint + celery offline task

**Модифицируемые файлы:**
- `backend/messenger/models.py` — добавить `Conversation.client_region`, `Conversation.client_region_source`, `Conversation.needs_help`, `Message.is_private`
- `backend/accounts/models.py` — добавить `User.messenger_online`, `User.messenger_last_seen`; импорт `BranchRegion`
- `backend/messenger/selectors.py` — функция `get_visible_conversations(user)`
- `backend/messenger/api.py` — endpoint `POST /api/messenger/heartbeat/`, endpoint `POST /api/messenger/conversations/{id}/transfer/`
- `backend/messenger/widget_api.py` — фильтрация `is_private=False` в SSE-стримах виджета
- `backend/messenger/tasks.py` — celery tasks: `check_offline_operators`, `auto_assign_new_conversation`
- `backend/messenger/signals.py` (создать если нет) — сигнал на `Conversation` post_save: если `assignee IS NULL and status=OPEN` → триггерить auto-assign
- `backend/crm/settings.py` — добавить celery beat schedule для `check_offline_operators`

**Миграции (по порядку):**
1. `messenger/migrations/0016_conversation_client_region.py` — 2 поля
2. `accounts/migrations/0010_user_messenger_online.py` — 2 поля + индекс
3. `accounts/migrations/0011_branch_region.py` — модель + первичные индексы
4. `accounts/migrations/0012_branch_regions_data.py` — data migration: вызов `load_branch_regions` программно
5. `messenger/migrations/0017_conversation_transfer.py` — модель `ConversationTransfer`
6. `messenger/migrations/0018_message_is_private.py` — поле + индекс
7. `messenger/migrations/0019_conversation_needs_help.py` — поле

---

## Task 1: Миграция `Conversation.client_region`

**Files:**
- Modify: `backend/messenger/models.py:150` (class Conversation)
- Create: `backend/messenger/migrations/0016_conversation_client_region.py`
- Test: `backend/messenger/tests/test_auto_assign.py` (создание файла — базовый импорт)

- [ ] **Step 1: Добавить поля в модель**

В `backend/messenger/models.py`, внутри `class Conversation`, после существующего поля `branch`:

```python
    client_region = models.CharField(
        "Регион клиента",
        max_length=128,
        blank=True,
        default="",
        db_index=True,
        help_text="Определён по GeoIP / pre-chat анкете / компании клиента",
    )

    class RegionSource(models.TextChoices):
        GEOIP = "geoip", "GeoIP"
        FORM = "form", "Анкета"
        COMPANY = "company", "Компания"
        UNKNOWN = "", "Не определён"

    client_region_source = models.CharField(
        "Источник региона",
        max_length=16,
        choices=RegionSource.choices,
        blank=True,
        default=RegionSource.UNKNOWN,
    )
```

- [ ] **Step 2: Создать миграцию**

Run: `cd backend && python manage.py makemigrations messenger --name conversation_client_region`
Expected: файл `0016_conversation_client_region.py` создан, содержит `AddField` для обоих полей.

- [ ] **Step 3: Запустить миграцию локально**

Run: `cd backend && python manage.py migrate messenger`
Expected: `Applying messenger.0016_conversation_client_region... OK`

- [ ] **Step 4: Написать smoke-тест создания**

Создать файл `backend/messenger/tests/test_auto_assign.py`:

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from accounts.models import Branch
from messenger.models import Inbox, Conversation, Contact

User = get_user_model()


class ConversationClientRegionTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Test Branch", code="test")
        self.inbox = Inbox.objects.create(name="Test Inbox", branch=self.branch)
        self.contact = Contact.objects.create(
            inbox=self.inbox, name="Test", email="test@example.com"
        )

    def test_conversation_stores_client_region_and_source(self):
        conv = Conversation.objects.create(
            inbox=self.inbox,
            contact=self.contact,
            client_region="Свердловская область",
            client_region_source=Conversation.RegionSource.GEOIP,
        )
        self.assertEqual(conv.client_region, "Свердловская область")
        self.assertEqual(conv.client_region_source, "geoip")

    def test_conversation_region_defaults_empty(self):
        conv = Conversation.objects.create(inbox=self.inbox, contact=self.contact)
        self.assertEqual(conv.client_region, "")
        self.assertEqual(conv.client_region_source, "")
```

- [ ] **Step 5: Запустить тест**

Run: `scripts/test.sh messenger.tests.test_auto_assign.ConversationClientRegionTests`
Expected: 2 tests passed.

- [ ] **Step 6: Commit**

```bash
git add backend/messenger/models.py backend/messenger/migrations/0016_conversation_client_region.py backend/messenger/tests/test_auto_assign.py
git commit -m "Feat(Messenger): add client_region field for auto-routing"
```

---

## Task 2: Поля `User.messenger_online` и heartbeat endpoint

**Files:**
- Modify: `backend/accounts/models.py` (class User)
- Create: `backend/accounts/migrations/0010_user_messenger_online.py`
- Modify: `backend/messenger/api.py` — новый endpoint
- Modify: `backend/messenger/urls.py` — маршрут
- Create: `backend/messenger/tests/test_heartbeat.py`

- [ ] **Step 1: Добавить поля в User**

В `backend/accounts/models.py`, в `class User`, после поля `role`:

```python
    messenger_online = models.BooleanField(
        "Онлайн в мессенджере",
        default=False,
        db_index=True,
    )
    messenger_last_seen = models.DateTimeField(
        "Последняя активность в мессенджере",
        null=True,
        blank=True,
    )
```

- [ ] **Step 2: Миграция**

Run: `cd backend && python manage.py makemigrations accounts --name user_messenger_online && python manage.py migrate accounts`
Expected: файл создан, миграция применена.

- [ ] **Step 3: Написать failing-тест heartbeat**

В `backend/messenger/tests/test_heartbeat.py`:

```python
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from accounts.models import Branch

User = get_user_model()


class HeartbeatEndpointTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.user = User.objects.create_user(
            username="op1", password="pw", role=User.Role.MANAGER, branch=self.branch
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_heartbeat_sets_online_and_last_seen(self):
        self.assertFalse(self.user.messenger_online)
        resp = self.client.post("/api/messenger/heartbeat/")
        self.assertEqual(resp.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.messenger_online)
        self.assertIsNotNone(self.user.messenger_last_seen)
        delta = timezone.now() - self.user.messenger_last_seen
        self.assertLess(delta.total_seconds(), 5)

    def test_heartbeat_requires_auth(self):
        self.client.force_authenticate(None)
        resp = self.client.post("/api/messenger/heartbeat/")
        self.assertEqual(resp.status_code, 401)
```

- [ ] **Step 4: Запустить тест — ожидаем FAIL**

Run: `scripts/test.sh messenger.tests.test_heartbeat`
Expected: FAIL (404 на `/api/messenger/heartbeat/`).

- [ ] **Step 5: Реализовать endpoint**

В `backend/messenger/api.py` добавить:

```python
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils import timezone


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def heartbeat_view(request):
    """Обновить messenger_online/messenger_last_seen для текущего пользователя."""
    user = request.user
    user.messenger_online = True
    user.messenger_last_seen = timezone.now()
    user.save(update_fields=["messenger_online", "messenger_last_seen"])
    return Response({"ok": True, "last_seen": user.messenger_last_seen.isoformat()})
```

В `backend/messenger/urls.py` добавить маршрут:

```python
from messenger.api import heartbeat_view

urlpatterns += [
    path("heartbeat/", heartbeat_view, name="messenger-heartbeat"),
]
```

- [ ] **Step 6: Запустить тест — PASS**

Run: `scripts/test.sh messenger.tests.test_heartbeat`
Expected: 2 tests passed.

- [ ] **Step 7: Commit**

```bash
git add backend/accounts/models.py backend/accounts/migrations/0010_user_messenger_online.py backend/messenger/api.py backend/messenger/urls.py backend/messenger/tests/test_heartbeat.py
git commit -m "Feat(Messenger): add messenger_online heartbeat endpoint"
```

---

## Task 3: Модель `BranchRegion` + загрузка регионов из PDF

**Files:**
- Create: `backend/accounts/models_region.py`
- Modify: `backend/accounts/models.py` (импорт в конце файла)
- Create: `backend/accounts/fixtures/branch_regions_2025_2026.json`
- Create: `backend/accounts/management/commands/load_branch_regions.py`
- Create: `backend/accounts/migrations/0011_branch_region.py`
- Create: `backend/accounts/migrations/0012_branch_regions_data.py`
- Test: `backend/accounts/tests/test_branch_region.py`

- [ ] **Step 1: Создать модель в отдельном файле**

Файл `backend/accounts/models_region.py`:

```python
from django.db import models
from accounts.models import Branch


class BranchRegion(models.Model):
    """Регион, закреплённый за филиалом (из Положения 2025-2026)."""

    branch = models.ForeignKey(
        Branch,
        on_delete=models.CASCADE,
        related_name="regions",
        verbose_name="Филиал",
    )
    region_name = models.CharField(
        "Регион",
        max_length=128,
        db_index=True,
    )
    is_common_pool = models.BooleanField(
        "Общий пул",
        default=False,
        help_text="Москва/МО, СПб/ЛО, Новгородская, Псковская — распределяется равномерно",
    )
    ordering = models.PositiveSmallIntegerField("Порядок", default=0)

    class Meta:
        verbose_name = "Регион филиала"
        verbose_name_plural = "Регионы филиалов"
        unique_together = [("branch", "region_name")]
        indexes = [
            models.Index(fields=["region_name", "branch"]),
            models.Index(fields=["is_common_pool"]),
        ]
        ordering = ["branch", "ordering"]

    def __str__(self):
        return f"{self.branch.name} — {self.region_name}"
```

В конце `backend/accounts/models.py` добавить:

```python
# Импорт дополнительных моделей (после основных, чтобы избежать circular imports)
from accounts.models_region import BranchRegion  # noqa: E402, F401
```

- [ ] **Step 2: Создать fixture из PDF**

Файл `backend/accounts/fixtures/branch_regions_2025_2026.json`:

```json
[
  {"branch_code": "common", "region_name": "Москва, Московская область", "is_common_pool": true},
  {"branch_code": "common", "region_name": "Санкт-Петербург, Ленинградская область", "is_common_pool": true},
  {"branch_code": "common", "region_name": "Новгородская область", "is_common_pool": true},
  {"branch_code": "common", "region_name": "Псковская область", "is_common_pool": true},

  {"branch_code": "ekb", "region_name": "Архангельская область"},
  {"branch_code": "ekb", "region_name": "Байконур"},
  {"branch_code": "ekb", "region_name": "Вологодская область"},
  {"branch_code": "ekb", "region_name": "Ивановская область"},
  {"branch_code": "ekb", "region_name": "Калининградская область"},
  {"branch_code": "ekb", "region_name": "Калужская область"},
  {"branch_code": "ekb", "region_name": "Кировская область"},
  {"branch_code": "ekb", "region_name": "Костромская область"},
  {"branch_code": "ekb", "region_name": "Липецкая область"},
  {"branch_code": "ekb", "region_name": "Магаданская область"},
  {"branch_code": "ekb", "region_name": "Мурманская область"},
  {"branch_code": "ekb", "region_name": "Псковская область"},
  {"branch_code": "ekb", "region_name": "Республика Башкортостан"},
  {"branch_code": "ekb", "region_name": "Республика Карелия"},
  {"branch_code": "ekb", "region_name": "Республика Коми"},
  {"branch_code": "ekb", "region_name": "Республика Марий Эл"},
  {"branch_code": "ekb", "region_name": "Республика Мордовия"},
  {"branch_code": "ekb", "region_name": "Республика Саха (Якутия)"},
  {"branch_code": "ekb", "region_name": "Республика Татарстан"},
  {"branch_code": "ekb", "region_name": "Рязанская область"},
  {"branch_code": "ekb", "region_name": "Свердловская область"},
  {"branch_code": "ekb", "region_name": "Удмуртская Республика"},
  {"branch_code": "ekb", "region_name": "Ульяновская область"},
  {"branch_code": "ekb", "region_name": "Челябинская область"},
  {"branch_code": "ekb", "region_name": "Чувашская Республика"},
  {"branch_code": "ekb", "region_name": "Чукотский автономный округ"},
  {"branch_code": "ekb", "region_name": "Ярославская область"},
  {"branch_code": "ekb", "region_name": "Луганская область"},

  {"branch_code": "tmn", "region_name": "Алтайский край"},
  {"branch_code": "tmn", "region_name": "Амурская область"},
  {"branch_code": "tmn", "region_name": "Еврейская автономная область"},
  {"branch_code": "tmn", "region_name": "Забайкальский край"},
  {"branch_code": "tmn", "region_name": "Запорожская область"},
  {"branch_code": "tmn", "region_name": "Иркутская область"},
  {"branch_code": "tmn", "region_name": "Камчатский край"},
  {"branch_code": "tmn", "region_name": "Кемеровская область"},
  {"branch_code": "tmn", "region_name": "Курганская область"},
  {"branch_code": "tmn", "region_name": "Красноярский край"},
  {"branch_code": "tmn", "region_name": "Ненецкий автономный округ"},
  {"branch_code": "tmn", "region_name": "Новосибирская область"},
  {"branch_code": "tmn", "region_name": "Омская область"},
  {"branch_code": "tmn", "region_name": "Пермский край"},
  {"branch_code": "tmn", "region_name": "Приморский край"},
  {"branch_code": "tmn", "region_name": "Республика Алтай"},
  {"branch_code": "tmn", "region_name": "Республика Бурятия"},
  {"branch_code": "tmn", "region_name": "Республика Тыва"},
  {"branch_code": "tmn", "region_name": "Республика Хакасия"},
  {"branch_code": "tmn", "region_name": "Сахалинская область"},
  {"branch_code": "tmn", "region_name": "Тамбовская область"},
  {"branch_code": "tmn", "region_name": "Тверская область"},
  {"branch_code": "tmn", "region_name": "Тульская область"},
  {"branch_code": "tmn", "region_name": "Томская область"},
  {"branch_code": "tmn", "region_name": "Тюменская область"},
  {"branch_code": "tmn", "region_name": "Хабаровский край"},
  {"branch_code": "tmn", "region_name": "Ханты-Мансийский автономный округ"},
  {"branch_code": "tmn", "region_name": "Ямало-Ненецкий автономный округ"},

  {"branch_code": "krd", "region_name": "Астраханская область"},
  {"branch_code": "krd", "region_name": "Белгородская область"},
  {"branch_code": "krd", "region_name": "Брянская область"},
  {"branch_code": "krd", "region_name": "Владимирская область"},
  {"branch_code": "krd", "region_name": "Волгоградская область"},
  {"branch_code": "krd", "region_name": "Воронежская область"},
  {"branch_code": "krd", "region_name": "Кабардино-Балкарская Республика"},
  {"branch_code": "krd", "region_name": "Карачаево-Черкесская Республика"},
  {"branch_code": "krd", "region_name": "Краснодарский край"},
  {"branch_code": "krd", "region_name": "Курская область"},
  {"branch_code": "krd", "region_name": "Нижегородская область"},
  {"branch_code": "krd", "region_name": "Оренбургская область"},
  {"branch_code": "krd", "region_name": "Орловская область"},
  {"branch_code": "krd", "region_name": "Пензенская область"},
  {"branch_code": "krd", "region_name": "Республика Адыгея"},
  {"branch_code": "krd", "region_name": "Республика Дагестан"},
  {"branch_code": "krd", "region_name": "Республика Ингушетия"},
  {"branch_code": "krd", "region_name": "Республика Калмыкия"},
  {"branch_code": "krd", "region_name": "Республика Крым"},
  {"branch_code": "krd", "region_name": "Республика Северная Осетия – Алания"},
  {"branch_code": "krd", "region_name": "Ростовская область"},
  {"branch_code": "krd", "region_name": "Самарская область"},
  {"branch_code": "krd", "region_name": "Саратовская область"},
  {"branch_code": "krd", "region_name": "Смоленская область"},
  {"branch_code": "krd", "region_name": "Ставропольский край"},
  {"branch_code": "krd", "region_name": "Чеченская Республика"},
  {"branch_code": "krd", "region_name": "Донецкая область"},
  {"branch_code": "krd", "region_name": "Херсонская область"}
]
```

**Замечание:** `branch_code` — значение поля `Branch.code`. Для общего пула используется значение `common` — это особая логика: в команде загрузки для общего пула создаются записи для ВСЕХ филиалов (`is_common_pool=True`). Это проверяется в management-команде.

- [ ] **Step 3: Management команда загрузки**

Файл `backend/accounts/management/commands/load_branch_regions.py`:

```python
import json
from pathlib import Path
from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.models import Branch, BranchRegion


class Command(BaseCommand):
    help = "Загрузка справочника BranchRegion из fixture JSON (Положение 2025-2026)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="accounts/fixtures/branch_regions_2025_2026.json",
            help="Путь к fixture-файлу",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Удалить все существующие BranchRegion перед загрузкой",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_absolute():
            # относительно backend/
            from django.conf import settings
            path = Path(settings.BASE_DIR) / path

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        if options["flush"]:
            BranchRegion.objects.all().delete()
            self.stdout.write("Очищено существующее.")

        branches = {b.code: b for b in Branch.objects.all()}
        created = 0
        skipped = 0

        for idx, item in enumerate(data):
            code = item["branch_code"]
            region = item["region_name"]
            is_pool = item.get("is_common_pool", False)

            if code == "common":
                # общий пул — создать запись для ВСЕХ реальных филиалов
                for branch in branches.values():
                    if branch.code == "common":
                        continue
                    _, was_created = BranchRegion.objects.get_or_create(
                        branch=branch,
                        region_name=region,
                        defaults={"is_common_pool": True, "ordering": idx},
                    )
                    if was_created:
                        created += 1
                    else:
                        skipped += 1
            else:
                branch = branches.get(code)
                if not branch:
                    self.stdout.write(self.style.WARNING(f"Филиал '{code}' не найден, пропущен регион '{region}'"))
                    skipped += 1
                    continue
                _, was_created = BranchRegion.objects.get_or_create(
                    branch=branch,
                    region_name=region,
                    defaults={"is_common_pool": is_pool, "ordering": idx},
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        self.stdout.write(self.style.SUCCESS(f"Готово. Создано: {created}, пропущено: {skipped}"))
```

- [ ] **Step 4: Миграция модели**

Run: `cd backend && python manage.py makemigrations accounts --name branch_region`
Expected: файл `0011_branch_region.py` создан.

- [ ] **Step 5: Data-миграция для загрузки fixture**

Создать `backend/accounts/migrations/0012_branch_regions_data.py` вручную:

```python
from django.db import migrations


def load_regions(apps, schema_editor):
    from django.core.management import call_command
    call_command("load_branch_regions", "--flush")


def unload_regions(apps, schema_editor):
    BranchRegion = apps.get_model("accounts", "BranchRegion")
    BranchRegion.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_branch_region"),
    ]

    operations = [
        migrations.RunPython(load_regions, unload_regions),
    ]
```

- [ ] **Step 6: Применить миграции**

Run: `cd backend && python manage.py migrate accounts`
Expected: обе миграции применены. В логах — `Готово. Создано: N, пропущено: M`.

- [ ] **Step 7: Написать тесты**

Файл `backend/accounts/tests/test_branch_region.py`:

```python
from django.test import TestCase
from django.core.management import call_command
from accounts.models import Branch, BranchRegion


class BranchRegionTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="Екатеринбург", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")
        self.krd = Branch.objects.create(name="Краснодар", code="krd")

    def test_load_fixture_creates_regions(self):
        call_command("load_branch_regions", "--flush")
        # Свердловская → ЕКБ
        self.assertTrue(
            BranchRegion.objects.filter(branch=self.ekb, region_name="Свердловская область").exists()
        )
        # Краснодарский край → КРД
        self.assertTrue(
            BranchRegion.objects.filter(branch=self.krd, region_name="Краснодарский край").exists()
        )
        # Тюменская → ТМН
        self.assertTrue(
            BranchRegion.objects.filter(branch=self.tmn, region_name="Тюменская область").exists()
        )

    def test_common_pool_created_for_all_branches(self):
        call_command("load_branch_regions", "--flush")
        moscow_count = BranchRegion.objects.filter(
            region_name="Москва, Московская область", is_common_pool=True
        ).count()
        # 3 филиала (ЕКБ+ТМН+КРД)
        self.assertEqual(moscow_count, 3)

    def test_unique_constraint(self):
        BranchRegion.objects.create(branch=self.ekb, region_name="Тест")
        with self.assertRaises(Exception):
            BranchRegion.objects.create(branch=self.ekb, region_name="Тест")

    def test_lookup_branch_by_region(self):
        call_command("load_branch_regions", "--flush")
        region = BranchRegion.objects.filter(
            region_name="Сахалинская область", is_common_pool=False
        ).first()
        self.assertEqual(region.branch, self.tmn)
```

- [ ] **Step 8: Запустить тесты**

Run: `scripts/test.sh accounts.tests.test_branch_region`
Expected: 4 tests passed.

- [ ] **Step 9: Commit**

```bash
git add backend/accounts/models_region.py backend/accounts/models.py backend/accounts/fixtures/branch_regions_2025_2026.json backend/accounts/management/commands/load_branch_regions.py backend/accounts/migrations/0011_branch_region.py backend/accounts/migrations/0012_branch_regions_data.py backend/accounts/tests/test_branch_region.py
git commit -m "Feat(Accounts): add BranchRegion model + fixture from Polozhenie 2025-2026"
```

---

## Task 4: Модель `ConversationTransfer`

**Files:**
- Modify: `backend/messenger/models.py` (добавить в конец файла)
- Create: `backend/messenger/migrations/0017_conversation_transfer.py`
- Test: `backend/messenger/tests/test_transfer.py`

- [ ] **Step 1: Добавить модель**

В `backend/messenger/models.py` в конец файла:

```python
class ConversationTransfer(models.Model):
    """Лог передачи диалога между операторами/филиалами."""

    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="transfers"
    )
    from_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messenger_transfers_from",
    )
    to_user = models.ForeignKey(
        "accounts.User",
        on_delete=models.PROTECT,
        related_name="messenger_transfers_to",
    )
    from_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messenger_transfers_out",
    )
    to_branch = models.ForeignKey(
        "accounts.Branch",
        on_delete=models.PROTECT,
        related_name="messenger_transfers_in",
    )
    reason = models.TextField("Причина передачи")
    cross_branch = models.BooleanField("Межфилиальная передача", default=False)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "Передача диалога"
        verbose_name_plural = "Передачи диалогов"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["conversation", "-created_at"]),
            models.Index(fields=["to_user", "-created_at"]),
        ]

    def __str__(self):
        return f"Transfer #{self.pk}: conv={self.conversation_id} {self.from_user_id}→{self.to_user_id}"
```

- [ ] **Step 2: Миграция**

Run: `cd backend && python manage.py makemigrations messenger --name conversation_transfer && python manage.py migrate messenger`
Expected: `0017_conversation_transfer.py` создан, применён.

- [ ] **Step 3: Написать failing тест API endpoint передачи**

В `backend/messenger/tests/test_transfer.py`:

```python
from django.test import TestCase
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model
from accounts.models import Branch
from messenger.models import Inbox, Conversation, Contact, ConversationTransfer

User = get_user_model()


class TransferEndpointTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.krd = Branch.objects.create(name="КРД", code="krd")
        self.op_ekb = User.objects.create_user("op_ekb", password="pw", role=User.Role.MANAGER, branch=self.ekb)
        self.op_krd = User.objects.create_user("op_krd", password="pw", role=User.Role.MANAGER, branch=self.krd)
        self.inbox = Inbox.objects.create(name="Widget", branch=self.ekb)
        self.contact = Contact.objects.create(inbox=self.inbox, name="Client")
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, assignee=self.op_ekb, branch=self.ekb
        )
        self.client = APIClient()
        self.client.force_authenticate(self.op_ekb)

    def test_transfer_requires_reason(self):
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {"to_user_id": self.op_krd.id, "reason": "abc"},  # < 5 симв
            format="json",
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("reason", resp.data)

    def test_transfer_creates_log_and_updates_assignee(self):
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {"to_user_id": self.op_krd.id, "reason": "Клиент из Краснодарского края по регламенту"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)

        self.conv.refresh_from_db()
        self.assertEqual(self.conv.assignee, self.op_krd)
        self.assertEqual(self.conv.branch, self.krd)

        log = ConversationTransfer.objects.get(conversation=self.conv)
        self.assertEqual(log.from_user, self.op_ekb)
        self.assertEqual(log.to_user, self.op_krd)
        self.assertEqual(log.from_branch, self.ekb)
        self.assertEqual(log.to_branch, self.krd)
        self.assertTrue(log.cross_branch)

    def test_transfer_same_branch_not_marked_cross(self):
        op_ekb2 = User.objects.create_user("op_ekb2", password="pw", role=User.Role.MANAGER, branch=self.ekb)
        resp = self.client.post(
            f"/api/messenger/conversations/{self.conv.id}/transfer/",
            {"to_user_id": op_ekb2.id, "reason": "Ухожу на обед, возьмёт коллега"},
            format="json",
        )
        self.assertEqual(resp.status_code, 200)
        log = ConversationTransfer.objects.get(conversation=self.conv)
        self.assertFalse(log.cross_branch)
```

- [ ] **Step 4: Запустить тест — ожидаем FAIL**

Run: `scripts/test.sh messenger.tests.test_transfer`
Expected: FAIL (404 на endpoint).

- [ ] **Step 5: Реализовать endpoint**

В `backend/messenger/api.py`:

```python
from rest_framework import serializers
from messenger.models import ConversationTransfer


class TransferRequestSerializer(serializers.Serializer):
    to_user_id = serializers.IntegerField()
    reason = serializers.CharField(min_length=5, max_length=2000)


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def transfer_conversation(request, conversation_id):
    from messenger.models import Conversation
    from accounts.models import User as UserModel

    try:
        conv = Conversation.objects.select_related("assignee", "branch").get(pk=conversation_id)
    except Conversation.DoesNotExist:
        return Response({"error": "not_found"}, status=404)

    serializer = TransferRequestSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    try:
        to_user = UserModel.objects.select_related("branch").get(pk=serializer.validated_data["to_user_id"])
    except UserModel.DoesNotExist:
        return Response({"error": "to_user_not_found"}, status=400)

    from_user = conv.assignee
    from_branch = conv.branch
    to_branch = to_user.branch
    cross_branch = bool(from_branch and to_branch and from_branch.id != to_branch.id)

    ConversationTransfer.objects.create(
        conversation=conv,
        from_user=from_user,
        to_user=to_user,
        from_branch=from_branch,
        to_branch=to_branch,
        reason=serializer.validated_data["reason"],
        cross_branch=cross_branch,
    )

    conv.assignee = to_user
    conv.branch = to_branch
    conv.save(update_fields=["assignee", "branch", "updated_at"] if hasattr(conv, "updated_at") else ["assignee", "branch"])

    return Response({"ok": True, "cross_branch": cross_branch})
```

Маршрут в `backend/messenger/urls.py`:

```python
from messenger.api import transfer_conversation

urlpatterns += [
    path("conversations/<int:conversation_id>/transfer/", transfer_conversation, name="messenger-transfer"),
]
```

- [ ] **Step 6: Запустить тест — PASS**

Run: `scripts/test.sh messenger.tests.test_transfer`
Expected: 3 tests passed.

- [ ] **Step 7: Commit**

```bash
git add backend/messenger/models.py backend/messenger/migrations/0017_conversation_transfer.py backend/messenger/api.py backend/messenger/urls.py backend/messenger/tests/test_transfer.py
git commit -m "Feat(Messenger): add ConversationTransfer model + API endpoint"
```

---

## Task 5: Поле `Message.is_private` + фильтрация в widget SSE

**Files:**
- Modify: `backend/messenger/models.py` (class Message)
- Create: `backend/messenger/migrations/0018_message_is_private.py`
- Modify: `backend/messenger/widget_api.py` (SSE-стрим)
- Test: `backend/messenger/tests/test_private_messages.py`

- [ ] **Step 1: Добавить поле**

В `backend/messenger/models.py`, в `class Message` после существующих флагов:

```python
    is_private = models.BooleanField(
        "Приватная заметка (только для сотрудников)",
        default=False,
        db_index=True,
    )
```

В `Meta.indexes` добавить:

```python
        models.Index(fields=["conversation", "is_private", "created_at"], name="msg_conv_private_idx"),
```

- [ ] **Step 2: Миграция**

Run: `cd backend && python manage.py makemigrations messenger --name message_is_private && python manage.py migrate messenger`

- [ ] **Step 3: Failing test — widget SSE фильтрует приватные**

Файл `backend/messenger/tests/test_private_messages.py`:

```python
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation, Message
from messenger.utils import get_widget_session

User = get_user_model()


class PrivateMessagesTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(inbox=self.inbox, name="Client", email="c@x.ru")
        self.conv = Conversation.objects.create(inbox=self.inbox, contact=self.contact)
        self.op = User.objects.create_user("op", password="pw", role=User.Role.MANAGER, branch=self.branch)

    def test_private_message_excluded_from_widget_poll(self):
        Message.objects.create(
            conversation=self.conv, author=self.op, content="Публичное", is_private=False,
            kind=Message.Kind.OUTGOING,
        )
        Message.objects.create(
            conversation=self.conv, author=self.op, content="ПРИВАТНО", is_private=True,
            kind=Message.Kind.OUTGOING,
        )

        # Создаём widget session и дергаем poll endpoint
        session = get_widget_session(self.conv, create=True)
        client = Client()
        resp = client.get(
            f"/api/messenger/widget/conversations/{self.conv.id}/poll/",
            HTTP_X_WIDGET_TOKEN=self.inbox.widget_token,
            HTTP_X_WIDGET_SESSION=session.session_token,
        )
        self.assertEqual(resp.status_code, 200)
        contents = [m["content"] for m in resp.json().get("messages", [])]
        self.assertIn("Публичное", contents)
        self.assertNotIn("ПРИВАТНО", contents)

    def test_private_message_visible_in_operator_api(self):
        Message.objects.create(
            conversation=self.conv, author=self.op, content="ПРИВАТНО", is_private=True,
            kind=Message.Kind.OUTGOING,
        )
        client = Client()
        client.force_login(self.op)
        resp = client.get(f"/api/messenger/conversations/{self.conv.id}/messages/")
        self.assertEqual(resp.status_code, 200)
        contents = [m["content"] for m in resp.json().get("results", resp.json())]
        self.assertIn("ПРИВАТНО", contents)
```

**Замечание:** точные имена полей `kind`/`Kind.OUTGOING` и путей endpoint могут отличаться — перед написанием теста проверь `backend/messenger/models.py` `class Message` и `backend/messenger/urls.py`. Подправь имена констант под реальные.

- [ ] **Step 4: Тест должен FAIL (первый — проходит только если случайно не фильтруется; второй — может уже работать)**

Run: `scripts/test.sh messenger.tests.test_private_messages.PrivateMessagesTests.test_private_message_excluded_from_widget_poll`
Expected: FAIL — приватное сообщение попадает в виджет.

- [ ] **Step 5: Добавить фильтр в widget_api**

В `backend/messenger/widget_api.py` найти функции, отдающие сообщения виджету (обычно `widget_poll_messages` или SSE-стрим `widget_stream`), и добавить `.filter(is_private=False)` ко всем querysets сообщений.

Примерно:
```python
# было:
messages = Message.objects.filter(conversation=conv).order_by("id")
# стало:
messages = Message.objects.filter(conversation=conv, is_private=False).order_by("id")
```

Применить ко ВСЕМ местам в `widget_api.py`, где сообщения отдаются виджету: poll, SSE stream, bootstrap initial load.

- [ ] **Step 6: Тест PASS**

Run: `scripts/test.sh messenger.tests.test_private_messages`
Expected: оба теста passed.

- [ ] **Step 7: Commit**

```bash
git add backend/messenger/models.py backend/messenger/migrations/0018_message_is_private.py backend/messenger/widget_api.py backend/messenger/tests/test_private_messages.py
git commit -m "Feat(Messenger): add Message.is_private + widget filter"
```

---

## Task 6: Поле `Conversation.needs_help` (эскалация)

**Files:**
- Modify: `backend/messenger/models.py`
- Create: `backend/messenger/migrations/0019_conversation_needs_help.py`

Это мелкая задача, один коммит без тестов (использование придёт в Плане 3).

- [ ] **Step 1: Добавить поле**

В `class Conversation`:

```python
    needs_help = models.BooleanField(
        "Требуется помощь руководителя",
        default=False,
        db_index=True,
    )
    needs_help_at = models.DateTimeField(null=True, blank=True)
```

- [ ] **Step 2: Миграция и применение**

Run: `cd backend && python manage.py makemigrations messenger --name conversation_needs_help && python manage.py migrate messenger`

- [ ] **Step 3: Commit**

```bash
git add backend/messenger/models.py backend/messenger/migrations/0019_conversation_needs_help.py
git commit -m "Feat(Messenger): add Conversation.needs_help escalation flag"
```

---

## Task 7: Сервис `MultiBranchRouter` — выбор целевого филиала по региону

**Files:**
- Create: `backend/messenger/assignment_services/region_router.py`
- Modify: `backend/messenger/tests/test_auto_assign.py` (добавить класс)

- [ ] **Step 1: Failing test**

В `backend/messenger/tests/test_auto_assign.py` добавить:

```python
from accounts.models import BranchRegion
from messenger.assignment_services.region_router import MultiBranchRouter


class MultiBranchRouterTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")
        self.krd = Branch.objects.create(name="Краснодар", code="krd")
        BranchRegion.objects.create(branch=self.ekb, region_name="Свердловская область")
        BranchRegion.objects.create(branch=self.tmn, region_name="Томская область")
        BranchRegion.objects.create(branch=self.krd, region_name="Ростовская область")
        # общий пул
        for b in (self.ekb, self.tmn, self.krd):
            BranchRegion.objects.create(branch=b, region_name="Москва, Московская область", is_common_pool=True)

        self.inbox = Inbox.objects.create(name="Widget", branch=self.ekb)
        self.contact = Contact.objects.create(inbox=self.inbox, name="C")

    def test_region_maps_to_exact_branch(self):
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Томская область",
        )
        branch = MultiBranchRouter().route(conv)
        self.assertEqual(branch, self.tmn)

    def test_unknown_region_falls_back_to_ekb(self):
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Нет такого региона",
        )
        branch = MultiBranchRouter().route(conv)
        self.assertEqual(branch, self.ekb)

    def test_empty_region_falls_back_to_ekb(self):
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="",
        )
        branch = MultiBranchRouter().route(conv)
        self.assertEqual(branch, self.ekb)

    def test_common_pool_picks_round_robin_branch(self):
        """Для регионов общего пула — выбор филиала round-robin (тест на детерминированность)."""
        router = MultiBranchRouter()
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Москва, Московская область",
        )
        branch = router.route(conv)
        # должен быть ОДИН из 3 филиалов
        self.assertIn(branch, [self.ekb, self.tmn, self.krd])
```

- [ ] **Step 2: Реализовать роутер**

Файл `backend/messenger/assignment_services/region_router.py`:

```python
"""
MultiBranchRouter — выбор целевого филиала для нового диалога по региону клиента.

Логика:
1. Если regionимеется и есть точное совпадение с BranchRegion (is_common_pool=False) → этот branch.
2. Если регион в общем пуле (is_common_pool=True) → round-robin между филиалами пула.
3. Fallback: филиал Екатеринбург (code="ekb"). Если его нет в БД — первый созданный филиал.
"""

from typing import Optional
from django.core.cache import cache
from accounts.models import Branch, BranchRegion
from messenger.models import Conversation


class MultiBranchRouter:
    COMMON_POOL_RR_KEY = "messenger:region_router:common_pool_rr"
    FALLBACK_BRANCH_CODE = "ekb"

    def route(self, conversation: Conversation) -> Optional[Branch]:
        region = (conversation.client_region or "").strip()

        if region:
            # 1. точное совпадение (не пул)
            exact = (
                BranchRegion.objects
                .select_related("branch")
                .filter(region_name=region, is_common_pool=False)
                .first()
            )
            if exact:
                return exact.branch

            # 2. общий пул?
            pool = (
                BranchRegion.objects
                .filter(region_name=region, is_common_pool=True)
                .select_related("branch")
            )
            if pool.exists():
                return self._pick_common_pool_branch(
                    [br.branch for br in pool]
                )

        # 3. fallback
        return self._fallback()

    def _pick_common_pool_branch(self, branches: list[Branch]) -> Branch:
        """Round-robin между филиалами общего пула. Состояние — в Redis."""
        if not branches:
            return self._fallback()
        branches_sorted = sorted(branches, key=lambda b: b.id)
        ids = [b.id for b in branches_sorted]

        last_idx = cache.get(self.COMMON_POOL_RR_KEY, -1)
        next_idx = (last_idx + 1) % len(ids)
        cache.set(self.COMMON_POOL_RR_KEY, next_idx, timeout=60 * 60 * 24 * 7)

        picked_id = ids[next_idx]
        return next(b for b in branches_sorted if b.id == picked_id)

    def _fallback(self) -> Optional[Branch]:
        branch = Branch.objects.filter(code=self.FALLBACK_BRANCH_CODE).first()
        if branch:
            return branch
        return Branch.objects.order_by("id").first()
```

- [ ] **Step 3: Тесты PASS**

Run: `scripts/test.sh messenger.tests.test_auto_assign.MultiBranchRouterTests`
Expected: 4 tests passed.

- [ ] **Step 4: Commit**

```bash
git add backend/messenger/assignment_services/region_router.py backend/messenger/tests/test_auto_assign.py
git commit -m "Feat(Messenger): add MultiBranchRouter for region-based routing"
```

---

## Task 8: `BranchLoadBalancer` — выбор наименее загруженного онлайн-менеджера филиала

**Files:**
- Create: `backend/messenger/assignment_services/branch_load_balancer.py`
- Modify: `backend/messenger/tests/test_auto_assign.py`

- [ ] **Step 1: Failing test**

Добавить в `test_auto_assign.py`:

```python
from messenger.assignment_services.branch_load_balancer import BranchLoadBalancer


class BranchLoadBalancerTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(inbox=self.inbox, name="C")

        self.op_free = User.objects.create_user(
            "op_free", password="pw", role=User.Role.MANAGER, branch=self.branch,
            messenger_online=True,
        )
        self.op_loaded = User.objects.create_user(
            "op_loaded", password="pw", role=User.Role.MANAGER, branch=self.branch,
            messenger_online=True,
        )
        self.op_offline = User.objects.create_user(
            "op_offline", password="pw", role=User.Role.MANAGER, branch=self.branch,
            messenger_online=False,
        )

        # op_loaded имеет 3 открытых диалога
        for _ in range(3):
            Conversation.objects.create(
                inbox=self.inbox, contact=self.contact,
                assignee=self.op_loaded, branch=self.branch,
                status=Conversation.Status.OPEN,
            )

    def test_picks_least_loaded_online_manager(self):
        picked = BranchLoadBalancer().pick(self.branch)
        self.assertEqual(picked, self.op_free)

    def test_offline_manager_excluded(self):
        # переводим free в offline
        self.op_free.messenger_online = False
        self.op_free.save()
        picked = BranchLoadBalancer().pick(self.branch)
        self.assertEqual(picked, self.op_loaded)

    def test_returns_none_when_nobody_online(self):
        User.objects.filter(branch=self.branch).update(messenger_online=False)
        picked = BranchLoadBalancer().pick(self.branch)
        self.assertIsNone(picked)

    def test_non_manager_excluded(self):
        self.op_free.role = User.Role.BRANCH_DIRECTOR
        self.op_free.save()
        picked = BranchLoadBalancer().pick(self.branch)
        self.assertEqual(picked, self.op_loaded)
```

- [ ] **Step 2: Реализация**

Файл `backend/messenger/assignment_services/branch_load_balancer.py`:

```python
"""
BranchLoadBalancer — выбор менеджера филиала с минимальным количеством открытых диалогов.

Только онлайн-менеджеры (messenger_online=True).
Только role=MANAGER.
При равенстве нагрузки — случайный (order_by('?')).
"""

from typing import Optional
from django.db.models import Count, Q
from accounts.models import Branch, User
from messenger.models import Conversation


class BranchLoadBalancer:
    def pick(self, branch: Branch) -> Optional[User]:
        candidates = (
            User.objects
            .filter(
                branch=branch,
                role=User.Role.MANAGER,
                is_active=True,
                messenger_online=True,
            )
            .annotate(
                active_count=Count(
                    "messenger_assigned_conversations",
                    filter=Q(messenger_assigned_conversations__status=Conversation.Status.OPEN),
                    distinct=True,
                )
            )
            .order_by("active_count", "?")
        )
        return candidates.first()
```

**Замечание:** `related_name` для `Conversation.assignee` — проверь в `backend/messenger/models.py` (может быть `assigned_conversations`, `messenger_conversations` или другое). Подставь реальное имя.

- [ ] **Step 3: Тесты PASS**

Run: `scripts/test.sh messenger.tests.test_auto_assign.BranchLoadBalancerTests`
Expected: 4 tests passed.

- [ ] **Step 4: Commit**

```bash
git add backend/messenger/assignment_services/branch_load_balancer.py backend/messenger/tests/test_auto_assign.py
git commit -m "Feat(Messenger): add BranchLoadBalancer (least-loaded online manager)"
```

---

## Task 9: Оркестратор `auto_assign` + триггер через сигнал

**Files:**
- Create: `backend/messenger/assignment_services/auto_assign.py`
- Create: `backend/messenger/signals.py` (если нет)
- Modify: `backend/messenger/apps.py` — подключить сигналы
- Modify: `backend/messenger/tests/test_auto_assign.py`

- [ ] **Step 1: Failing integration test**

Добавить в `test_auto_assign.py`:

```python
from messenger.assignment_services.auto_assign import auto_assign_conversation


class AutoAssignIntegrationTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")
        BranchRegion.objects.create(branch=self.tmn, region_name="Томская область")

        self.op_ekb = User.objects.create_user(
            "op_ekb", password="pw", role=User.Role.MANAGER, branch=self.ekb,
            messenger_online=True,
        )
        self.op_tmn = User.objects.create_user(
            "op_tmn", password="pw", role=User.Role.MANAGER, branch=self.tmn,
            messenger_online=True,
        )

        self.inbox = Inbox.objects.create(name="Widget", branch=self.ekb)
        self.contact = Contact.objects.create(inbox=self.inbox, name="C")

    def test_regional_conversation_assigned_to_branch_manager(self):
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Томская область",
        )
        result = auto_assign_conversation(conv)
        conv.refresh_from_db()
        self.assertEqual(conv.assignee, self.op_tmn)
        self.assertEqual(conv.branch, self.tmn)
        self.assertTrue(result["assigned"])
        self.assertEqual(result["branch"], self.tmn)

    def test_no_online_manager_leaves_pool(self):
        self.op_tmn.messenger_online = False
        self.op_tmn.save()
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Томская область",
        )
        result = auto_assign_conversation(conv)
        conv.refresh_from_db()
        self.assertIsNone(conv.assignee)
        # branch всё равно устанавливается — для видимости РОПа филиала
        self.assertEqual(conv.branch, self.tmn)
        self.assertFalse(result["assigned"])
        self.assertEqual(result["branch"], self.tmn)
```

- [ ] **Step 2: Реализация оркестратора**

Файл `backend/messenger/assignment_services/auto_assign.py`:

```python
"""
Оркестратор автоназначения нового диалога.

Pipeline:
  MultiBranchRouter.route → Branch
  BranchLoadBalancer.pick → User (или None)
  Обновление Conversation.assignee/branch
  Fallback pool: если никого онлайн — branch всё равно ставится, assignee=None.
"""

from dataclasses import dataclass
from typing import Optional
from messenger.models import Conversation
from accounts.models import Branch, User
from messenger.assignment_services.region_router import MultiBranchRouter
from messenger.assignment_services.branch_load_balancer import BranchLoadBalancer


@dataclass
class AutoAssignResult:
    assigned: bool
    branch: Optional[Branch]
    user: Optional[User]

    def __getitem__(self, key):
        return getattr(self, key)


def auto_assign_conversation(conversation: Conversation) -> AutoAssignResult:
    router = MultiBranchRouter()
    balancer = BranchLoadBalancer()

    branch = router.route(conversation)
    if not branch:
        return AutoAssignResult(assigned=False, branch=None, user=None)

    user = balancer.pick(branch)

    # В любом случае закрепляем branch (РОП филиала должен видеть)
    conversation.branch = branch
    if user:
        conversation.assignee = user
        conversation.save(update_fields=["branch", "assignee"])
        return AutoAssignResult(assigned=True, branch=branch, user=user)
    else:
        conversation.save(update_fields=["branch"])
        return AutoAssignResult(assigned=False, branch=branch, user=None)
```

- [ ] **Step 3: Тесты PASS**

Run: `scripts/test.sh messenger.tests.test_auto_assign.AutoAssignIntegrationTests`
Expected: 2 tests passed.

- [ ] **Step 4: Подключить через сигнал при создании диалога**

Создать/открыть `backend/messenger/signals.py`:

```python
from django.db.models.signals import post_save
from django.dispatch import receiver
from messenger.models import Conversation


@receiver(post_save, sender=Conversation)
def auto_assign_new_conversation(sender, instance: Conversation, created: bool, **kwargs):
    if not created:
        return
    if instance.assignee_id:
        return
    if instance.status != Conversation.Status.OPEN:
        return
    # Ленивый импорт чтобы избежать circular при миграциях
    from messenger.assignment_services.auto_assign import auto_assign_conversation
    try:
        auto_assign_conversation(instance)
    except Exception:
        import logging
        logging.getLogger("messenger.auto_assign").exception(
            "auto_assign failed for conversation %s", instance.pk
        )
```

В `backend/messenger/apps.py` в `ready()`:

```python
class MessengerConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "messenger"

    def ready(self):
        from messenger import signals  # noqa: F401
```

- [ ] **Step 5: Интеграционный тест через сигнал**

Добавить тест в `AutoAssignIntegrationTests`:

```python
    def test_signal_triggers_auto_assign_on_create(self):
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact,
            client_region="Томская область",
        )
        conv.refresh_from_db()
        self.assertEqual(conv.assignee, self.op_tmn)
        self.assertEqual(conv.branch, self.tmn)
```

Run: `scripts/test.sh messenger.tests.test_auto_assign`
Expected: все тесты passed.

- [ ] **Step 6: Commit**

```bash
git add backend/messenger/assignment_services/auto_assign.py backend/messenger/signals.py backend/messenger/apps.py backend/messenger/tests/test_auto_assign.py
git commit -m "Feat(Messenger): orchestrate auto-assign on Conversation create via signal"
```

---

## Task 10: Ролевая видимость диалогов

**Files:**
- Modify: `backend/messenger/selectors.py` — добавить `get_visible_conversations(user)`
- Create: `backend/messenger/tests/test_visibility.py`

- [ ] **Step 1: Failing tests**

Файл `backend/messenger/tests/test_visibility.py`:

```python
from django.test import TestCase
from django.contrib.auth import get_user_model
from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation
from messenger.selectors import get_visible_conversations

User = get_user_model()


class VisibilityTests(TestCase):
    def setUp(self):
        self.ekb = Branch.objects.create(name="ЕКБ", code="ekb")
        self.tmn = Branch.objects.create(name="Тюмень", code="tmn")

        self.mgr_ekb_1 = User.objects.create_user("m1", password="pw", role=User.Role.MANAGER, branch=self.ekb)
        self.mgr_ekb_2 = User.objects.create_user("m2", password="pw", role=User.Role.MANAGER, branch=self.ekb)
        self.mgr_tmn = User.objects.create_user("m3", password="pw", role=User.Role.MANAGER, branch=self.tmn)
        self.director_ekb = User.objects.create_user("d1", password="pw", role=User.Role.BRANCH_DIRECTOR, branch=self.ekb)
        self.rop_ekb = User.objects.create_user("r1", password="pw", role=User.Role.SALES_HEAD, branch=self.ekb)
        self.admin = User.objects.create_superuser("admin", password="pw")

        self.inbox_ekb = Inbox.objects.create(name="Widget", branch=self.ekb)
        self.inbox_tmn = Inbox.objects.create(name="Widget TMN", branch=self.tmn)

        def mk_conv(inbox, assignee, branch):
            contact = Contact.objects.create(inbox=inbox, name=f"C-{assignee}")
            return Conversation.objects.create(
                inbox=inbox, contact=contact, assignee=assignee, branch=branch
            )

        self.conv_m1_assigned = mk_conv(self.inbox_ekb, self.mgr_ekb_1, self.ekb)
        self.conv_m2_assigned = mk_conv(self.inbox_ekb, self.mgr_ekb_2, self.ekb)
        self.conv_ekb_pool = mk_conv(self.inbox_ekb, None, self.ekb)
        self.conv_tmn_assigned = mk_conv(self.inbox_tmn, self.mgr_tmn, self.tmn)
        self.conv_tmn_pool = mk_conv(self.inbox_tmn, None, self.tmn)

    def test_manager_sees_own_plus_own_branch_pool(self):
        visible = set(get_visible_conversations(self.mgr_ekb_1).values_list("id", flat=True))
        self.assertIn(self.conv_m1_assigned.id, visible)
        self.assertIn(self.conv_ekb_pool.id, visible)
        self.assertNotIn(self.conv_m2_assigned.id, visible)
        self.assertNotIn(self.conv_tmn_pool.id, visible)

    def test_director_sees_whole_branch(self):
        visible = set(get_visible_conversations(self.director_ekb).values_list("id", flat=True))
        self.assertIn(self.conv_m1_assigned.id, visible)
        self.assertIn(self.conv_m2_assigned.id, visible)
        self.assertIn(self.conv_ekb_pool.id, visible)
        self.assertNotIn(self.conv_tmn_assigned.id, visible)
        self.assertNotIn(self.conv_tmn_pool.id, visible)

    def test_rop_sees_whole_branch_same_as_director(self):
        visible = set(get_visible_conversations(self.rop_ekb).values_list("id", flat=True))
        self.assertEqual(
            visible,
            {self.conv_m1_assigned.id, self.conv_m2_assigned.id, self.conv_ekb_pool.id},
        )

    def test_admin_sees_everything(self):
        visible = set(get_visible_conversations(self.admin).values_list("id", flat=True))
        self.assertEqual(
            visible,
            {
                self.conv_m1_assigned.id,
                self.conv_m2_assigned.id,
                self.conv_ekb_pool.id,
                self.conv_tmn_assigned.id,
                self.conv_tmn_pool.id,
            },
        )
```

- [ ] **Step 2: Запустить тест — FAIL (функции нет)**

Run: `scripts/test.sh messenger.tests.test_visibility`
Expected: ImportError или AttributeError.

- [ ] **Step 3: Реализовать селектор**

В `backend/messenger/selectors.py` в конец файла:

```python
from django.db.models import Q
from messenger.models import Conversation
from accounts.models import User


def get_visible_conversations(user) -> "QuerySet[Conversation]":
    """
    Возвращает queryset диалогов, видимых пользователю согласно роли.

    - is_superuser / ADMIN: всё
    - BRANCH_DIRECTOR / SALES_HEAD: все диалоги своего филиала + пул своего филиала
    - GROUP_MANAGER: диалоги его подчинённых
    - MANAGER: свои диалоги + пул своего филиала
    - Остальные: только свои назначенные
    """
    if user.is_superuser or getattr(user, "role", None) == User.Role.ADMIN:
        return Conversation.objects.all()

    role = getattr(user, "role", None)
    branch_id = getattr(user, "branch_id", None)

    if role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
        if not branch_id:
            return Conversation.objects.none()
        return Conversation.objects.filter(
            Q(branch_id=branch_id) | Q(inbox__branch_id=branch_id)
        ).distinct()

    if role == User.Role.GROUP_MANAGER:
        sub_ids = list(User.objects.filter(group_manager=user).values_list("id", flat=True)) \
            if hasattr(User, "group_manager") else [user.id]
        return Conversation.objects.filter(assignee_id__in=sub_ids)

    if role == User.Role.MANAGER:
        if not branch_id:
            return Conversation.objects.filter(assignee=user)
        return Conversation.objects.filter(
            Q(assignee=user)
            | Q(assignee__isnull=True, branch_id=branch_id)
        ).distinct()

    return Conversation.objects.filter(assignee=user)
```

**Замечание:** если в `User` нет поля `group_manager` / нет понятия «подчинённые» — GROUP_MANAGER fallback видит только свои. Если в проекте есть другая модель иерархии — подставь её.

- [ ] **Step 4: Тест PASS**

Run: `scripts/test.sh messenger.tests.test_visibility`
Expected: 4 tests passed.

- [ ] **Step 5: Commit**

```bash
git add backend/messenger/selectors.py backend/messenger/tests/test_visibility.py
git commit -m "Feat(Messenger): add role-based conversation visibility selector"
```

---

## Task 11: Celery task `check_offline_operators` + beat schedule

**Files:**
- Modify: `backend/messenger/tasks.py`
- Modify: `backend/crm/settings.py` (beat schedule)
- Modify: `backend/messenger/tests/test_heartbeat.py`

- [ ] **Step 1: Failing test**

Добавить в `test_heartbeat.py`:

```python
from datetime import timedelta
from django.utils import timezone
from messenger.tasks import check_offline_operators


class CheckOfflineOperatorsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="Br", code="br")
        self.op_active = User.objects.create_user(
            "a", password="pw", role=User.Role.MANAGER, branch=self.branch,
            messenger_online=True, messenger_last_seen=timezone.now(),
        )
        self.op_stale = User.objects.create_user(
            "b", password="pw", role=User.Role.MANAGER, branch=self.branch,
            messenger_online=True,
            messenger_last_seen=timezone.now() - timedelta(seconds=120),
        )

    def test_stale_operator_marked_offline(self):
        check_offline_operators()
        self.op_active.refresh_from_db()
        self.op_stale.refresh_from_db()
        self.assertTrue(self.op_active.messenger_online)
        self.assertFalse(self.op_stale.messenger_online)
```

- [ ] **Step 2: FAIL**

Run: `scripts/test.sh messenger.tests.test_heartbeat.CheckOfflineOperatorsTests`
Expected: ImportError (функции нет).

- [ ] **Step 3: Реализовать task**

В `backend/messenger/tasks.py` в конец:

```python
from datetime import timedelta
from celery import shared_task
from django.utils import timezone


@shared_task(name="messenger.check_offline_operators")
def check_offline_operators(stale_seconds: int = 90):
    """
    Переводит операторов в messenger_online=False, если heartbeat не приходил > stale_seconds.
    Запускается celery-beat каждую минуту.
    """
    from accounts.models import User
    threshold = timezone.now() - timedelta(seconds=stale_seconds)
    updated = User.objects.filter(
        messenger_online=True,
        messenger_last_seen__lt=threshold,
    ).update(messenger_online=False)
    return {"marked_offline": updated}
```

- [ ] **Step 4: Beat schedule**

В `backend/crm/settings.py` найти `CELERY_BEAT_SCHEDULE` и добавить:

```python
CELERY_BEAT_SCHEDULE = {
    # ... существующие
    "messenger-check-offline-operators": {
        "task": "messenger.check_offline_operators",
        "schedule": 60.0,  # раз в минуту
    },
}
```

- [ ] **Step 5: Тест PASS**

Run: `scripts/test.sh messenger.tests.test_heartbeat.CheckOfflineOperatorsTests`
Expected: 1 test passed.

- [ ] **Step 6: Commit**

```bash
git add backend/messenger/tasks.py backend/crm/settings.py backend/messenger/tests/test_heartbeat.py
git commit -m "Feat(Messenger): celery task check_offline_operators + beat schedule"
```

---

## Task 12: Полный прогон тестов + push + staging deploy + smoke

**Files:** — (без кода)

- [ ] **Step 1: Полный прогон всех messenger и accounts тестов**

Run: `scripts/test.sh messenger accounts`
Expected: всё passed. Если что-то упало — починить и перезапустить.

- [ ] **Step 2: Проверить миграции на свежей БД**

```bash
cd backend
python manage.py migrate --run-syncdb
python manage.py makemigrations --dry-run --check
```
Expected: `No changes detected`.

- [ ] **Step 3: Push в main**

```bash
git push origin main
```

- [ ] **Step 4: Staging pull + миграции + пересборка**

```bash
ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172 "cd /opt/proficrm-staging && git pull https://github.com/darbyhtml/proficrm.git main && docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate && docker compose -f docker-compose.staging.yml up -d web"
```

- [ ] **Step 5: Smoke-проверка на staging**

- `curl https://crm-staging.groupprofi.ru/health/` → 200
- Войти в CRM как manager, открыть виджет на vm-f841f9cb.na4u.ru/chat-test.html, отправить сообщение
- Проверить в Django admin: `Conversation.client_region` заполняется, `assignee` автоматически назначен, `branch` = филиал по региону
- Проверить `ConversationTransfer.objects.count()` — новых нет (нормально)
- Проверить `User.messenger_online` — меняется при heartbeat от operator-panel

- [ ] **Step 6: Обновить Obsidian wiki**

Отредактировать `docs/wiki/05-Журнал/Changelog.md` — добавить запись за 2026-04-13:

```markdown
## 2026-04-13

### Feat: Live-chat Backend Foundation
- Региональная автомаршрутизация: новые диалоги назначаются в филиал по `client_region` клиента
- Ролевая видимость: MANAGER видит свои+пул филиала, РОП/директор — весь филиал, ADMIN — всё
- Модель `ConversationTransfer` + endpoint `/api/messenger/conversations/{id}/transfer/` с обязательной причиной
- Поле `Message.is_private` для приватных заметок коллегам (фильтруется из widget SSE)
- Heartbeat `/api/messenger/heartbeat/` + celery task `check_offline_operators`
- Справочник `BranchRegion` с fixture из Положения 2025-2026
- Фундамент для UX-улучшений (планы 2-4 — frontend)
```

Обновить `docs/current-sprint.md` — отметить завершение Плана 1, следующий План 2.

- [ ] **Step 7: Commit документации**

```bash
git add docs/wiki/05-Журнал/Changelog.md docs/current-sprint.md
git commit -m "Docs: Live-chat Backend Foundation — Plan 1 complete"
git push origin main
```

---

## Self-Review Checklist

После завершения всех Task:

- [ ] Все тесты зелёные (`scripts/test.sh messenger accounts`)
- [ ] Нет лишних миграций (`makemigrations --dry-run --check`)
- [ ] Staging health-check OK
- [ ] Автоназначение работает на реальном виджете
- [ ] Heartbeat обновляет `messenger_online`
- [ ] Celery-beat запускает `check_offline_operators` (проверить `docker compose logs celery-beat`)
- [ ] Obsidian wiki обновлён

---

## Выход и следующий шаг

**Что достигнуто:** backend умеет автоматически распределять новые диалоги по региону клиента → филиалу → наименее загруженному онлайн-менеджеру. Ролевая видимость позволяет РОП/директору видеть весь филиал. Appnotes, передачи с аудитом, heartbeat — всё работает через API, но UI ещё старый.

**Следующий план:** План 2 — UI Status Simplification + Operator CTA (упрощённые статусы поверх 3 DB-значений, крупные CTA-кнопки, модалки подтверждения, private notes UI, quick-replies кнопки).
