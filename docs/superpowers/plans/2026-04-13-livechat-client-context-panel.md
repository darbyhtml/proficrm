# Live-chat Client Context Panel Implementation Plan (Plan 4)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Правая панель диалога наполняется блоками «Клиент», «Компания» (с автосвязкой по email/phone), «История обращений» клиента, «Быстрые действия», «Аудит» — один агрегированный API-эндпоинт `GET /api/conversations/{id}/context/`.

**Architecture:**
- **Backend:** новый FK `Conversation.company → companies.Company` (nullable). Сервис `autolink_contact_to_company()` запускается при создании `Conversation` (post_save signal) — ищет `Company` по domain(email) и phone контакта, при единственном совпадении ставит FK. Новый API `GET /api/conversations/{id}/context/` возвращает структуру: `{client, company, previous_conversations, audit_log}` — агрегация, одним запросом, чтобы не делать N round-trips с фронта.
- **Frontend:** `renderConversationInfo()` при вызове сначала дергает `/context/`, кэширует ответ в `this._contextCache[convId]`, затем рендерит блоки. Блок «Компания» имеет кнопку «Привязать компанию» (открывает поиск) и «Создать компанию из диалога». Блок «История» показывает список кликабельных элементов (переключается на нужный диалог через `openConversation`).

**Tech Stack:** Django 6, DRF, PostgreSQL, vanilla JS. Reuse существующих `Company`/`Contact` моделей companies app.

---

## File Structure

**Модифицируются:**
- `backend/messenger/models.py` — `Conversation.company` FK
- `backend/messenger/migrations/0023_conversation_company.py`
- `backend/messenger/services.py` (или новый файл `company_autolink.py`) — `autolink_contact_to_company()`
- `backend/messenger/signals.py` — post_save на `Conversation` вызывает autolink
- `backend/messenger/api.py` — новое `@action(detail=True, url_path='context')` в `ConversationViewSet`
- `backend/messenger/serializers.py` — (опц.) добавить `company_id` в `ConversationSerializer`
- `backend/messenger/static/messenger/operator-panel.js` — новый метод `loadConversationContext(id)`, расширенный `renderConversationInfo`
- `docs/current-sprint.md`, `docs/wiki/05-Журнал/Changelog.md`

**Создаются:**
- `backend/messenger/tests/test_company_autolink.py`
- `backend/messenger/tests/test_conversation_context_api.py`

---

## Задачи

### Task 1: `Conversation.company` FK + миграция

**Files:**
- Modify: `backend/messenger/models.py`
- Create: `backend/messenger/migrations/0023_conversation_company.py`
- Test: inline в `test_company_autolink.py` (Step 1 ниже)

- [ ] **Step 1: Падающий тест**

`backend/messenger/tests/test_company_autolink.py`:
```python
from django.test import TestCase
from accounts.models import Branch
from messenger.models import Conversation, Contact, Inbox


class ConversationCompanyFieldTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_ctx", settings={}
        )
        self.contact = Contact.objects.create(
            external_id="ctx_c", name="C", email="c@e.com"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch
        )

    def test_company_defaults_none(self):
        self.assertIsNone(self.conv.company)
```

- [ ] **Step 2:** `bash scripts/test.sh messenger.tests.test_company_autolink` → FAIL (AttributeError `company`).

- [ ] **Step 3: Добавить FK в `Conversation`** (рядом с `branch`):
```python
    company = models.ForeignKey(
        "companies.Company",
        verbose_name="Компания клиента",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="messenger_conversations",
        help_text="Автосвязь по email/phone контакта. Может быть переназначена вручную.",
    )
```

- [ ] **Step 4: Миграция**

`bash scripts/manage.sh makemigrations messenger --name conversation_company` либо через docker/venv. Имя файла `0023_conversation_company.py`.

- [ ] **Step 5: Тест зелёный.** `bash scripts/test.sh messenger.tests.test_company_autolink` → 1/1 OK.

- [ ] **Step 6: Коммит**
```
Feat(Messenger): Plan 4 Task 1 — Conversation.company FK
```

---

### Task 2: Сервис `autolink_contact_to_company` + signal

**Files:**
- Create: `backend/messenger/services/company_autolink.py`
- Modify: `backend/messenger/signals.py` (подключить к post_save Conversation)
- Modify: `backend/messenger/tests/test_company_autolink.py` — добавить класс `AutolinkTests`

