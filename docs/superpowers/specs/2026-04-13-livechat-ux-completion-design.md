# Live-chat UX Completion — Design Spec

**Дата:** 2026-04-13
**Автор:** Claude Code (sp-brainstorming)
**Статус:** Draft → на ревью пользователя
**Подход:** Backend-first (фундамент → оператор-панель → виджет)

---

## 1. Контекст и проблема

Live-chat модуль CRM ПРОФИ влит в main 2026-04-02, базовая функциональность работает (SSE real-time, widget, operator panel, Chatwoot-style). Однако неопытные сотрудники филиалов путаются:

- **A. Непонятный CTA** — не видят уведомлений, не знают что делать с новым диалогом.
- **B. Список диалогов без приоритетов** — не видно, что срочно, что уже ответили.
- **C. Нет контекста клиента** — кто, откуда, компания, история обращений.
- **D. Ошибки действий** — случайно закрывают диалог, не находят шаблоны.
- **E. Путаница в статусах** — 5 внутренних статусов, непонятно когда какой.

Плюс: отсутствует автоматическая маршрутизация по региону согласно «Положению о распределении регионов 2025-2026».

## 2. Цели и нецели

**Цели:**
- Простой и понятный UX для неопытных операторов (MANAGER)
- Автораспределение новых диалогов по региону клиента в нужный филиал
- Ролевая видимость: MANAGER видит своё+пул, РОП/директор филиала — весь филиал, ADMIN — всё
- Защита от ошибок и страх потерять клиента
- Контекст клиента всегда под рукой
- Вся работа в светлой теме, в стилистике существующего CRM
- Тестирование только на staging

**Нецели:**
- Dark mode
- Новые каналы (Telegram/WhatsApp/VK/Email) — отдельная задача
- Капитальный редизайн виджета
- Мобильное приложение оператора
- Перенос истории с/на прод (прод трогаем только по явному разрешению)

## 3. Подход

**Backend-first (Подход 2 из брейншторма).** Порядок работ:

1. **Фундамент backend:** auto-routing по региону, ролевая видимость, модель transfer, поле `client_region`, `User.messenger_online`, автопереходы статусов (Celery)
2. **Оператор-панель:** упрощённые статусы в UI, крупные CTA, защита от ошибок, быстрые шаблоны, правая панель контекста, комбо-уведомления, private notes, эскалация
3. **Виджет:** только косметика и мелкие фиксы (см. §9)

Обоснование: UX на кривом роутинге бесполезен — оператору не придёт правильный диалог.

---

## 4. Архитектура: ролевая видимость и роутинг

### 4.1. Модели и миграции

**4.1.1. `Conversation.client_region`** (новое поле)
```python
client_region = models.CharField(
    max_length=64, blank=True, default="",
    help_text="Регион клиента (определён по GeoIP/анкете/компании)"
)
client_region_source = models.CharField(
    max_length=16,
    choices=[("geoip", "GeoIP"), ("form", "Анкета"), ("company", "Компания"), ("", "Не определён")],
    blank=True, default=""
)
```

**4.1.2. `Inbox.branches`** (новое поле M2M)
```python
branches = models.ManyToManyField(
    "accounts.Branch",
    related_name="messenger_inboxes",
    help_text="Филиалы, участвующие в маршрутизации этого inbox"
)
```
Миграция данных: для существующих inbox — привязать ко всем 4 филиалам (виджет ПРОФИ общий).

**4.1.3. `User.messenger_online`** (новое поле)
```python
messenger_online = models.BooleanField(default=False, db_index=True)
messenger_last_seen = models.DateTimeField(null=True, blank=True)
```
Обновляется по heartbeat из operator-panel.js (раз в 30 секунд).

**4.1.4. Новая модель `ConversationTransfer`**
```python
class ConversationTransfer(models.Model):
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name="transfers")
    from_user = models.ForeignKey(User, null=True, related_name="transfers_from")
    to_user = models.ForeignKey(User, related_name="transfers_to")
    from_branch = models.ForeignKey("accounts.Branch", null=True, related_name="transfers_out")
    to_branch = models.ForeignKey("accounts.Branch", related_name="transfers_in")
    reason = models.TextField()
    cross_branch = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
```

**4.1.5. `Message.is_private`** (новое поле)
```python
is_private = models.BooleanField(default=False, db_index=True)
```
Приватные заметки — видны только сотрудникам, не клиенту. При отправке виджету SSE фильтрует их.

