# Live-chat Notifications + Escalation Implementation Plan (Plan 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Автоматическая эскалация молчаливых диалогов по таймерам `waiting_minutes` (warn/urgent/rop_alert/pool_return) с in-app уведомлениями операторам/РОПам, звуковым/desktop-notification сигналом о новом сообщении, favicon-бейджем и хранением outcome резолюции.

**Architecture:**
- **Backend:** periodic Celery task `escalate_waiting_conversations` раз в 30 секунд обходит диалоги с `ui_status=WAITING`, вычисляет `waiting_minutes`, создаёт `Notification` записи + payload с `conversation_id` для deep-link, инкрементирует `Conversation.escalation_level` (int 0/1/2/3) для идемпотентности (каждый уровень триггерит событие ровно один раз). На `pool_return_min` диалог возвращается в пул (`.filter(pk=…).update(assignee=None)`).
- **Hranenie rezolyutsii:** новое поле `Conversation.resolution` (JSONField) для outcome+comment из Task 7 Plan 2, заполняется в `ConversationViewSet.partial_update` при переходе в `resolved`.
- **Frontend:** `operator-panel.js` — звук `new-message.mp3` + Desktop Notification API + обновление title; новый файл `static/js/favicon-badge.js` для canvas-бейджа; тост-нотификации поверх эскалаций через существующий `showNotification`; реактивное отображение бейджа эскалации («ждёт 12 мин») в списке диалогов.
- **PolicyConfig:** новое JSONField `livechat_escalation` (дефолты прописаны в `Conversation.escalation_thresholds()`). Админка (Django admin) редактирует JSON.

**Tech Stack:** Django 6, Celery 5.4, django-redis, PostgreSQL JSONField, vanilla JS (Canvas API, Notification API, Audio API).

---

## File Structure

**Модифицируются:**
- `backend/messenger/models.py` — поля `Conversation.resolution`, `Conversation.escalation_level`, `Conversation.last_escalated_at`
- `backend/messenger/migrations/0022_conversation_escalation_fields.py` — новая миграция
- `backend/messenger/tasks.py` — новая таска `escalate_waiting_conversations`
- `backend/messenger/serializers.py` — добавить `resolution`, `escalation_level` в `ConversationSerializer`
- `backend/messenger/api.py` — `ConversationViewSet.partial_update` пишет `resolution`; `resolve` action расширить параметрами outcome/comment
- `backend/policy/models.py` — новое поле `livechat_escalation` JSONField
- `backend/policy/migrations/0002_policyconfig_livechat_escalation.py` — миграция
- `backend/policy/admin.py` — регистрация/exposure
- `backend/crm/settings.py` — `CELERY_BEAT_SCHEDULE` запись для `escalate_waiting_conversations`
- `backend/messenger/static/messenger/operator-panel.js` — звук, desktop notification, title, эскалационный бейдж в списке
- `backend/templates/ui/messenger_conversations_unified.html` — подключение `favicon-badge.js`, `<audio>` элемент
- `docs/current-sprint.md`, `docs/wiki/05-Журнал/Changelog.md`

**Создаются:**
- `backend/messenger/static/messenger/sounds/new-message.mp3` — placeholder (маленький тихий «клик», base64 декодинг в step). Альтернатива: WebAudio beep без файла (используем beep, чтобы не тащить бинарник в git).
- `backend/static/js/favicon-badge.js` — canvas badge renderer
- `backend/messenger/tests/test_escalation.py` — тесты эскалации
- `backend/messenger/tests/test_resolution_field.py` — тесты resolution JSONField

---

## Задачи

### Task 1: Поле `Conversation.resolution` + `escalation_level` + `last_escalated_at`

**Files:**
- Modify: `backend/messenger/models.py`
- Create: `backend/messenger/migrations/0022_conversation_escalation_fields.py`
- Test: `backend/messenger/tests/test_resolution_field.py`

- [ ] **Step 1: Написать падающий тест**

Файл: `backend/messenger/tests/test_resolution_field.py`
```python
from django.test import TestCase
from django.utils import timezone
from accounts.models import User, Branch
from messenger.models import Conversation, Inbox, Contact


class ConversationEscalationFieldsTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(name="Site", channel_type="widget")
        self.contact = Contact.objects.create(
            inbox=self.inbox, name="Client", email="c@example.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch
        )

    def test_resolution_defaults_empty_dict(self):
        self.assertEqual(self.conv.resolution, {})

    def test_resolution_stores_outcome_and_comment(self):
        self.conv.resolution = {"outcome": "success", "comment": "ok"}
        self.conv.save()
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.resolution["outcome"], "success")

    def test_escalation_level_defaults_zero(self):
        self.assertEqual(self.conv.escalation_level, 0)

    def test_last_escalated_at_nullable(self):
        self.assertIsNone(self.conv.last_escalated_at)
```

