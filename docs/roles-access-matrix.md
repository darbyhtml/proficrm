# CRM ПРОФИ — Карта ролей и доступов (текущее состояние)

**Дата:** 2026-04-15
**Источник:** аудит кода HEAD `1481060` (staging)
**Статус:** AS-IS (текущее состояние). Целевую модель добавим после согласования.

---

## 1. Роли в системе

Определены в [backend/accounts/models.py:30-60](backend/accounts/models.py#L30-L60) через `TextChoices`:

| Код в БД | Русское название (UI) | Русское название (речь) |
|---|---|---|
| `manager` | Менеджер | Менеджер |
| `sales_head` | Руководитель отдела | **РОП** |
| `branch_director` | Директор филиала | Директор подразделения |
| `group_manager` | Управляющий группой | Управляющий |
| `admin` | Администратор | Администратор |

**Важно:** в коде роль РОП называется `sales_head` (Sales Head). В общении — РОП. Возможная точка путаницы для будущих разработчиков.

**Дополнительные поля User:**
- `branch` (ForeignKey → `accounts_branch`) — подразделение сотрудника
- `data_scope` (TextChoices: `GLOBAL` / `BRANCH` / `SELF`) — независимый от роли скоуп данных
  - Используется в мессенджере
  - **НЕ используется** для компаний (там всегда глобальная видимость)
  - **НЕ используется** для задач

---

## 2. Архитектура проверок доступа

Двухуровневая система:

### Уровень 1. Policy Engine (RBAC)
- [backend/policy/engine.py](backend/policy/engine.py) — стержень
- [backend/policy/resources.py:28-149](backend/policy/resources.py#L28-L149) — реестр 146 ресурсов (pages + actions)
- `baseline_allowed_for_role(role, resource)` — дефолтные правила, жёстко в коде
- `PolicyRule` (БД) — переопределения baseline через UI админки
- `@policy_required(resource_type="page", resource="ui:dashboard")` — декоратор на views
- `decide()` → `PolicyDecision`, режим `observe_only` / `enforce`
- Суперпользователь всегда разрешён

### Уровень 2. Per-object checks
- [backend/companies/permissions.py](backend/companies/permissions.py) — `can_edit_company`, `can_transfer_company`, `editable_company_qs`, `get_transfer_targets`
- [backend/companies/policy.py](backend/companies/policy.py) — `visible_companies_qs`, `visible_contacts_qs`, `visible_company_notes_qs`, `can_view_company`
- [backend/tasksapp/policy.py](backend/tasksapp/policy.py) — `visible_tasks_qs`, `can_manage_task_status`
- [backend/messenger/selectors.py](backend/messenger/selectors.py) — `visible_inboxes_qs`, `visible_conversations_qs`, `visible_canned_responses_qs`
- [backend/ui/views/_base.py:227-253](backend/ui/views/_base.py#L227-L253) — `_can_edit_company`, `_can_delete_company`

---

## 3. Матрица «Роль × Сущность × Действие» (AS-IS)

Легенда: ✅ = разрешено, ❌ = запрещено, 🔸 = ограниченно (см. примечание)

### 3.1 Company

| Действие | MANAGER | РОП (sales_head) | BRANCH_DIRECTOR | GROUP_MANAGER | ADMIN | Примечание |
|---|---|---|---|---|---|---|
| Видеть (list/detail) | ✅ все | ✅ все | ✅ все | ✅ все | ✅ все | `visible_companies_qs` = `Q()` (общая база) |
| Создать | 🔸 (ответственный=сам) | 🔸 (филиал свой) | 🔸 (филиал свой) | ✅ | ✅ | `perform_create` в API |
| Редактировать | 🔸 (свои) | 🔸 (филиал + свои) | 🔸 (филиал + свои) | ✅ | ✅ | `can_edit_company` |
| Удалить | ❌ | 🔸 (филиал свой) | 🔸 (филиал свой) | ✅ | ✅ | `_can_delete_company` |
| Запросить удаление | ✅ (свои) | ❌ | ❌ | ❌ | ❌ | `CompanyDeletionRequest` |
| Передать другому | 🔸 (свои) | 🔸 (своих менеджеров филиала) | 🔸 (своих менеджеров филиала) | ✅ | ✅ | `can_transfer_company` |
| Массовая передача | ❌ | 🔸 (филиал свой) | 🔸 (филиал свой) | ✅ | ✅ | baseline: `ui:companies:bulk_transfer` |

### 3.2 Task

| Действие | MANAGER | РОП | BRANCH_DIRECTOR | GROUP_MANAGER | ADMIN | Примечание |
|---|---|---|---|---|---|---|
| Видеть (list) | 🔸 свои | 🔸 филиал + свои | 🔸 филиал + свои | ✅ все | ✅ все | `visible_tasks_qs` |
| Создать | 🔸 (себе) | 🔸 (филиал) | 🔸 (филиал) | ✅ | ✅ | `perform_create` проверяет `assigned_to` |
| Редактировать | 🔸 (создатель/исполнитель) | 🔸 (филиал) | 🔸 (филиал) | ✅ | ✅ | Гибко: создатель/исполнитель получает широкие права |
| Сменить статус | 🔸 (создатель/исполнитель) | 🔸 (филиал) | 🔸 (филиал) | ✅ | ✅ | `can_manage_task_status` |
| Удалить | 🔸 (создатель) | 🔸 (филиал) | 🔸 (филиал) | ✅ | ✅ | baseline: `ui:tasks:delete` = sensitive |
| Массовое переназначение | ❌ | ❌ | ❌ | ❌ | ✅ | Только ADMIN |

### 3.3 Мессенджер (Conversation, Inbox)

| Действие | MANAGER | РОП | BRANCH_DIRECTOR | GROUP_MANAGER | ADMIN | Примечание |
|---|---|---|---|---|---|---|
| Видеть Inbox | 🔸 филиал | 🔸 филиал | 🔸 филиал | 🔸 филиал | ✅ все | `visible_inboxes_qs`: без `branch_id` → ничего |
| Видеть диалоги | 🔸 data_scope | 🔸 филиал | 🔸 филиал | ✅ | ✅ | `visible_conversations_qs` + `data_scope` |
| Отправить сообщение | ✅ (assigned) | ✅ | ✅ | ✅ | ✅ | |
| Участие в round-robin | ✅ | ❌ | ❌ | ❌ | ❌ | Только MANAGER принимает новые диалоги |
| Управлять Inbox | ❌ | ❌ | ❌ | ❌ | ✅ | Только ADMIN |

### 3.4 Настройки (Settings/Админка UI)

| Раздел | MANAGER | РОП | BRANCH_DIRECTOR | GROUP_MANAGER | ADMIN |
|---|---|---|---|---|---|
| Все страницы `settings_*` | ❌ | ❌ | ❌ | ❌ | ✅ |
| Аналитика `ui:analytics` | ❌ | ✅ | ✅ | ✅ | ✅ |

### 3.5 Почта (mailer)

| Действие | MANAGER | РОП | BRANCH_DIRECTOR | GROUP_MANAGER | ADMIN |
|---|---|---|---|---|---|
| Кампании (все действия) | ✅ | ✅ | ✅ | ✅ | ✅ |
| SMTP настройки | ❌ | ❌ | ❌ | ❌ | ✅ |

---

## 4. Нестыковки и дыры (КРИТИЧНО)

### 4.1 Несогласованность `is_staff` и `role`

[backend/ui/forms.py:240-241](backend/ui/forms.py#L240-L241):
```python
user.is_staff = user.role == User.Role.ADMIN
```

**Проблема:** синхронизация вручную в формах, в разных формах может быть забыта. Нет сигнала на модели User.

**Риск:** рассинхронизация → доступ в django-admin без роли ADMIN или наоборот.

**Фикс:** `post_save` signal на User, который всегда приводит `is_staff = (role == ADMIN)`. Либо вообще отказаться от `is_staff` в проекте и ориентироваться только на `role`.

### 4.2 Хардкоды строк ролей в шаблонах

[backend/templates/ui/dashboard.html:105-106](backend/templates/ui/dashboard.html#L105-L106):
```html
{% if request.user.role == 'sales_head' or request.user.role == 'branch_director' %}
```

[backend/templates/ui/messenger_conversations_unified.html](backend/templates/ui/messenger_conversations_unified.html):
```html
{% if user.is_superuser or user.role == 'admin' %}
```

**Проблема:**
- Магические строки, не ссылаются на константы
- Смешение `is_superuser` и `role` без явного правила
- При переименовании ролей шаблоны молча сломаются

**Фикс:** кастомный templatetag `{% if user|has_role:"MANAGER,ADMIN" %}` + context-процессор с флагами `can_view_analytics`, `can_edit_admin` и т.д.

### 4.3 Параллельные реализации логики create/update

[backend/companies/api.py:125-140](backend/companies/api.py#L125-L140) имеет свою проверку ролей, а [backend/companies/permissions.py](backend/companies/permissions.py) — свою. Логика может разойтись.

**Фикс:** `perform_create` вызывает `can_create_company(user, validated_data)`, единая функция.

### 4.4 Policy baseline для API разрешает всё

[backend/policy/engine.py:~200](backend/policy/engine.py):
```python
if resource_key.startswith("api:") or resource_key.startswith("phone:"):
    return True
```

**Риск:** вся защита API зависит от queryset filtering и perform_* checks. Если забыли — IDOR.

**Фикс:** переопределить `get_object()` в ViewSet'ах для ранней проверки, не полагаться только на `perform_update/delete`.

### 4.5 `data_scope` используется непоследовательно

- В мессенджере — работает (`SELF` ограничивает до `assignee_id`)
- В компаниях — **игнорируется** (всегда `Q()`)
- В задачах — **игнорируется** (логика по `role`, а не по `data_scope`)

**Непонятно:** data_scope — задумка, которая не доехала, или осознанное решение «для разных сущностей разный скоуп»? Нужно решение.

### 4.6 `sales_head` в коде vs «РОП» в речи

Это не баг, но источник путаницы. В коде `User.Role.SALES_HEAD = "sales_head"`, в интерфейсе — «Руководитель отдела продаж», в речи — «РОП». Три имени одной роли.

**Вариант:** оставить как есть, задокументировать. Либо переименовать в коде на `ROP` (много правок).

### 4.7 Дефолт `MANAGER` для новой роли может быть опасен

Если при миграции добавления TENDERIST кто-то создаст пользователя без явного указания роли — он станет MANAGER и сможет создавать компании. Надо не забыть указывать роль явно при bulk-операциях.

---

## 5. Что нужно для внедрения роли ТЕНДЕРИСТ

1. **Миграция БД:** `0015_user_role_add_tenderist.py` — добавить `TENDERIST = "tenderist", "Тендерист"` в choices. Migration-level — просто `AlterField(choices=...)`, без изменения данных.

2. **Policy baseline** ([backend/policy/engine.py](backend/policy/engine.py)):
   - `ui:companies:create` → исключить TENDERIST
   - `ui:companies:update` → исключить TENDERIST
   - `ui:companies:delete` → исключить (и так не было)
   - `ui:companies:transfer` → исключить TENDERIST
   - `ui:companies:bulk_transfer` → исключить
   - `ui:tasks:*` → оставить как у менеджера (может создавать себе задачи? — **уточнить**)
   - `ui:mail:*` → ? (**уточнить**)
   - `ui:notes:create` на компаниях — разрешить (тендерист пишет заметки)

3. **Per-object permissions** ([backend/companies/permissions.py](backend/companies/permissions.py)):
   - `can_edit_company()`: первая строка `if user.role == TENDERIST: return False`
   - `can_transfer_company()`: то же
   - `editable_company_qs()`: для TENDERIST вернуть `Company.objects.none()`

4. **Мессенджер** — исключить из round-robin:
   - [backend/messenger/assignment_services/](backend/messenger/assignment_services/) — все селекторы агентов добавить `.exclude(role=User.Role.TENDERIST)`
   - [backend/messenger/selectors.py](backend/messenger/selectors.py): `visible_conversations_qs` для TENDERIST → `none()`? Или показать диалоги только read-only? **Уточнить.**

5. **Задачи** ([backend/tasksapp/policy.py](backend/tasksapp/policy.py)):
   - `visible_tasks_qs()` для TENDERIST — как для MANAGER (свои) или полностью скрыть? **Уточнить.**

6. **Шаблоны** — проверить все хардкоды `role == 'manager'` и расширить до `role in ('manager', 'tenderist')`, где TENDERIST должен наследовать поведение MANAGER.

7. **UI меню:**
   - Скрыть пункт «Создать компанию» для TENDERIST
   - Не показывать кнопки редактирования компании
   - Показывать предупреждение «Вы — тендерист, компании видите только для чтения» где уместно

---

## 6. Статистика

- Всего `@policy_required` декораторов: **~92**
- Всего `if user.role == / in (...)` проверок в Python: **50+**
- Всего вызовов `can_edit_company / can_transfer / can_delete`: **40+**
- Всего `visible_*_qs` функций: **7**
- Всего проверок в API `perform_*`: **15+**

---

## 7. Целевая модель (TO-BE) — согласовано 2026-04-15

### 7.1 Решения по Q11–Q17

| # | Вопрос | Решение |
|---|---|---|
| Q11 | Переименование `sales_head` → `rop` в коде | **Нет.** В коде остаётся `sales_head`. В UI везде — «РОП». Исключительно текст, без миграций. |
| Q12 | Судьба поля `data_scope` | **Оставить как есть.** Работает только в мессенджере, в компаниях/задачах игнорируется. Документировано. Вернуться отдельной задачей (отложено). |
| Q13 | Задачи для ТЕНДЕРИСТ | Видит и управляет **своими** задачами (как менеджер). Может создавать напоминания себе. Если в будущем окажется, что не пользуются — скроем меню. |
| Q14 | Почта для ТЕНДЕРИСТ | Полный доступ как у менеджера (не блокируем). |
| Q15 | Мессенджер для ТЕНДЕРИСТ | **Полностью скрыт раздел.** Не видит, не участвует в round-robin. |
| Q16 | Тендерист как ответственный компании | **Запрещено.** Тендерист никогда не может быть `responsible` компании. |
| Q17 | Чинить хардкоды is_staff + templates | **Да, оба**, отдельными коммитами до редизайна страниц. |

### 7.2 Целевая роль ТЕНДЕРИСТ (`tenderist`)

**Код роли в БД:** `tenderist`
**Название в UI:** «Тендерист»
**Назначение:** сотрудник тендерного отдела. Читает всю базу компаний для контекста тендеров, пишет заметки, ставит себе задачи-напоминания, но не владеет клиентами и не ведёт переписку в чате.

**Права по сущностям:**

| Сущность | Действие | Разрешено | Примечание |
|---|---|---|---|
| Company | Видеть (list/detail) | ✅ все | Как у всех, общая база |
| Company | Создать | ❌ | Явный запрет в API и UI |
| Company | Редактировать | ❌ | `can_edit_company` = False |
| Company | Быть ответственным | ❌ | Не может оказаться в `responsible_id` |
| Company | Получить по передаче | ❌ | Исключён из `get_transfer_targets` |
| Company | Удалить / запросить удаление | ❌ | Нет прав |
| Contact | Видеть | ✅ | Через видимые компании |
| Contact | Создать/редактировать | ❌ | Нет прав редактирования |
| CompanyNote | Видеть | ✅ | |
| CompanyNote | Создать | ✅ | **Ключевое отличие — пишет заметки** |
| CompanyNote | Редактировать свои | ✅ | Только свои |
| Task | Видеть (list) | ✅ свои | Как менеджер (`assigned_to=self`) |
| Task | Создать | ✅ себе | `assigned_to_id == user.id` |
| Task | Редактировать свои | ✅ | Создатель/исполнитель |
| Task | Массовое переназначение | ❌ | Только ADMIN |
| Mail campaigns | Видеть/создавать/отправлять | ✅ | Как менеджер (в будущем можно ограничить) |
| Mail SMTP settings | ❌ | Только ADMIN |
| Messenger (Inbox, Conversation, Message) | ❌ всё | **Раздел полностью скрыт в меню.** Селекторы возвращают `.none()` |
| Messenger round-robin | ❌ | Исключён из всех `.filter(role__in=...)` в assignment services |
| Notifications | Видеть свои | ✅ | Стандартно |
| Settings / Admin UI | ❌ | Только ADMIN |
| Analytics | ❌ | Как менеджер — нет доступа |

**Реализация (точки правки кода):**

1. **Миграция** [backend/accounts/migrations/0015_user_role_add_tenderist.py](backend/accounts/migrations/) — `AlterField` на `User.role.choices`
2. **Модель** [backend/accounts/models.py](backend/accounts/models.py) — добавить `TENDERIST = "tenderist", "Тендерист"` в `User.Role`
3. **Helper** [backend/accounts/models.py](backend/accounts/models.py) — добавить `User.is_tenderist` property для удобства в шаблонах
4. **Permissions** [backend/companies/permissions.py](backend/companies/permissions.py):
   - `can_edit_company()` → early return `False` для tenderist
   - `can_transfer_company()` → early return `False`
   - `editable_company_qs()` → `.none()` для tenderist
   - `get_transfer_targets()` → `.exclude(role=TENDERIST)` (нельзя передать компанию тендеристу)
5. **Policy baseline** [backend/policy/engine.py](backend/policy/engine.py):
   - `ui:companies:create` — исключить TENDERIST
   - `ui:companies:update` — исключить TENDERIST
   - `ui:companies:delete` — исключить (и так был)
   - `ui:companies:transfer` — исключить
   - `ui:companies:bulk_transfer` — исключить
   - `ui:messenger:*` — исключить ВСЕ
   - `ui:notes:create` — разрешить
6. **Мессенджер**:
   - [backend/messenger/selectors.py](backend/messenger/selectors.py):
     - `visible_inboxes_qs()` → если tenderist, вернуть `.none()`
     - `visible_conversations_qs()` → если tenderist, вернуть `.none()`
   - [backend/messenger/assignment_services/](backend/messenger/assignment_services/) — все round-robin queries добавить `.exclude(role=TENDERIST)` (защита, даже если он в mainline не попадает)
7. **API** [backend/companies/api.py:perform_create](backend/companies/api.py) — явный `403` если tenderist
8. **Шаблоны**:
   - [backend/templates/ui/base.html](backend/templates/ui/base.html) — скрыть пункт «Мессенджер» если tenderist
   - [backend/templates/ui/company_list.html](backend/templates/ui/company_list.html) — скрыть кнопку «Создать компанию»
   - [backend/templates/ui/company_detail.html](backend/templates/ui/company_detail.html) — скрыть кнопки редактирования, показать плашку «Вы — тендерист, просмотр только для чтения»
   - Все проверки — через новый `has_role` templatetag, без хардкодов строк
9. **Тесты** — обязательно покрыть:
   - `can_edit_company(tenderist, ...)` == False
   - `visible_conversations_qs(tenderist)` == `.none()`
   - API `POST /api/companies/` с tenderist → 403
   - Round-robin не назначает tenderist