### 4.2. Сервис `messenger/routing.py` (новый файл)

```python
def assign_conversation(conversation: Conversation) -> User | None:
    """
    Автороутинг нового диалога.
    1. Определить регион клиента (GeoIP → анкета → компания → fallback Екатеринбург)
    2. Найти филиал по региону из таблицы Положения (BranchRegion модель/fixture)
    3. Выбрать online-менеджера филиала с минимальной нагрузкой
    4. Если никого — в пул (assignee=None), уведомить РОПа
    """
```

**Справочник `BranchRegion`** (новая модель + fixture из PDF):
```python
class BranchRegion(models.Model):
    branch = models.ForeignKey("accounts.Branch", on_delete=models.CASCADE, related_name="regions")
    region_name = models.CharField(max_length=128, db_index=True)
    is_common_pool = models.BooleanField(default=False, help_text="Общий пул (Мск/СПб/Новг/Пск)")
```
Fixture `branch_regions_2025_2026.json` заполняется из PDF (Екатеринбург 28 регионов, Тюмень 28, Краснодар 28, общий пул 4 региона).

**Логика выбора менеджера:**
```python
def pick_least_loaded_manager(branch: Branch) -> User | None:
    return (
        User.objects
        .filter(branch=branch, role="MANAGER", is_active=True, messenger_online=True)
        .annotate(active_count=Count("assigned_conversations", filter=Q(
            assigned_conversations__status__in=["ASSIGNED", "WAITING"],
            assigned_conversations__updated_at__gte=now() - timedelta(hours=24)
        )))
        .order_by("active_count", "?")  # при равенстве — случайный
        .first()
    )
```

Fallback: если регион не определён или не найден в `BranchRegion` — используется филиал Екатеринбург. Если в ЕКБ нет онлайн-менеджеров — пул.

### 4.3. Ролевая видимость

Расширяем `messenger/selectors.py`:

```python
def get_visible_conversations(user: User) -> QuerySet[Conversation]:
    if user.role == "ADMIN" or user.is_superuser:
        return Conversation.objects.all()
    if user.role in ("BRANCH_DIRECTOR", "SALES_HEAD"):  # SALES_HEAD = РОП
        return Conversation.objects.filter(
            Q(assignee__branch=user.branch)
            | Q(assignee__isnull=True, inbox__branches=user.branch)
        ).distinct()
    if user.role == "GROUP_MANAGER":
        return Conversation.objects.filter(assignee__in=user.subordinates.all())
    # MANAGER
    return Conversation.objects.filter(
        Q(assignee=user)
        | Q(assignee__isnull=True, inbox__branches=user.branch)
    ).distinct()
```

**Read-only флаг:** в template через `{% if conversation.assignee_id != request.user.id %}read-only{% endif %}`. API endpoint `reply` проверяет `assignee == request.user` (или superuser).

### 4.4. Heartbeat онлайн-статуса

Endpoint `POST /api/messenger/heartbeat/` — обновляет `messenger_online=True, messenger_last_seen=now()`.

Celery beat task `check_offline_operators` раз в минуту: всех с `messenger_last_seen < now()-90s` → `messenger_online=False`.

---

## 5. Упрощение статусов (UI-слой)

### 5.1. Маппинг DB → UI

| UI статус | DB значение | Цвет | Иконка | Подсказка |
|---|---|---|---|---|
| 🔴 Новый | `NEW` + `assignee IS NULL` | `bg-red-500` | `bell` | «Никто ещё не взял. Нажми "Взять себе"» |
| 🟡 Ждёт ответа | `ASSIGNED` + `last_customer_msg_at > last_agent_msg_at` | `bg-amber-400` | `message-circle` | «Клиент написал, ждёт твоего ответа» |
| 🔵 В работе | `ASSIGNED` + `last_agent_msg_at >= last_customer_msg_at` | `bg-blue-500` | `hourglass` | «Ты ответил, ждём реакции клиента» |
| ⚪ Завершён | `RESOLVED` / `CLOSED` | `bg-gray-400` | `check` | «Диалог закрыт» |

Реализация: property `Conversation.ui_status` + template tag `{% ui_status_badge conversation %}`.

### 5.2. Автопереходы

- Оператор открыл диалог → `mark_read()` → пересчёт `ui_status`
- Клиент прислал сообщение → `unread=true` → `ui_status` = «Ждёт ответа»
- Оператор нажал «Завершить» → `RESOLVED`, celery task через 24ч → `CLOSED`
- Оператор нажал «Переоткрыть» → `ASSIGNED`