- [ ] **Step 2: Запустить — должно упасть**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field -v 2`
Ожидание: FAIL — поля не существуют.

- [ ] **Step 3: Добавить поля в модель**

В `backend/messenger/models.py` рядом с `needs_help`/`needs_help_at` добавить:
```python
    resolution = models.JSONField(
        "Итог резолюции",
        blank=True,
        default=dict,
        help_text="Структура {outcome, comment, resolved_at} из resolve modal",
    )
    escalation_level = models.PositiveSmallIntegerField(
        "Уровень эскалации",
        default=0,
        db_index=True,
        help_text="0=нет, 1=warn, 2=urgent, 3=rop_alert, 4=pool_return",
    )
    last_escalated_at = models.DateTimeField(
        "Время последней эскалации",
        null=True,
        blank=True,
    )
```

- [ ] **Step 4: Создать миграцию**

Команда: `bash scripts/manage.sh makemigrations messenger --name conversation_escalation_fields`
Ожидание: создан файл `0022_conversation_escalation_fields.py` с тремя AddField.

Если `scripts/manage.sh` отсутствует — использовать `docker compose -f docker-compose.staging.yml exec web python manage.py makemigrations` или локальный venv через `.venv/Scripts/python backend/manage.py makemigrations`.

- [ ] **Step 5: Прогнать тест — должен пройти**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field -v 2`
Ожидание: `Ran 4 tests ... OK`.

- [ ] **Step 6: Коммит**

```bash
git add backend/messenger/models.py backend/messenger/migrations/0022_conversation_escalation_fields.py backend/messenger/tests/test_resolution_field.py
git commit -m "Feat(Messenger): Plan 3 Task 1 — поля resolution, escalation_level, last_escalated_at"
```

---

### Task 2: `PolicyConfig.livechat_escalation` JSONField + admin

**Files:**
- Modify: `backend/policy/models.py`
- Create: `backend/policy/migrations/0002_policyconfig_livechat_escalation.py`
- Modify: `backend/policy/admin.py`
- Test: `backend/messenger/tests/test_resolution_field.py` (добавить класс `EscalationThresholdsFromPolicyTests`)

- [ ] **Step 1: Написать падающий тест**

Добавить в `backend/messenger/tests/test_resolution_field.py`:
```python
from messenger.models import Conversation
from policy.models import PolicyConfig


class EscalationThresholdsFromPolicyTests(TestCase):
    def test_defaults_when_policy_empty(self):
        cfg = PolicyConfig.load()
        cfg.livechat_escalation = {}
        cfg.save()
        thresholds = Conversation.escalation_thresholds()
        self.assertEqual(thresholds["warn_min"], 3)
        self.assertEqual(thresholds["pool_return_min"], 40)

    def test_policy_overrides_defaults(self):
        cfg = PolicyConfig.load()
        cfg.livechat_escalation = {"warn_min": 5, "pool_return_min": 60}
        cfg.save()
        thresholds = Conversation.escalation_thresholds()
        self.assertEqual(thresholds["warn_min"], 5)
        self.assertEqual(thresholds["pool_return_min"], 60)
        self.assertEqual(thresholds["urgent_min"], 10)  # дефолт сохраняется
```

- [ ] **Step 2: Запустить — упадёт на отсутствии поля**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field.EscalationThresholdsFromPolicyTests -v 2`
Ожидание: FAIL — атрибут `livechat_escalation` не существует.

- [ ] **Step 3: Добавить поле в модель PolicyConfig**

В `backend/policy/models.py` внутри класса `PolicyConfig`:
```python
    livechat_escalation = models.JSONField(
        "Пороги эскалации live-chat (минуты)",
        blank=True,
        default=dict,
        help_text=(
            "Ключи: warn_min, urgent_min, rop_alert_min, pool_return_min. "
            "Пустые значения → дефолты 3/10/20/40."
        ),
    )
```

- [ ] **Step 4: Создать миграцию**

Команда: `.venv/Scripts/python backend/manage.py makemigrations policy --name policyconfig_livechat_escalation`
Ожидание: `0002_policyconfig_livechat_escalation.py`.

- [ ] **Step 5: Проверить что `Conversation.escalation_thresholds()` читает из поля**

Прочитай существующий код `backend/messenger/models.py` — метод `escalation_thresholds()`. Он уже пытается читать `cfg.livechat_escalation`. После добавления поля этот код заработает автоматически — ничего менять в `models.py` не надо.

- [ ] **Step 6: Выставить поле в Django admin**

В `backend/policy/admin.py` (если файл существует — отредактировать `PolicyConfigAdmin.fields`; если нет — создать минимальный admin):
```python
from django.contrib import admin
from .models import PolicyConfig