- [ ] **Step 1: Падающие тесты**

Добавить в `test_company_autolink.py`:
```python
from companies.models import Company, Contact as CompanyContact


class AutolinkTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_autolink", settings={}
        )
        # Готовая компания с контактом, у которого email на domain gazprom.ru
        self.company = Company.objects.create(name="Газпром")
        CompanyContact.objects.create(
            company=self.company, email="ivan@gazprom.ru", name="Иван"
        )

    def test_autolink_by_email_domain(self):
        contact = Contact.objects.create(
            external_id="x", name="Екатерина", email="kate@gazprom.ru"
        )
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=contact, branch=self.branch
        )
        conv.refresh_from_db()
        self.assertEqual(conv.company, self.company)

    def test_autolink_skips_when_multiple_matches(self):
        other = Company.objects.create(name="Дубликат")
        CompanyContact.objects.create(
            company=other, email="vasya@gazprom.ru", name="Вася"
        )
        contact = Contact.objects.create(
            external_id="x2", name="K", email="kate@gazprom.ru"
        )
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=contact, branch=self.branch
        )
        conv.refresh_from_db()
        self.assertIsNone(conv.company)

    def test_autolink_skips_public_email_domains(self):
        contact = Contact.objects.create(
            external_id="x3", name="K", email="kate@gmail.com"
        )
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=contact, branch=self.branch
        )
        conv.refresh_from_db()
        self.assertIsNone(conv.company)

    def test_autolink_by_phone_when_email_missing(self):
        CompanyContact.objects.create(
            company=self.company, phone="+79991234567", name="М"
        )
        contact = Contact.objects.create(
            external_id="x4", name="K", email="", phone="+79991234567"
        )
        conv = Conversation.objects.create(
            inbox=self.inbox, contact=contact, branch=self.branch
        )
        conv.refresh_from_db()
        self.assertEqual(conv.company, self.company)
```

⚠️ Сначала прочитай `backend/companies/models.py` и уточни:
- Имя модели контакта (возможно `Contact` или `CompanyContact`) — подстрой импорт.
- Поля: `email`, `phone`, `company`-FK — проверить реальные имена.
- Если нормализация phone уже есть (E.164) — использовать то же преобразование.

- [ ] **Step 2:** Запустить — FAIL (`conv.company is None` / ImportError сервиса).

- [ ] **Step 3: Сервис**

Создать `backend/messenger/services/__init__.py` если нет.

`backend/messenger/services/company_autolink.py`:
```python
"""Автосвязка messenger.Contact ↔ companies.Company по email domain / phone."""

from __future__ import annotations

import re

PUBLIC_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "outlook.com", "hotmail.com",
    "mail.ru", "yandex.ru", "yandex.com", "bk.ru", "list.ru",
    "inbox.ru", "icloud.com", "rambler.ru", "protonmail.com",
}

_PHONE_DIGITS = re.compile(r"\D+")


def _normalize_phone(phone: str) -> str:
    return _PHONE_DIGITS.sub("", phone or "")


def find_company_for_contact(contact) -> "Company | None":
    """Вернуть Company если найдено единственное совпадение по domain или phone."""
    from companies.models import Company

    candidates = set()

    # 1. Email domain (если не публичный)
    email = (contact.email or "").strip().lower()
    if email and "@" in email:
        domain = email.split("@", 1)[1]
        if domain and domain not in PUBLIC_EMAIL_DOMAINS:
            matches = Company.objects.filter(
                contacts__email__iendswith=f"@{domain}"
            ).distinct()
            candidates.update(matches[:5].values_list("id", flat=True))

    # 2. Phone (нормализованный)
    phone_norm = _normalize_phone(contact.phone)
    if phone_norm and len(phone_norm) >= 10:
        # Берём последние 10 цифр (без кода страны) для устойчивости
        tail = phone_norm[-10:]
        from django.db.models import Q
        matches = Company.objects.filter(
            Q(contacts__phone__icontains=tail)
        ).distinct()
        candidates.update(matches[:5].values_list("id", flat=True))

    if len(candidates) != 1:
        return None
    return Company.objects.filter(id=candidates.pop()).first()


def autolink_conversation_company(conversation):
    """Привязать company к conversation, если контакт однозначно определяется."""
    if conversation.company_id or not conversation.contact_id:
        return
    company = find_company_for_contact(conversation.contact)
    if company:
        # Обход инварианта save() — через update
        conversation.__class__.objects.filter(pk=conversation.pk).update(company=company)
```