### 5.3. Сортировка списка

По умолчанию — по `ui_status` приоритет + давность последнего сообщения клиента:
1. 🔴 Новые (сверху)
2. 🟡 Ждут (старые клиентские сообщения выше)
3. 🔵 В работе (старые выше)
4. ⚪ Завершённые (внизу или скрыты, toggle «Показать завершённые»)

### 5.4. Индикатор «молчит N минут»

Property `Conversation.waiting_minutes` — разница `now() - last_customer_msg_at` для 🟡. Пороги берутся из единой константы `PolicyConfig.livechat_escalation` (см. §7.4):
- `≥ warn_min` (дефолт 3) → бейдж пульсирует (`animate-pulse`)
- `≥ urgent_min` (дефолт 10) → бейдж оранжевый + текст «⚠ долго ждёт»
- `≥ rop_alert_min` (дефолт 20) → красный + уведомление РОПу
- `≥ pool_return_min` (дефолт 40) → возврат в пул + уведомление всем онлайн-менеджерам филиала

---

## 6. Оператор-панель: CTA, действия, защита от ошибок

### 6.1. Контекстный главный CTA

В шапке открытого диалога — одна большая кнопка (`h-12 text-lg px-6`), остальные действия в `⋯`:

| Состояние | Главная кнопка | Класс | Вторичные (⋯) |
|---|---|---|---|
| 🔴 Новый | «Взять себе» | `bg-green-600 hover:bg-green-700 text-white` | Передать, Назначить |
| 🟡 Мой, клиент ждёт | Поле ввода в focus + «Отправить» | `bg-blue-600` | Передать, Шаблон, Завершить |
| 🔵 Мой, я ответил | Поле ввода + «Отправить» | `bg-blue-600` | Передать, Завершить, Напомнить |
| ⚪ Завершён | «Переоткрыть» | `bg-gray-500` | — |
| 👁 Чужой (read-only РОП) | «Забрать себе» | `bg-orange-500` + подтверждение | Private note, Аудит |

### 6.2. Защита от ошибок (P0)

**Завершить:** модалка подтверждения «Диалог будет закрыт. Клиент получит предложение оценить работу. Продолжить?». Тоаст «Отменить» 5 секунд после закрытия (undo). Реализация — отложенный вызов `resolveConversation()` через `setTimeout`, отмена = `clearTimeout`.

**Передать:** модалка с обязательными полями:
- Получатель (select из online-менеджеров филиала + опция «Передать в другой филиал» → показывается список филиалов → менеджеры)
- Причина (`textarea`, min 5 символов)
- Кнопка «Передать» disabled пока причина короткая
- При `cross_branch=True` → красная предупреждающая полоса «⚠ Передача в другой филиал. Клиент будет помечен пометкой».

**Enter/Shift+Enter:** `Enter` = отправить, `Shift+Enter` = перенос. Подсказка прямо в `placeholder`.

**Черновик:** `beforeunload` + `localStorage.setItem('draft_' + convId, text)`. При открытии диалога — восстановление черновика + баннер «У вас есть несохранённый черновик» + кнопки «Восстановить / Удалить».

### 6.3. Быстрые шаблоны

- Кнопка «📋 Шаблоны» рядом с полем ввода (`h-10 px-4`, подпись «Шаблоны»)
- Поповер со списком + поиск
- Slash-команды: `/` в начале поля → автокомплит
- 4-6 quick-reply кнопок под полем ввода (настраиваются в `/settings/messenger/quick-replies/`)

Модель `QuickReply` уже есть (`CannedResponse`) — используем её, добавляем флаг `is_quick_button` (bool) и сортировку.

### 6.4. Private notes

UI: над полем ввода toggle «💬 Клиенту / 🔒 Заметка для своих». При 🔒:
- Фон поля жёлтый (`bg-amber-50`)
- Плейсхолдер «Заметка видна только сотрудникам»
- Отправленная заметка — жёлтая карточка с иконкой замка, подпись «Видно только сотрудникам»

Backend: `Message.is_private=True`. В `widget_api.py` SSE-стриме фильтрация `.filter(is_private=False)`. В `operator SSE` — без фильтра.

### 6.5. Escalation «Позвать руководителя»

Кнопка в `⋯` → ставит флаг `Conversation.needs_help=True` + `ConversationTransfer(to_user=branch_director, reason='escalation')`. РОП филиала получает push + в его панели появляется фильтр «Просят помощи».