@admin.register(PolicyConfig)
class PolicyConfigAdmin(admin.ModelAdmin):
    fieldsets = (
        (None, {"fields": ("mode",)}),
        ("Live-chat эскалация", {
            "fields": ("livechat_escalation",),
            "description": "Пороги в минутах: warn_min, urgent_min, rop_alert_min, pool_return_min",
        }),
    )
```

Если admin уже зарегистрирован — только расширить `fieldsets` или `fields`, не ломать существующие разделы.

- [ ] **Step 7: Прогнать тесты**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field -v 2`
Ожидание: все 6 тестов OK.

- [ ] **Step 8: Коммит**

```bash
git add backend/policy/models.py backend/policy/migrations/0002_policyconfig_livechat_escalation.py backend/policy/admin.py backend/messenger/tests/test_resolution_field.py
git commit -m "Feat(Policy): PolicyConfig.livechat_escalation JSONField + admin"
```

---

### Task 3: Celery task `escalate_waiting_conversations`

**Files:**
- Modify: `backend/messenger/tasks.py`
- Modify: `backend/crm/settings.py` (`CELERY_BEAT_SCHEDULE`)
- Create: `backend/messenger/tests/test_escalation.py`

- [ ] **Step 1: Написать падающие тесты**

Файл: `backend/messenger/tests/test_escalation.py`
```python
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import User, Branch
from messenger.models import Conversation, Inbox, Contact, Message
from messenger.tasks import escalate_waiting_conversations
from notifications.models import Notification


class EscalationTaskTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.manager = User.objects.create_user(
            username="m", password="x", role="manager", branch=self.branch
        )
        self.rop = User.objects.create_user(
            username="rop", password="x", role="sales_head", branch=self.branch
        )
        self.inbox = Inbox.objects.create(name="Site", channel_type="widget")
        self.contact = Contact.objects.create(
            inbox=self.inbox, name="Client", email="c@e.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch,
            assignee=self.manager,
        )
        # Имитируем WAITING: клиент написал 5 минут назад, оператор не отвечал
        now = timezone.now()
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=now - timedelta(minutes=5),
            last_agent_msg_at=None,
        )
        self.conv.refresh_from_db()

    def test_warn_level_creates_no_notification(self):
        """warn (3мин) — только бейдж на фронте, Notification не создаётся."""
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.count(), 0)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 1)

    def test_urgent_level_notifies_assignee(self):
        """urgent (10мин) — Notification для assignee."""
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=11),
        )
        escalate_waiting_conversations()
        notifs = Notification.objects.filter(user=self.manager)
        self.assertEqual(notifs.count(), 1)
        self.assertIn("ждёт", notifs.first().title.lower())
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 2)

    def test_rop_alert_notifies_branch_sales_heads(self):
        """rop_alert (20мин) — Notification всем SALES_HEAD филиала."""
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=21),
        )
        escalate_waiting_conversations()
        notifs = Notification.objects.filter(user=self.rop)
        self.assertEqual(notifs.count(), 1)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.escalation_level, 3)

    def test_pool_return_unassigns_and_notifies_branch(self):
        """pool_return (40мин) — assignee=None + уведомление всем онлайн-менеджерам филиала."""
        User.objects.create_user(
            username="m2", password="x", role="manager",
            branch=self.branch, messenger_online=True,
        )
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=41),
        )
        escalate_waiting_conversations()
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.assignee)
        self.assertEqual(self.conv.escalation_level, 4)
        # Notification получил как минимум второй менеджер
        self.assertTrue(Notification.objects.filter(
            payload__conversation_id=self.conv.id
        ).exists())

    def test_idempotent_same_level(self):
        """Повторный вызов на том же уровне не дублирует Notification."""
        Conversation.objects.filter(pk=self.conv.pk).update(
            last_customer_msg_at=timezone.now() - timedelta(minutes=11),
        )
        escalate_waiting_conversations()
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.filter(user=self.manager).count(), 1)

    def test_resolved_conversation_skipped(self):
        Conversation.objects.filter(pk=self.conv.pk).update(
            status=Conversation.Status.RESOLVED,
            last_customer_msg_at=timezone.now() - timedelta(minutes=15),
        )
        escalate_waiting_conversations()
        self.assertEqual(Notification.objects.count(), 0)
```

- [ ] **Step 2: Запустить — упадут**

Команда: `bash scripts/test.sh messenger.tests.test_escalation -v 2`
Ожидание: ImportError на `escalate_waiting_conversations`.

- [ ] **Step 3: Реализовать задачу**

