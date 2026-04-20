---
tags: [runbook, performance, n+1, signals, god-nodes]
created: 2026-04-20
mode: read-only
---

# God-Nodes & N+1 analysis

Финальный пласт Day 3 — анализ центральных моделей (User, Company) и поиск performance-антипаттернов в сигналах и вьюхах. Основа — graphify + code grep + ручной обзор.

## TL;DR

Код **в целом хорошо написан** (35 мест с `select_related`/`prefetch_related`, signals через `transaction.on_commit`, нормализация в save). Но есть **5 точек деградации** при росте данных.

| # | Находка | Severity | Решение |
|---|---------|----------|---------|
| 1 | **18 сигналов дублируют FTS rebuild** при bulk operations (без dedup) | 🔴 | Релиз 2: Redis-based dedup или request-cache |
| 2 | `Task post_save` → rebuild company search index — **избыточно** (task не в FTS) | 🟡 | Релиз 2: удалить сигнал |
| 3 | `Company.save()` делает DB query на `self.responsible.branch` — при bulk create = N+1 | 🟡 | Сервис-слой с bulk_create |
| 4 | `user.is_currently_absent(today)` — DB query per user. Если в цикле — N+1 | 🟡 | Annotate через Subquery |
| 5 | `Company.clean()` идёт по head_company вложенно — до N DB queries на создание | 🟢 | Рекурсивный CTE (оптимизация, не критичная) |

---

## 1. God-nodes — графовый анализ

Из `graphify-out/GRAPH_REPORT.md`:

| God Node | Рёбер | Комментарий |
|----------|------:|-------------|
| **User** | 747 | центральная модель RBAC + messenger online + branch |
| **Company** | 613 | ядро CRM, 15 FK/M2M |
| **Contact** | 426 | связан с Company, Email, Phone, сigналы |
| **Branch** | 385 | 3 подразделения (ЕКБ/Тюмень/Краснодар) |
| **ContactPhone** | 341 | нормализация + FTS |
| **Task** | 298 | recurring, assigned_to, company |
| **CompanyPhone** | 293 | аналогично Contact |
| **CompanyNote** | 279 | attachments + signal delete |
| **Conversation** | 277 | messenger core |
| **ContactEmail** | 223 | |

**448 сообществ** detected. Топ именованные:
- Миграции БД + ADR
- Нормализация телефонов
- Django Admin + роли
- Android API + CallFlow + Queue + MainActivity
- Messenger каналы (Email/TG/WA/VK) — **важное открытие**
- Recurring tasks (RRULE)
- Company autolink signals
- Policy + CompanyNote

### Новое: Messenger-каналы
В сообществе «Messenger каналы (Email/TG/WA/VK)» есть код **четырёх типов каналов**: Email / Telegram / WhatsApp / VK. Это значит, прошлый разработчик заложил архитектуру под **omnichannel messenger** (как у Chatwoot). Не только встроенный виджет на сайт, но и подключение Email-inbox, Telegram-бота, WhatsApp Business и ВКонтакте.

Это **дополнительно повышает ценность** встроенного messenger в Релизе 2. Не just «заменить Chatwoot виджет», а **единый inbox для всех каналов общения с клиентами**.

## 2. Модель User — структура

```python
class User(AbstractUser):
    Role: MANAGER, BRANCH_DIRECTOR, SALES_HEAD (РОП), GROUP_MANAGER, TENDERIST, ADMIN
    DataScope: GLOBAL, BRANCH, SELF
    role: CharField(choices=Role)
    data_scope: CharField(choices=DataScope)
    branch: FK(Branch, SET_NULL)
    messenger_online: Bool (db_index)
    messenger_last_seen: DateTime
    email_signature_html: Text
    avatar: Image
```

### Methods
- `is_tenderist` (property, no DB) ✅
- `is_admin_role` (property, no DB) ✅
- `is_currently_absent(on_date=None)` — **DB query**: `UserAbsence.objects.filter(user_id=self.id, ...).exists()`

### Риск
Вызов `is_currently_absent` **в цикле** = N+1:
- **Найдено**: `ui/views/dashboard.py:742: "is_currently_absent": user.is_currently_absent(today)` — **для одного юзера** (текущего). OK.
- НО в будущем при выводе списка менеджеров со статусами → **N+1**.