⚠️ Реальное имя related_name от `companies.Company.contacts` может быть другим — проверь. Также уточни, есть ли у `Contact` поле `phone` (или `phone_e164`, или список телефонов через reverse-relation).

- [ ] **Step 4: Signal**

В `backend/messenger/signals.py` (если файла нет — создать и зарегистрировать в `apps.py`):
```python
from django.db.models.signals import post_save
from django.dispatch import receiver

from messenger.models import Conversation
from messenger.services.company_autolink import autolink_conversation_company


@receiver(post_save, sender=Conversation)
def _autolink_on_create(sender, instance, created, **kwargs):
    if not created:
        return
    if instance.company_id:
        return
    autolink_conversation_company(instance)
```

Если signals.py уже есть и подключён в `AppConfig.ready()` — просто добавь receiver. Если нет — создай файл и зарегистрируй в `messenger/apps.py`:
```python
def ready(self):
    from . import signals  # noqa: F401
```

- [ ] **Step 5: Тесты зелёные.** `bash scripts/test.sh messenger.tests.test_company_autolink` → 5/5 OK.

- [ ] **Step 6: Регрессия.** `bash scripts/test.sh messenger` — all OK (signal не должен ломать существующие тесты; если ломает — отладить).

- [ ] **Step 7: Коммит**
```
Feat(Messenger): Plan 4 Task 2 — autolink contact→company по email/phone
```

---

### Task 3: API `GET /api/conversations/{id}/context/`

**Files:**
- Modify: `backend/messenger/api.py` — новый `@action` на `ConversationViewSet`
- Create: `backend/messenger/tests/test_conversation_context_api.py`

- [ ] **Step 1: Падающие тесты**

```python
from datetime import timedelta
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from accounts.models import User, Branch
from companies.models import Company
from messenger.models import Conversation, Contact, Inbox, ConversationTransfer


@override_settings(MESSENGER_ENABLED=True)
class ConversationContextApiTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(name="ЕКБ", code="ekb")
        self.user = User.objects.create_user(
            username="ctxu", password="x", role="manager", branch=self.branch
        )
        self.inbox = Inbox.objects.create(
            name="S", branch=self.branch, widget_token="tok_ctxapi", settings={}
        )
        self.company = Company.objects.create(name="Тест-Ко")
        self.contact = Contact.objects.create(
            external_id="ctxapi_c", name="Клиент", email="k@test-ko.example", phone="+79990000000"
        )
        self.conv = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch,
            assignee=self.user, company=self.company,
        )
        # Предыдущий диалог того же контакта
        self.prev = Conversation.objects.create(
            inbox=self.inbox, contact=self.contact, branch=self.branch,
            status=Conversation.Status.RESOLVED,
        )
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def test_context_endpoint_returns_client_block(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertIn("client", resp.data)
        self.assertEqual(resp.data["client"]["name"], "Клиент")
        self.assertEqual(resp.data["client"]["email"], "k@test-ko.example")

    def test_context_endpoint_returns_company_block(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIn("company", resp.data)
        self.assertEqual(resp.data["company"]["id"], self.company.id)
        self.assertEqual(resp.data["company"]["name"], "Тест-Ко")

    def test_context_endpoint_returns_previous_conversations(self):
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        previous = resp.data["previous_conversations"]
        self.assertIsInstance(previous, list)
        prev_ids = [p["id"] for p in previous]
        self.assertIn(self.prev.id, prev_ids)
        self.assertNotIn(self.conv.id, prev_ids)  # текущий исключён

    def test_context_endpoint_returns_audit_log(self):
        ConversationTransfer.objects.create(
            conversation=self.conv, from_user=self.user, to_user=self.user,
            reason="тест причина", from_branch=self.branch, to_branch=self.branch,
        )
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIn("audit_log", resp.data)
        self.assertGreaterEqual(len(resp.data["audit_log"]), 1)
        self.assertIn("тест", resp.data["audit_log"][0].get("text", "").lower())

    def test_context_endpoint_no_company_returns_null(self):
        self.conv.company = None
        self.conv.save(update_fields=["company"])
        resp = self.api.get(f"/api/conversations/{self.conv.id}/context/")
        self.assertIsNone(resp.data["company"])
```