В `backend/messenger/tasks.py` добавить:
```python
@shared_task(name="messenger.escalate_waiting_conversations")
def escalate_waiting_conversations():
    """Эскалация молчаливых диалогов по порогам waiting_minutes.

    Идемпотентна: каждый уровень (warn/urgent/rop_alert/pool_return) триггерит
    события ровно один раз через Conversation.escalation_level.
    """
    from django.db.models import Q
    from django.utils import timezone
    from accounts.models import User
    from notifications.models import Notification
    from messenger.models import Conversation

    thresholds = Conversation.escalation_thresholds()
    now = timezone.now()
    stats = {"warn": 0, "urgent": 0, "rop_alert": 0, "pool_return": 0}

    candidates = Conversation.objects.filter(
        status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING],
        last_customer_msg_at__isnull=False,
    ).exclude(
        last_agent_msg_at__gte=models.F("last_customer_msg_at"),
    )

    for conv in candidates.select_related("assignee", "branch"):
        waiting = (now - conv.last_customer_msg_at).total_seconds() / 60
        target_level = 0
        if waiting >= thresholds["pool_return_min"]:
            target_level = 4
        elif waiting >= thresholds["rop_alert_min"]:
            target_level = 3
        elif waiting >= thresholds["urgent_min"]:
            target_level = 2
        elif waiting >= thresholds["warn_min"]:
            target_level = 1

        if target_level <= conv.escalation_level:
            continue

        if target_level == 1:
            stats["warn"] += 1
        elif target_level == 2 and conv.assignee_id:
            Notification.objects.create(
                user=conv.assignee,
                kind=Notification.Kind.INFO,
                title=f"Клиент ждёт {int(waiting)} мин",
                body=f"Диалог #{conv.id} — {conv.contact.name if conv.contact else ''}",
                url=f"/messenger/?conv={conv.id}",
                payload={"conversation_id": conv.id, "level": "urgent"},
            )
            stats["urgent"] += 1
        elif target_level == 3 and conv.branch_id:
            rops = User.objects.filter(
                branch_id=conv.branch_id,
                role=User.Role.SALES_HEAD,
                is_active=True,
            )
            for rop in rops:
                Notification.objects.create(
                    user=rop,
                    kind=Notification.Kind.INFO,
                    title=f"Клиент ждёт {int(waiting)} мин — требуется вмешательство",
                    body=f"Диалог #{conv.id} у {conv.assignee.get_full_name() if conv.assignee else 'не назначен'}",
                    url=f"/messenger/?conv={conv.id}",
                    payload={"conversation_id": conv.id, "level": "rop_alert"},
                )
            stats["rop_alert"] += 1
        elif target_level == 4 and conv.branch_id:
            Conversation.objects.filter(pk=conv.pk).update(assignee=None)
            branch_managers = User.objects.filter(
                branch_id=conv.branch_id,
                role=User.Role.MANAGER,
                is_active=True,
                messenger_online=True,
            )
            for m in branch_managers:
                Notification.objects.create(
                    user=m,
                    kind=Notification.Kind.INFO,
                    title=f"Диалог возвращён в пул — ждёт {int(waiting)} мин",
                    body=f"Диалог #{conv.id} ожидает свободного оператора",
                    url=f"/messenger/?conv={conv.id}",
                    payload={"conversation_id": conv.id, "level": "pool_return"},
                )
            stats["pool_return"] += 1

        Conversation.objects.filter(pk=conv.pk).update(
            escalation_level=target_level,
            last_escalated_at=now,
        )

    return stats
```

Добавить `from django.db import models` в начало файла, если ещё не импортирован.

- [ ] **Step 4: Прогнать тесты**

Команда: `bash scripts/test.sh messenger.tests.test_escalation -v 2`
Ожидание: `Ran 6 tests ... OK`.

Если `test_pool_return_unassigns_and_notifies_branch` падает потому что оператор-2 не проходит фильтр `messenger_online=True` — исправить фикстуру (в `create_user` уже `messenger_online=True` передаётся).

- [ ] **Step 5: Зарегистрировать в CELERY_BEAT_SCHEDULE**

В `backend/crm/settings.py` найти блок `CELERY_BEAT_SCHEDULE = {...}` и добавить:
```python
    "messenger-escalate-waiting": {
        "task": "messenger.escalate_waiting_conversations",
        "schedule": 30.0,  # секунд
    },
```

- [ ] **Step 6: Регрессия messenger**

Команда: `bash scripts/test.sh messenger`
Ожидание: все тесты OK (115/115 примерно: 109 существующих + 6 новых).

- [ ] **Step 7: Коммит**

```bash
git add backend/messenger/tasks.py backend/messenger/tests/test_escalation.py backend/crm/settings.py
git commit -m "Feat(Messenger): Plan 3 Task 3 — Celery task escalate_waiting_conversations"
```

---

### Task 4: API — resolution в resolve, escalation_level в сериалайзере

**Files:**
- Modify: `backend/messenger/serializers.py`
- Modify: `backend/messenger/api.py`
- Modify: `backend/messenger/tests/test_resolution_field.py` (добавить API-тесты)

- [ ] **Step 1: Написать тесты**