---

## 7. Уведомления (комбо, вариант D)

### 7.1. Вкладка активна

- Пульсация бейджа диалога в списке 3 секунды
- Мягкий звук `static/messenger/sounds/new-message.mp3`
- Громкость настраивается в профиле

### 7.2. Вкладка CRM, но не в мессенджере

- **Favicon:** canvas рисует красную точку поверх, чередование 1 Гц. Новый JS `static/js/favicon-badge.js` (~40 строк).
- **Title:** `(3) ПРОФИ CRM — 2 новых диалога` (обновляется через `document.title`).
- **Sidebar badge:** красная точка-счётчик на иконке «Мессенджер» в left nav.
- Звук.

### 7.3. Вкладка неактивна (другое приложение)

Всё из 7.2 **+ Desktop Notification API:**
```js
new Notification("Новый диалог", {
  body: "Иван Иванов, ООО Ромашка. Регион: Свердловская обл.",
  icon: "/static/img/notification-icon.png",
  tag: "conv-" + convId,  // заменяет предыдущее уведомление того же диалога
});
```
Клик → `window.focus()` + `location.hash = '#conv-' + convId`.

Разрешение запрашивается один раз при входе в мессенджер, с объяснительным баннером «Чтобы не пропустить клиента, разрешите уведомления».

### 7.4. Эскалация (Celery task)

`messenger.tasks.check_silent_conversations` — каждую минуту:

```python
@shared_task
def check_silent_conversations():
    thresholds = PolicyConfig.get("livechat_escalation", default={
        "warn_min": 3, "urgent_min": 10, "rop_alert_min": 20, "pool_return_min": 40
    })
    for conv in Conversation.objects.filter(
        status="ASSIGNED",
        last_customer_msg_at__gt=F("last_agent_msg_at")
    ):
        waiting = (now() - conv.last_customer_msg_at).total_seconds() / 60
        if waiting >= thresholds["pool_return_min"]:
            conv.assignee = None
            conv.save()
            notify_branch_managers(conv)
        elif waiting >= thresholds["rop_alert_min"]:
            notify_rop(conv)
        elif waiting >= thresholds["urgent_min"]:
            send_push(conv.assignee, "⚠ Клиент ждёт 10+ минут")
```

### 7.5. Настройки уведомлений

В профиле оператора (`/settings/messenger/notifications/`):
- Звук (тумблер + громкость)
- Desktop-уведомления (тумблер)
- Тихие часы `22:00 — 08:00`
- Быстрый «Не беспокоить 1 час» в шапке панели

### 7.6. Онлайн/офлайн тумблер

В шапке оператор-панели — большой тумблер `🟢 Онлайн / 🔴 Офлайн`.
- При переходе в офлайн при наличии 🟡 диалогов — confirm «У тебя 2 диалога ждут ответа. Точно уйти в офлайн?»
- При офлайн автороутинг пропускает оператора
- Heartbeat раз в 30с обновляет `messenger_online`

---

## 8. Контекст клиента (правая панель диалога)

### 8.1. Компоненты

**Блок «Клиент»** (верх):
- Имя, должность (из pre-chat формы)
- Email, телефон (кликабельные)
- Регион + источник (🎯 GeoIP / 📝 анкета / 🏢 компания)
- Если регион чужой → жёлтая полоса «⚠ Клиент не из твоего региона» + кнопка «Передать в филиал X»
- Онлайн-индикатор (last_seen)

**Блок «Компания»** (если связана):
- Название, статус, ответственный, филиал
- Счётчики: сделок, звонков, заметок
- Ссылка «Открыть в CRM →»
- Если ответственный ≠ оператор → кнопка «Передать ответственному»
- Если компании нет → кнопка «➕ Создать компанию из диалога»

**Автосвязка:** по `email domain` или `phone` искать `Company.contacts__email` или `Company.contacts__phone`. При найденной единственной — автопривязка к `Conversation.company`. При нескольких — показать список.

**Блок «История обращений»:**
- Все предыдущие `Conversation` этого `Contact` (по email/телефону)
- Дата, UI-статус, тема (первое клиентское сообщение, truncate 40 символов), оценка (если есть)
- Клик → read-only модалка с полной перепиской

**Блок «Быстрые действия»:**
- 📝 Создать задачу (prefill)
- 💼 Создать сделку (prefill)
- 📧 Написать email (prefill)
- 🔗 Открыть карточку компании