⚠️ Сигнатура `ConversationTransfer.objects.create(...)` может отличаться — проверь модель в `backend/messenger/models.py` и подстрой поля. Если модель требует `from_branch/to_branch/meta` — подстрой.

- [ ] **Step 2:** `bash scripts/test.sh messenger.tests.test_conversation_context_api` → FAIL (404 или ключи отсутствуют).

- [ ] **Step 3: Реализовать action**

В `backend/messenger/api.py` в `ConversationViewSet`:
```python
    @action(detail=True, methods=["get"], url_path="context")
    def context(self, request, pk=None):
        """Агрегированные данные правой панели: клиент, компания, история, аудит."""
        conv = self.get_object()
        contact = conv.contact

        # Блок client
        client_block = {
            "id": str(contact.id),
            "name": contact.name,
            "email": contact.email,
            "phone": contact.phone,
            "region": conv.client_region or "",
            "region_source": conv.client_region_source or "",
            "last_activity_at": contact.last_activity_at,
            "blocked": contact.blocked,
        }

        # Блок company
        company_block = None
        if conv.company_id:
            company = conv.company
            company_block = {
                "id": company.id,
                "name": company.name,
                # TODO дополнительные поля — подставить существующие атрибуты Company
                "status": getattr(company, "status", ""),
                "branch_id": getattr(company, "branch_id", None),
                "responsible_id": getattr(company, "responsible_id", None),
                "deals_count": company.deals.count() if hasattr(company, "deals") else 0,
                "url": f"/companies/{company.id}/",
            }

        # Предыдущие диалоги (исключая текущий)
        previous_qs = (
            Conversation.objects
            .filter(contact=contact)
            .exclude(pk=conv.pk)
            .order_by("-created_at")[:20]
        )
        previous_list = []
        for p in previous_qs:
            previous_list.append({
                "id": p.id,
                "status": p.status,
                "ui_status": p.ui_status,
                "created_at": p.created_at,
                "resolution": p.resolution,
            })

        # Аудит: ConversationTransfer + resolution
        audit_log = []
        for t in conv.transfers.select_related("from_user", "to_user").order_by("-created_at")[:20]:
            audit_log.append({
                "kind": "transfer",
                "created_at": t.created_at,
                "from_user": t.from_user.get_full_name() if t.from_user else None,
                "to_user": t.to_user.get_full_name() if t.to_user else None,
                "text": t.reason,
            })
        if conv.resolution and conv.resolution.get("outcome"):
            audit_log.insert(0, {
                "kind": "resolution",
                "created_at": conv.resolution.get("resolved_at"),
                "text": conv.resolution.get("comment", ""),
                "outcome": conv.resolution.get("outcome"),
            })

        return Response({
            "client": client_block,
            "company": company_block,
            "previous_conversations": previous_list,
            "audit_log": audit_log,
        })
```

⚠️ Реальное related_name `conv.transfers` (ConversationTransfer.conversation) может быть другим — проверь в модели. Также проверь какие поля у `Company` реально есть (responsible, branch, status, deals).

- [ ] **Step 4:** Тесты зелёные. Если какие-то assertions падают из-за других имён полей — исправь согласованно тест и код.

- [ ] **Step 5: Регрессия.** `bash scripts/test.sh messenger` — all OK.

- [ ] **Step 6: Коммит**
```
Feat(Messenger): Plan 4 Task 3 — GET /api/conversations/{id}/context/
```

---

### Task 4: Фронт — правая панель читает `/context/` и рендерит блоки

**Files:**
- Modify: `backend/messenger/static/messenger/operator-panel.js`

**Контекст:** текущий `renderConversationInfo()` (строка ~1745) рендерит блоки «Контакт», «Детали», «Действия». Нужно расширить: добавить подгрузку `/context/`, показать блоки «Компания», «История обращений», «Аудит диалога». Сохранять существующие блоки (не ломать UI).

- [ ] **Step 1: Новый метод `loadConversationContext`**

В классе добавить:
```js
async loadConversationContext(convId) {
    if (!convId) return null;
    if (this._contextCache && this._contextCache[convId]) {
        return this._contextCache[convId];
    }
    try {
        const resp = await fetch(`/api/conversations/${convId}/context/`, {
            credentials: 'same-origin',
            headers: { 'Accept': 'application/json' },
        });
        if (!resp.ok) return null;
        const data = await resp.json();
        this._contextCache = this._contextCache || {};
        this._contextCache[convId] = data;
        return data;
    } catch (e) {
        return null;
    }
}

clearConversationContextCache(convId) {
    if (!this._contextCache) return;
    if (convId) { delete this._contextCache[convId]; }
    else { this._contextCache = {}; }
}
```