Добавить в `backend/messenger/tests/test_resolution_field.py`:
```python
from rest_framework.test import APIClient


class ResolutionApiTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.user = User.objects.create_user(
            username="m", password="x", role="manager", branch=self.branch
        )
        self.inbox = Inbox.objects.create(name="S", channel_type="widget")
        self.contact = Contact.objects.create(
            inbox=self.inbox, name="C", email="c@e.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch,
            assignee=self.user,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_serializer_exposes_resolution_and_escalation_level(self):
        resp = self.client.get(f"/api/conversations/{self.conv.id}/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("resolution", resp.data)
        self.assertIn("escalation_level", resp.data)

    def test_patch_status_resolved_with_resolution_payload(self):
        resp = self.client.patch(
            f"/api/conversations/{self.conv.id}/",
            {"status": "resolved", "resolution": {"outcome": "success", "comment": "ok"}},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, resp.data)
        self.conv.refresh_from_db()
        self.assertEqual(self.conv.status, Conversation.Status.RESOLVED)
        self.assertEqual(self.conv.resolution["outcome"], "success")
```

- [ ] **Step 2: Запустить — упадёт**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field.ResolutionApiTests -v 2`
Ожидание: `resolution` не в response, или 400 на PATCH.

- [ ] **Step 3: Расширить сериалайзер**

В `backend/messenger/serializers.py` в `ConversationSerializer.Meta.fields` добавить `"resolution"`, `"escalation_level"`, `"last_escalated_at"`.

Важно: `ConversationSerializer.update()` в api.py имеет whitelist полей (см. Plan 2 Task 8 — там был hardcoded список `{status, assignee, priority, labels}`). Нужно добавить `resolution` в whitelist.

- [ ] **Step 4: Расширить whitelist в update**

Открой `backend/messenger/api.py`, найди `ConversationSerializer.update()` или метод `partial_update` ViewSet'а. Добавь ключ `"resolution"` в список allowed полей.

Если whitelist в `serializers.py` — отредактируй там:
```python
    def update(self, instance, validated_data):
        allowed = {"status", "assignee", "priority", "labels", "resolution"}
        for key in list(validated_data.keys()):
            if key not in allowed:
                validated_data.pop(key)
        return super().update(instance, validated_data)
```

(Точное местоположение и название метода — проверить при чтении файла.)

- [ ] **Step 5: Прогнать тесты**

Команда: `bash scripts/test.sh messenger.tests.test_resolution_field.ResolutionApiTests -v 2`
Ожидание: 2 теста PASS.

- [ ] **Step 6: Регрессия**

Команда: `bash scripts/test.sh messenger`
Ожидание: все OK.

- [ ] **Step 7: Коммит**

```bash
git add backend/messenger/serializers.py backend/messenger/api.py backend/messenger/tests/test_resolution_field.py
git commit -m "Feat(Messenger): Plan 3 Task 4 — resolution в сериалайзере и PATCH whitelist"
```

---

### Task 5: Фронт — resolve modal пишет resolution в PATCH

**Files:**
- Modify: `backend/messenger/static/messenger/operator-panel.js`
- Modify: `backend/templates/ui/messenger_conversations_unified.html` (убрать hint про Plan 3)

- [ ] **Step 1: Найти блок resolve modal**

Прочитай `operator-panel.js` — метод `submitResolveModal()` (около строки 2155 по данным Plan 2). Там в `setTimeout` вызывается `this.patchConversation(convId, {status: 'resolved'}, onError)`.

- [ ] **Step 2: Включить resolution в payload**

Заменить payload на:
```js
const payload = {
    status: 'resolved',
    resolution: {
        outcome: this._pendingResolve.outcome,
        comment: this._pendingResolve.comment || '',
        resolved_at: new Date().toISOString(),
    },
};
this.patchConversation(convId, payload, () => {
    this.showNotification('Не удалось завершить диалог — проверьте статус', 'error');
});
```

Убрать TODO-комментарий про Plan 3 рядом с этим местом.

- [ ] **Step 3: Убрать hint из шаблона**

В `backend/templates/ui/messenger_conversations_unified.html` найти `#resolveDialogModal` и строку `<p class="text-xs text-gray-500 mt-1">Комментарий будет сохраняться в карточке диалога (появится позже).</p>` — удалить.

- [ ] **Step 4: Проверить синтаксис**

Команда: `node -c backend/messenger/static/messenger/operator-panel.js`
Ожидание: без ошибок.

- [ ] **Step 5: Регрессия**

Команда: `bash scripts/test.sh messenger`
Ожидание: все тесты OK (резолюция теперь сохраняется и в API-тесте из Task 4).

- [ ] **Step 6: Коммит**

```bash
git add backend/messenger/static/messenger/operator-panel.js backend/templates/ui/messenger_conversations_unified.html
git commit -m "Feat(Messenger): Plan 3 Task 5 — resolve modal сохраняет resolution"
```

---

### Task 6: Фронт — звук нового сообщения + Desktop Notification + title