**Блок «Метки»:**
- Цветные ConversationLabel toggle
- Существующая модель `Label`/`ConversationLabel` уже есть — используем

**Блок «Аудит диалога»** (свернут):
- Список действий: кто открыл, кто взял, кто передавал (с причинами)
- Для РОПа кнопка «Подробнее» → полный audit log

### 8.2. Responsive

- `≥1280px` — панель всегда видна (ширина 320px)
- `768-1279px` — кнопка `ℹ Контекст` в шапке, выдвигается как drawer справа
- `<768px` — не в scope (мобилка отдельно)

---

## 9. Виджет (минимум)

Капитальных изменений нет. Только:
- Мелкий фикс: auto-reply показывается при первом подключении (сейчас пропускается из-за `since_id`)
- Пре-chat форма: добавить поле «Регион» (optional, с autodetect GeoIP)
- Private-сообщения не попадают в стрим виджета (фильтрация в `widget_api.py`)
- Косметика: выравнивание бейджей, исправление скачка при открытии

---

## 10. План миграций

1. `0XXX_conversation_client_region` — поля `client_region`, `client_region_source`
2. `0XXX_inbox_branches_m2m` — поле `branches` + data migration (все inbox → все филиалы)
3. `0XXX_user_messenger_online` — `messenger_online`, `messenger_last_seen`
4. `0XXX_branch_region` — модель `BranchRegion` + fixture из PDF 2025-2026
5. `0XXX_conversation_transfer` — модель `ConversationTransfer`
6. `0XXX_message_is_private` — поле `is_private`
7. `0XXX_conversation_needs_help` — флаг escalation

Все миграции reversible, data migration в отдельных файлах.

---

## 11. Тестирование (staging only)

**Backend (pytest + scripts/test.sh):**
- `test_routing.py` — выбор филиала по региону, fallback, least-loaded, пул при отсутствии онлайн
- `test_visibility.py` — MANAGER/РОП/ADMIN/GROUP_MANAGER запросы
- `test_transfer.py` — cross-branch, обязательность причины
- `test_private_messages.py` — фильтрация в виджет-стриме
- `test_escalation.py` — все пороги (3/10/20/40 мин) с `freeze_time`

**Frontend (Playwright E2E):**
- Новый диалог → автороутинг → уведомление → оператор взял → ответил → клиент увидел
- 🔴 → 🟡 → 🔵 → ⚪ полный цикл статусов
- Private note не видна в виджете
- Transfer с комментарием
- Favicon badge появляется и исчезает
- Draft сохраняется при reload

**Ручная проверка staging:**
- Второй браузер = виджет на vm-f841f9cb.na4u.ru
- Первый = оператор-панель на crm-staging.groupprofi.ru
- Проверить все 5 болей (A-E)

---

## 12. Риски и компромиссы

| Риск | Митигация |
|---|---|
| Массовые миграции (7 шт) могут упасть на staging | Каждая reversible, прогон на dev-дампе сначала |
| GeoIP может ошибаться → неверный филиал | Явный источник региона (🎯/📝/🏢), кнопка «передать в правильный филиал» всегда видна |
| Desktop-уведомления заблокированы в браузере | Fallback: favicon+title+звук работают без разрешения |
| Celery escalation task нагружает БД | Индексы на `last_customer_msg_at`, `status`, только `ASSIGNED` в запросе |
| Heartbeat раз в 30с × N операторов → нагрузка | Легковесный endpoint, один UPDATE, ~5ms |
| Roles SALES_HEAD (РОП) не существует в БД | Проверить и добавить в миграции accounts если нужно |

---

## 13. Метрики успеха

- Время от входящего сообщения клиента до первого ответа оператора (TTFR) снизилось на 30%
- % диалогов, где оператор ошибочно нажал «Завершить» и тут же переоткрыл — < 2%
- % диалогов с ручной передачей между филиалами — < 10% (значит автороутинг работает)
- Жалоб РОПов «не вижу что происходит в отделе» — 0
- Средняя оценка клиента — не падает

---

## 14. Что НЕ в этом spec

- Каналы Telegram/WhatsApp/VK/Email — отдельный проект
- Bot / автоответы AI — отдельный проект
- Мобильное приложение оператора — отдельный проект
- Аналитика мессенджера (дашборды, отчёты РОПа по KPI) — отдельный проект
- Интеграция с телефонией (звонок из диалога) — отдельный проект (PhoneBridge уже есть, интеграция минимальная)