- [ ] **Step 2: Расширить `renderConversationInfo`**

После существующего рендера HTML добавить placeholder-блоки (чтобы сразу были видны, даже до загрузки):
```js
// Плейсхолдеры для Plan 4
html += `
  <div id="panelCompanyBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
  <div id="panelHistoryBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
  <div id="panelAuditBlock" class="bg-white rounded-lg border border-brand-soft/60 p-3" hidden></div>
`;
```

(Вставить перед закрытием `</div>` главного `space-y-3` контейнера.)

После `infoArea.innerHTML = html` добавить асинхронную загрузку:
```js
this.loadConversationContext(conversation.id).then(ctx => {
    if (!ctx) return;
    // Проверяем, что пользователь ещё на том же диалоге
    if (this.currentConversation?.id !== conversation.id) return;
    this._renderCompanyBlock(ctx.company);
    this._renderHistoryBlock(ctx.previous_conversations || []);
    this._renderAuditBlock(ctx.audit_log || []);
});
```

- [ ] **Step 3: Методы рендера блоков**

```js
_renderCompanyBlock(company) {
    const el = document.getElementById('panelCompanyBlock');
    if (!el) return;
    if (!company) {
        el.hidden = false;
        el.innerHTML = `
          <h3 class="text-sm font-semibold mb-2">Компания</h3>
          <p class="text-xs text-brand-dark/60 mb-2">Не привязана</p>
          <button type="button" class="btn btn-outline btn-sm text-xs w-full" disabled>➕ Создать компанию (в разработке)</button>
        `;
        return;
    }
    el.hidden = false;
    el.innerHTML = `
      <h3 class="text-sm font-semibold mb-2">Компания</h3>
      <div class="text-sm font-medium">${this.escapeHtml(company.name || '')}</div>
      ${company.deals_count != null ? `<div class="text-xs text-brand-dark/60 mt-1">Сделок: ${company.deals_count}</div>` : ''}
      <a href="${this.escapeHtml(company.url || '#')}" target="_blank" rel="noopener" class="inline-block mt-2 text-xs text-brand-teal hover:underline">Открыть в CRM →</a>
    `;
}

_renderHistoryBlock(previous) {
    const el = document.getElementById('panelHistoryBlock');
    if (!el) return;
    if (!previous.length) {
        el.hidden = true;
        return;
    }
    el.hidden = false;
    const items = previous.slice(0, 10).map(p => {
        const date = p.created_at ? new Date(p.created_at).toLocaleDateString('ru-RU') : '';
        const status = this.escapeHtml(p.ui_status || p.status || '');
        return `<li><button type="button" class="text-left w-full px-2 py-1 hover:bg-brand-soft/30 rounded text-xs" data-history-conv-id="${p.id}">
            <span class="text-brand-dark/60">${date}</span>
            <span class="ml-2 inline-block px-1.5 rounded bg-brand-soft/50">${status}</span>
        </button></li>`;
    }).join('');
    el.innerHTML = `
      <h3 class="text-sm font-semibold mb-2">История обращений (${previous.length})</h3>
      <ul class="space-y-1">${items}</ul>
    `;
    el.querySelectorAll('[data-history-conv-id]').forEach(btn => {
        btn.addEventListener('click', () => {
            const id = btn.getAttribute('data-history-conv-id');
            if (id) this.openConversation(id);
        });
    });
}

_renderAuditBlock(audit) {
    const el = document.getElementById('panelAuditBlock');
    if (!el) return;
    if (!audit.length) {
        el.hidden = true;
        return;
    }
    el.hidden = false;
    const items = audit.slice(0, 5).map(a => {
        const date = a.created_at ? new Date(a.created_at).toLocaleString('ru-RU') : '';
        const kind = a.kind === 'transfer' ? 'Передача' : (a.kind === 'resolution' ? 'Резолюция' : '—');
        const text = this.escapeHtml(a.text || '');
        return `<li class="text-xs text-brand-dark/70">
            <div class="font-medium">${kind} <span class="text-brand-dark/50">· ${date}</span></div>
            ${text ? `<div class="mt-0.5">${text}</div>` : ''}
        </li>`;
    }).join('');
    el.innerHTML = `
      <details class="group">
        <summary class="text-sm font-semibold cursor-pointer select-none">Аудит диалога (${audit.length})</summary>
        <ul class="space-y-2 mt-2">${items}</ul>
      </details>
    `;
}
```