**Files:**
- Modify: `backend/messenger/static/messenger/operator-panel.js`
- Modify: `backend/templates/ui/messenger_conversations_unified.html`

- [ ] **Step 1: Добавить звук через WebAudio (без бинарника)**

В `MessengerOperatorPanel` добавить метод:
```js
playNotificationSound() {
    try {
        if (this._soundMuted) return;
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (!AudioCtx) return;
        const ctx = this._audioCtx || (this._audioCtx = new AudioCtx());
        const osc = ctx.createOscillator();
        const gain = ctx.createGain();
        osc.type = 'sine';
        osc.frequency.value = 880;
        gain.gain.value = 0.08;
        osc.connect(gain);
        gain.connect(ctx.destination);
        osc.start();
        osc.frequency.exponentialRampToValueAtTime(440, ctx.currentTime + 0.1);
        gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.15);
        osc.stop(ctx.currentTime + 0.18);
    } catch (e) { /* ignore */ }
}
```

- [ ] **Step 2: Добавить Desktop Notification helper**

```js
requestNotificationPermission() {
    if (!('Notification' in window)) return;
    if (Notification.permission === 'default') {
        Notification.requestPermission();
    }
}

showDesktopNotification(conv, message) {
    if (!('Notification' in window)) return;
    if (Notification.permission !== 'granted') return;
    if (!document.hidden && this.currentConversation?.id === conv.id) return;
    try {
        const contactName = conv.contact_name || conv.contact?.name || 'Клиент';
        const notif = new Notification(`Новое сообщение — ${contactName}`, {
            body: (message.text || '').slice(0, 120),
            icon: '/static/img/notification-icon.png',
            tag: `conv-${conv.id}`,
        });
        notif.onclick = () => {
            window.focus();
            this.openConversation(conv.id);
            notif.close();
        };
    } catch (e) { /* ignore */ }
}
```

- [ ] **Step 3: Title-бейдж при неактивной вкладке**

```js
updateTitleBadge(unreadCount) {
    const base = this._titleBase || (this._titleBase = document.title.replace(/^\(\d+\)\s*/, ''));
    document.title = unreadCount > 0 ? `(${unreadCount}) ${base}` : base;
}
```

- [ ] **Step 4: Встроить вызовы**

Найти место обработки нового входящего сообщения (SSE/websocket handler, поиск `direction === 'in'` или `Message.Direction.IN`). Добавить после успешной обработки входящего:
```js
if (message.direction === 'IN' || message.direction === 'in') {
    this.playNotificationSound();
    this.showDesktopNotification(conversation, message);
    if (document.hidden) {
        this._pendingUnread = (this._pendingUnread || 0) + 1;
        this.updateTitleBadge(this._pendingUnread);
    }
}
```

В `openConversation` (или где фокус ставится на диалог) сбросить счётчик:
```js
this._pendingUnread = 0;
this.updateTitleBadge(0);
```

Также добавить `document.addEventListener('visibilitychange', () => { if (!document.hidden) { this._pendingUnread = 0; this.updateTitleBadge(0); } });` в `init()`.

- [ ] **Step 5: Запросить permission на первом клике**

В `init()`:
```js
document.addEventListener('click', () => this.requestNotificationPermission(), { once: true });
```

- [ ] **Step 6: Проверить синтаксис + регрессия**

```bash
node -c backend/messenger/static/messenger/operator-panel.js
bash scripts/test.sh messenger
```

- [ ] **Step 7: Коммит**

```bash
git add backend/messenger/static/messenger/operator-panel.js
git commit -m "Feat(Messenger): Plan 3 Task 6 — звук, desktop notification, title badge"
```

---

### Task 7: Фронт — favicon badge (canvas)

**Files:**
- Create: `backend/static/js/favicon-badge.js`
- Modify: `backend/templates/ui/messenger_conversations_unified.html`
- Modify: `backend/messenger/static/messenger/operator-panel.js`

- [ ] **Step 1: Создать favicon-badge.js**

Файл: `backend/static/js/favicon-badge.js`
```js
(function () {
    const origFaviconHref = (document.querySelector('link[rel="icon"]') || {}).href;
    const size = 32;
    const cache = {};

    function draw(count) {
        if (cache[count]) return cache[count];
        const img = new Image();
        img.crossOrigin = 'anonymous';
        img.src = origFaviconHref;
        const canvas = document.createElement('canvas');
        canvas.width = size;
        canvas.height = size;
        const ctx = canvas.getContext('2d');
        return new Promise((resolve) => {
            img.onload = () => {
                ctx.drawImage(img, 0, 0, size, size);
                if (count > 0) {
                    ctx.fillStyle = '#ef4444';
                    ctx.beginPath();
                    ctx.arc(size - 8, 8, 8, 0, 2 * Math.PI);
                    ctx.fill();
                    ctx.fillStyle = '#fff';
                    ctx.font = 'bold 12px sans-serif';
                    ctx.textAlign = 'center';
                    ctx.textBaseline = 'middle';
                    ctx.fillText(count > 9 ? '9+' : String(count), size - 8, 9);
                }
                const url = canvas.toDataURL('image/png');
                cache[count] = url;
                resolve(url);
            };
            img.onerror = () => resolve(origFaviconHref);
        });
    }

    window.setFaviconBadge = async function (count) {
        const url = await draw(count);
        let link = document.querySelector('link[rel="icon"]');
        if (!link) {
            link = document.createElement('link');
            link.rel = 'icon';
            document.head.appendChild(link);
        }
        link.href = url;
    };
})();
```