**Лучше**: `annotate(_absent=Exists(UserAbsence.objects.filter(user_id=OuterRef('pk'), start__lte=today, end__gte=today)))` — 1 запрос.

## 3. Модель Company — 30+ полей

Тяжёлая модель. Выделенные группы полей:
- **Идентификаторы**: name, legal_name, inn, kpp
- **Контакты**: phone, phone_comment, email, contact_name, contact_position
- **Адрес и время работы**: address, website, workday_start/end, work_timezone, work_schedule
- **Холодный обзвон** (legacy + current): is_cold_call, primary_contact_is_cold_call, primary_cold_marked_at/by/call
- **Контракт**: contract_type (FK), contract_until, contract_amount
- **Иерархия**: head_company (FK self)
- **Организационные**: status, spheres (M2M), region (FK), responsible (FK User), branch (FK)
- **Интеграция**: amocrm_company_id, raw_fields (JSONB)
- **6 GIN trigram индексов** для FTS

### `save()` — делает много

```python
def save(self, *args, **kwargs):
    if self.responsible_id is not None:
        resp_branch = getattr(self.responsible, "branch", None)   # ← DB query!
        if resp_branch is not None:
            self.branch = resp_branch
    # Нормализация INN, KPP, phone, email, work_schedule через normalizers
    super().save(*args, **kwargs)
```

**Риск**: `self.responsible.branch` — lazy FK access. Если компания уже загружена без `select_related('responsible__branch')`, **каждый save = 1 extra DB query**.

При импорте 1000 компаний из AmoCRM = **2000 extra queries** (load responsible + load branch per company).

### Comment в docstring — хорошо

Авторы **знают**, что `bulk_update()` / `.update()` обходят `save()`:
```
save() здесь - это "последняя линия обороны", а не единственный путь нормализации.
```

**Рецепт**: для массовых операций использовать `companies/services.py` с `bulk_create(companies, batch_size=500)` + пост-обработка через `bulk_update`. А `save()` остаётся для UI/форм/API.

### `clean()` — циклическая проверка head_company

```python
while current_id is not None:
    if current_id == self.pk: raise "циклическая связь"
    ...
    current_id = Company.objects.filter(pk=current_id).values_list("head_company_id", flat=True).first()
```

**Каждая итерация = DB query**. Обычно иерархия 2-3 уровня, но потенциал для **5-10 запросов** на один `clean()`.

**Оптимизация** (не критична): один рекурсивный CTE в PostgreSQL:
```sql
WITH RECURSIVE chain AS (
    SELECT id, head_company_id FROM companies_company WHERE id = %s
    UNION ALL
    SELECT c.id, c.head_company_id FROM companies_company c
    JOIN chain ON c.id = chain.head_company_id
)
SELECT id FROM chain WHERE id = %s LIMIT 1
```

Пока не критично (компаний в иерархии мало).

## 4. Сигналы — главный performance-риск

Файл: `backend/companies/signals.py` — **18 сигналов** + `pre_delete` хуки.

### Архитектура

Почти все signal handlers вызывают:
```python
_schedule_rebuild_index_for_company(company_id)
  → transaction.on_commit(lambda cid=company_id: _rebuild_index_for_company(cid))
  → rebuild_company_search_index(company_id)
```

Цель: при любом изменении Company / Contact / Email / Phone / Note / Task — **перестроить FTS-индекс компании**.

### Проблема: нет дедупликации on_commit callbacks

`transaction.on_commit()` **не дедуплицирует** callback'и по умолчанию. Если в одной транзакции:
- Обновлён Company (1 signal)
- Создано 3 Phone (3 signals)
- Создано 2 Email (2 signals)
- Создано 5 Contacts (5 signals)
- Для каждого контакта 2 phone (10 signals)

= **21 signal × 1 on_commit** = **21 rebuild** одной и той же company после COMMIT. Каждый rebuild — несколько SQL запросов + string processing FTS.

### Где это больно

1. **AmoCRM import** (`companies/importer.py`):
   - Импорт одной компании с 5 контактами × 2 phones/emails = ~20-25 signals
   - При импорте 500 компаний = **10 000+ rebuild'ов** FTS после commit