- [ ] **Step 4: Инвалидация кэша**

При закрытии диалога/переключении:
- В `openConversation(id)` перед загрузкой нового диалога добавить сброс: `this.clearConversationContextCache();` (чтобы на следующем открытии данные были свежие).
- После успешного `PATCH /transfer/` (Plan 2 Task 8) — добавить `this.clearConversationContextCache(convId)` чтобы аудит обновился.

Найди метод, который обрабатывает успешную передачу диалога (`submitTransferModal` или подобный), и добавь очистку кэша.

- [ ] **Step 5: Проверить синтаксис**

```bash
node -c backend/messenger/static/messenger/operator-panel.js
```

- [ ] **Step 6: Регрессия**

```bash
bash scripts/test.sh messenger
```

- [ ] **Step 7: Коммит**
```
Feat(Messenger): Plan 4 Task 4 — правая панель использует /context/ API
```

---

### Task 5: Docs + staging deploy

**Files:**
- Modify: `docs/current-sprint.md`
- Modify: `docs/wiki/05-Журнал/Changelog.md`

- [ ] **Step 1: Полная регрессия**
```bash
bash scripts/test.sh messenger accounts policy notifications companies
```

- [ ] **Step 2: `current-sprint.md`** — добавить раздел Plan 4 ✅ с перечислением задач, тестов, миграции `0023`.

- [ ] **Step 3: `Changelog.md`** — раздел `### Feat: Live-chat Client Context Panel (Plan 4)` по шаблону Plan 1/2/3.

- [ ] **Step 4: Push**
```bash
git add docs/current-sprint.md "docs/wiki/05-Журнал/Changelog.md"
git commit -m "Docs: Plan 4 Client Context Panel"
git push origin main
```

- [ ] **Step 5: Staging deploy**
```bash
ssh -i ~/.ssh/id_proficrm_deploy root@5.181.254.172 "cd /opt/proficrm-staging && git pull origin main && docker compose -f docker-compose.staging.yml up -d --build web"
```

Миграция `messenger.0023_conversation_company` применяется автоматически в entrypoint.

- [ ] **Step 6: Smoke-test**
```bash
curl -sS -o /dev/null -w 'health=%{http_code}\n' https://crm-staging.groupprofi.ru/health/
```

Вручную: открыть любой диалог в operator-panel → убедиться что блоки «Компания»/«История»/«Аудит» появляются (скрыты если данных нет).

---

## Self-Review

**Spec coverage (раздел 8):**
- ✅ Блок «Клиент» — уже рендерится существующим `renderConversationInfo`, расширения не требуется (имя/email/phone/регион уже доступны)
- ✅ Блок «Компания» — Task 1-4 (FK + autolink + API + рендер)
- ✅ Автосвязка по email domain/phone — Task 2
- ✅ Блок «История обращений» — Task 3 + Task 4
- ✅ Блок «Быстрые действия» — частично (ссылка «Открыть в CRM»); создание task/deal/email отложено как nice-to-have (требует prefill-ссылок в companies/tasks/mailer)
- ✅ Блок «Метки» — в существующем `renderConversationInfo` уже есть logic для labels (Plan 2), не трогаем
- ✅ Блок «Аудит диалога» (свёрнут) — Task 3 + Task 4 через `ConversationTransfer` + `resolution`
- ⚠️ Responsive drawer < 1280px — существующий layout уже имеет CSS для mobile (`.lg:hidden`); новые блоки наследуют это поведение автоматически, отдельной задачи не нужно

**Placeholder scan:** в Task 2 и Task 3 есть уточнения «проверь реальные имена полей» — это плановая разведка, а не плейсхолдер, потому что субагент должен свериться с моделями `Company`/`ConversationTransfer` перед реализацией (жестко зашить — риск фейла).

**Type consistency:** `conv.company_id` везде int|None; `audit_log[].kind` ∈ {transfer, resolution}; `previous_conversations[].id` совпадает с `Conversation.pk` (int).

---

## Execution Handoff

5 задач, TDD. Backend Tasks 1-3 sequential, Task 4 фронт зависит от Task 3, Task 5 — завершение.