- [ ] **Step 2: Подключить скрипт в шаблон**

В `backend/templates/ui/messenger_conversations_unified.html` в конце `<head>` (или перед `</body>`):
```html
<script src="{% static 'js/favicon-badge.js' %}"></script>
```

- [ ] **Step 3: Вызывать из updateTitleBadge**

В `operator-panel.js` в методе `updateTitleBadge` (из Task 6) добавить:
```js
if (typeof window.setFaviconBadge === 'function') {
    window.setFaviconBadge(unreadCount);
}
```

- [ ] **Step 4: Синтаксис + регрессия**

```bash
node -c backend/messenger/static/messenger/operator-panel.js
node -c backend/static/js/favicon-badge.js
bash scripts/test.sh messenger
```

- [ ] **Step 5: Коммит**

```bash
git add backend/static/js/favicon-badge.js backend/templates/ui/messenger_conversations_unified.html backend/messenger/static/messenger/operator-panel.js
git commit -m "Feat(Messenger): Plan 3 Task 7 — favicon badge"
```

---

### Task 8: Фронт — бейдж waiting_minutes в списке диалогов

**Files:**
- Modify: `backend/messenger/static/messenger/operator-panel.js`
- Modify: `backend/messenger/serializers.py` (добавить `waiting_minutes` если ещё не сериализуется)

- [ ] **Step 1: Проверить что `waiting_minutes` в ConversationSerializer**

Прочитай `backend/messenger/serializers.py` — если поле `waiting_minutes` уже объявлено (Plan 2 Task 3 добавил его в `Conversation` property), убедись что оно попадает в `Meta.fields`. Если нет — добавить:
```python
    waiting_minutes = serializers.IntegerField(read_only=True)
```
и в `Meta.fields`.

- [ ] **Step 2: Добавить бейдж в список диалогов**

Найти метод рендера элемента списка (по Plan 2 Task 12 — рядом со строкой 967–1004 в operator-panel.js, где рендерится `needsHelpBadge`). Добавить:
```js
const waitingMin = conversation.waiting_minutes || 0;
const thresholds = { warn: 3, urgent: 10, rop: 20 };  // клиент-сайд зеркало дефолтов
let waitingBadge = '';
if (waitingMin >= thresholds.rop) {
    waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-red-600 text-white animate-pulse" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
} else if (waitingMin >= thresholds.urgent) {
    waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-orange-500 text-white" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
} else if (waitingMin >= thresholds.warn) {
    waitingBadge = `<span class="inline-flex items-center px-2 py-0.5 text-xs rounded-full bg-yellow-400 text-yellow-900" title="Ждёт ${waitingMin} мин">${waitingMin}м</span>`;
}
```

Вставить `waitingBadge` в HTML рядом с `needsHelpBadge` и `statusBadge`. Убедись что строковая интерполяция использует проверенное числовое значение (`waitingMin` — это число, XSS невозможен).

- [ ] **Step 3: Синтаксис + регрессия**

```bash
node -c backend/messenger/static/messenger/operator-panel.js
bash scripts/test.sh messenger
```

- [ ] **Step 4: Коммит**

```bash
git add backend/messenger/static/messenger/operator-panel.js backend/messenger/serializers.py
git commit -m "Feat(Messenger): Plan 3 Task 8 — бейдж waiting_minutes в списке диалогов"
```

---

### Task 9: Фронт — реакция на эскалационные уведомления

**Files:**
- Modify: `backend/messenger/static/messenger/operator-panel.js`

Контекст: уведомления уже поллятся через существующий `/api/notifications/poll/` (notifications/views.py). Оператор-панель встраивается в ту же страницу, где работает notification-poll. Нужно, чтобы уведомления с `payload.level in (urgent, rop_alert, pool_return)` подсвечивались как «горячие» — toast с переходом в диалог.

- [ ] **Step 1: Найти notification poll handler**

Grep `notifications/poll` в `backend/templates/` и `backend/static/`. Вероятно, есть глобальный скрипт `static/js/notifications.js` или inline в base-шаблоне.

- [ ] **Step 2: Добавить обработку live-chat уведомлений**