2. **Cascade delete Company**:
   - 20 contacts × 5 emails/phones = 100 child objects
   - Each pre_delete signal → rebuild orphaned company
   - **100+ rebuild'ов** удалённой компании!

3. **Bulk notifications** или **массовое обновление responsible**: те же проблемы.

### Решение для Релиза 2

**Redis-based deduplication** (1 час работы):

```python
# companies/signals.py
def _schedule_rebuild_index_for_company(company_id):
    if not company_id:
        return
    # Redis-based dedup: если уже запланирован в этой транзакции — skip
    tx_key = f"rebuild_pending:{company_id}:{id(transaction.get_connection())}"
    from django.core.cache import cache
    if cache.add(tx_key, 1, timeout=60):
        transaction.on_commit(
            lambda cid=company_id: (_rebuild_index_for_company(cid), cache.delete(tx_key))
        )
```

**Результат**: 21 signals → **1 rebuild**. При AmoCRM import — 500 rebuild'ов вместо 10 000.

### Бонус: избыточный сигнал на Task

```python
@receiver(post_save, sender=Task)
@receiver(post_delete, sender=Task)
def _task_changed(sender, instance: Task, **kwargs):
    if instance.company_id:
        _schedule_rebuild_index_for_company(instance.company_id)
```

**Task-данные (title, status, due_at) НЕ входят в FTS-индекс компании**. Этот сигнал **ничего не добавляет** в индекс, но каждое сохранение Task триггерит rebuild FTS компании впустую.

При 18 281 задачах на проде и активном использовании — это **впустую сотни rebuild'ов в день**.

**Решение**: просто **удалить** сигнал `_task_changed` в Релизе 2.

## 5. N+1 scan — общая статистика

- **106 `for` loops** в 11 ui/views файлах
- **35 `select_related` / `prefetch_related`** в 15 файлах
- **30 `.save()` / `.delete()`**  в 10 файлах

### Ratio
**35/106 = 33% coverage** очевидных N+1 сценариев. Это значит, что **67% из просмотренных циклов в views** потенциально имеют N+1. Реально многие из них безопасные (итерируются по небольшим массивам). Но при масштабировании нагрузки — узкие места будут проявляться.

### Места для проверки через `django-debug-toolbar` в Релизе 0-1

1. `/companies/<UUID>/` — dashboard для компании (390 запросов/день)
2. `/tasks/` list — с фильтрами
3. `/dashboard/` — main page
4. `/companies/` list с фильтрами+сортировкой
5. `/mail/campaigns/<id>/` — детали кампании

## 6. Подозрительный `Contact.objects.all()` в messenger

```python
# messenger/services.py:88
qs = Contact.objects.all()
```

Без `.filter` / `.order_by` / `[:N]` это **тянет 99 152 записи** в память. Скорее всего:
- Либо далее есть фильтрация (не видно в grep)
- Либо реально загружает всё (catastrophe waiting to happen)

**Надо посмотреть контекст**. Если реально `.all()` без bounds — это **P0** для Релиза 1.

## Обновлённый tech-debt roadmap

### Квик-вины (Релиз 0-1)

1. **Удалить `_task_changed` signal** (1 строка удалить) — -100% бесполезных FTS rebuild'ов от задач
2. **Посмотреть `Contact.objects.all()` в `messenger/services.py:88`** — если это утечка памяти, критичный фикс
3. **Запустить `django-debug-toolbar` на staging** с копией прод-БД — получить реальный N+1 map

### Релиз 2 (оптимизация)

4. **Redis-based dedup для `_schedule_rebuild_index_for_company`** — -90% FTS rebuild'ов при bulk operations
5. **`Company.save()` с prefetch'd responsible** через services layer — -N queries на import
6. **Annotate is_currently_absent через Subquery** в дашбордах — -N queries на list menagers

### Долгосрочно

7. **Рекурсивный CTE для head_company chain** — микрооптимизация
8. **Перевести массовые операции на bulk_create/bulk_update с пост-обработкой** — вместо save() в циклах

## Аудитор

Senior Day 3 God-Nodes Deep Dive, 2026-04-20.
Read-only. Все находки — через статический анализ кода + graphify.