Если глобальный notification-centre существует — добавь в его handler: при получении notification с `payload.conversation_id` — вызвать `window.MessengerPanel?.playNotificationSound?.()` и, если страница messenger открыта, `window.MessengerPanel.highlightConversation(payload.conversation_id)`.

Добавить метод в `MessengerOperatorPanel`:
```js
highlightConversation(convId) {
    const el = document.querySelector(`[data-conversation-id="${convId}"]`);
    if (!el) return;
    el.classList.add('ring-2', 'ring-red-500');
    setTimeout(() => el.classList.remove('ring-2', 'ring-red-500'), 3000);
}
```

Если глобального handler нет — пропусти этот шаг, оставь TODO и ограничься тем, что Notification уже видна в notification-колокольчике.

- [ ] **Step 3: Регрессия**

```bash
bash scripts/test.sh messenger
```

- [ ] **Step 4: Коммит**

```bash
git add backend/messenger/static/messenger/operator-panel.js
git commit -m "Feat(Messenger): Plan 3 Task 9 — подсветка эскалированных диалогов"
```

---

### Task 10: Docs + staging deploy

**Files:**
- Modify: `docs/current-sprint.md`
- Modify: `docs/wiki/05-Журнал/Changelog.md`

- [ ] **Step 1: Полный прогон тестов**

Команда: `bash scripts/test.sh messenger accounts policy notifications`
Ожидание: все тесты зелёные.

- [ ] **Step 2: Обновить current-sprint.md**

Добавить раздел "Plan 3 — Notifications + Escalation ✅" с перечислением задач, тестов, миграций.

- [ ] **Step 3: Обновить Changelog.md**

Добавить запись `### Feat: Live-chat Notifications + Escalation (Plan 3)` с детальным списком по шаблону Plan 1/2.

- [ ] **Step 4: Push**

```bash
git add docs/current-sprint.md "docs/wiki/05-Журнал/Changelog.md"
git commit -m "Docs: Plan 3 Notifications + Escalation — current-sprint + Changelog"
git push origin main
```

- [ ] **Step 5: Staging deploy**

Из локальной машины:
```bash
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172 "cd /opt/proficrm-staging && git pull origin main && docker compose -f docker-compose.staging.yml up -d --build web && docker restart crm_staging_celery crm_staging_celery_beat"
```

- [ ] **Step 6: Smoke-test**

```bash
curl -sS -o /dev/null -w 'health=%{http_code}\n' https://crm-staging.groupprofi.ru/health/
```

Ожидание: `health=200`.

Вручную в UI: открыть диалог, завершить через resolve modal → в БД на staging проверить `SELECT id, status, resolution FROM messenger_conversation ORDER BY id DESC LIMIT 1;`

- [ ] **Step 7: Проверить логи celery**

```bash
ssh root@5.181.254.172 "docker logs crm_staging_celery_beat 2>&1 | grep -i escalate | tail -5"
```

Ожидание: строки `Scheduler: Sending due task messenger-escalate-waiting`.

---

## Self-Review

**Spec coverage:**
- ✅ warn_min / urgent_min / rop_alert_min / pool_return_min — Task 3
- ✅ Эскалация assignee → pool — Task 3 (level 4)
- ✅ Notification для assignee / РОПа / branch managers — Task 3
- ✅ Звук нового сообщения — Task 6
- ✅ Desktop Notification API — Task 6
- ✅ Title badge (неактивная вкладка) — Task 6
- ✅ Favicon badge — Task 7
- ✅ Resolution outcome/comment — Tasks 1, 4, 5
- ✅ PolicyConfig.livechat_escalation — Task 2
- ✅ Визуальная эскалация в списке (waiting_minutes бейдж) — Task 8
- ⚠️ Sidebar badge на иконке «Мессенджер» в left nav — **не покрыт**, требует изменения base-шаблона. Оставлен как nice-to-have (выходит за рамки operator-panel).

**Placeholder scan:** чисто. Все шаги содержат конкретный код или точные команды. Task 9 имеет условное ветвление ("если глобальный handler нет — пропусти") — это допустимо потому что результат разведки зависит от состояния кода и не может быть предсказан заранее.

**Type consistency:** `escalation_level` везде int 0..4; `resolution` везде JSONField/dict; `livechat_escalation` — JSONField с 4 фиксированными ключами; Celery task name `messenger.escalate_waiting_conversations` совпадает в декораторе и в BEAT_SCHEDULE.

---

## Execution Handoff

План содержит 10 задач, разбитых по TDD-паттерну. Основная работа — backend (Tasks 1-4), фронт (Tasks 5-9), завершение (Task 10).

Подходит Subagent-Driven Development: каждая задача self-contained, backend-задачи независимы друг от друга кроме миграций (1→2→3→4 sequential), фронт-задачи тоже sequential (5→6→7→8→9), но независимы от backend после Task 4.
