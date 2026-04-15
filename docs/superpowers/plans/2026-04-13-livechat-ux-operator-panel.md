# Live-chat UX: Операторская панель — План 2 (v2, адаптирован под реальный код)

> **For agentic workers:** REQUIRED SUB-SKILL: `superpowers:subagent-driven-development`.

**Goal:** Упростить UX оператор-панели мессенджера: UI-статусы поверх DB-значений, крупный контекстный CTA в шапке диалога, защита от ошибок (модалка подтверждения + undo), модалка передачи, черновик в localStorage, улучшенный UI приватных заметок, видимые quick-reply кнопки, эскалация «Позвать руководителя».

**Architecture:** Backend — новые поля (`last_customer_msg_at`, `last_agent_msg_at`, `CannedResponse.is_quick_button/sort_order`), property `ui_status`/`waiting_minutes`, template tag бейджа. Frontend — расширение существующего `MessengerOperatorPanel` в `operator-panel.js` (3390 строк, contenteditable `#messageBody`), шаблон `messenger_conversations_unified.html` (3-колоночный Chatwoot-layout). UI — текущий стиль CRM, без dark mode.

**Tech Stack:** Django 6.0.1, Python 3.13, Tailwind, vanilla JS (класс `MessengerOperatorPanel`), DRF.

**Связанная спека:** `docs/superpowers/specs/2026-04-13-livechat-ux-completion-design.md` §5, §6.

---

## Ключевые факты о существующем коде (важно для всех задач)

1. **Шаблон:** `backend/templates/ui/messenger_conversations_unified.html` (3 колонки: список / диалог / инфо)
2. **JS:** `backend/messenger/static/messenger/operator-panel.js` — класс `MessengerOperatorPanel`, глобально `window.MessengerPanel`
3. **Поле ввода:** `<div id="messageBody" contenteditable="true">` (НЕ textarea)
4. **Существующие действия:** в ПРАВОЙ колонке — `assignMeBtn`, `closeConvBtn`, `convStatusSelect` (open/pending/resolved/closed), `convAssigneeSelect`, `convPrioritySelect`
5. **Отправка:** `sendMessage(event)` → `POST /api/conversations/{id}/messages/` с полями `body`, `direction` (`out` или `internal`), `attachments`. Режим управляется `this.composeMode`
6. **Resolve/reopen:** через `patchConversation(id, {status: 'resolved'})` → `PATCH /api/conversations/{id}/`. Отдельных endpoint'ов нет и НЕ нужны
7. **Агенты:** `GET /api/conversations/agents/` — есть, возвращает `{id, username, name}` фильтр `role='manager', is_active=True`
8. **Шаблоны:** `GET /api/canned-responses/` — есть, slash-команда `/` в поле уже работает через `loadCannedResponses()`/`showCannedDropdown()`
9. **SSE:** `GET /api/conversations/{id}/stream/`, события `ready`, `message.created`, `conversation.updated`. Есть fallback polling
10. **Transfer:** endpoint `POST /api/messenger/conversations/{id}/transfer/` (из Plan 1) — **UI отсутствует**
11. **is_private vs INTERNAL:** `Message.Direction.INTERNAL` уже существует и исключается из widget API. `Message.is_private` (Plan 1 Task 5) — дублирующее поле, в UI **НЕ используем**; для приватных заметок берём существующий `direction=INTERNAL` (композ-режим уже есть в JS)
12. **DB статусы:** `OPEN / PENDING / RESOLVED / CLOSED`
13. **Все коммиты/docstrings на русском** (CLAUDE.md)

---

## UI-статусы (маппинг поверх DB)

| UI статус | DB условие | Tailwind |
|---|---|---|
| 🔴 Новый | `status=OPEN AND assignee IS NULL` | `bg-red-500 text-white` |
| 🟡 Ждёт ответа | `status IN (OPEN,PENDING) AND assignee NOT NULL AND last_customer_msg_at > last_agent_msg_at` | `bg-amber-400 text-gray-900` |
| 🔵 В работе | `status IN (OPEN,PENDING) AND assignee NOT NULL AND (last_agent_msg_at >= last_customer_msg_at OR last_customer_msg_at IS NULL)` | `bg-blue-500 text-white` |
| ⚪ Завершён | `status IN (RESOLVED, CLOSED)` | `bg-gray-400 text-white` |

---

## Task 1: Поля `last_customer_msg_at` / `last_agent_msg_at`

**Files:**
- Modify: `backend/messenger/models.py` (Conversation + Message.save())
- Create: `backend/messenger/migrations/0020_conversation_msg_timestamps.py`
- Create: `backend/messenger/tests/test_ui_status.py`

- [ ] **Step 1: Падающий тест**

```python
# backend/messenger/tests/test_ui_status.py
from django.test import TestCase
from django.db.models.signals import post_save
from django.utils import timezone
from accounts.models import Branch
from messenger.models import Inbox, Contact, Conversation, Message
from messenger.signals import auto_assign_new_conversation


class MessageTimestampsTests(TestCase):
    def setUp(self):
        post_save.disconnect(auto_assign_new_conversation, sender=Conversation)
        self.addCleanup(post_save.connect, auto_assign_new_conversation, sender=Conversation)
        self.branch = Branch.objects.create(name="Br", code="br")
        self.inbox = Inbox.objects.create(name="Widget", branch=self.branch)
        self.contact = Contact.objects.create(name="C")
        self.conv = Conversation.objects.create(inbox=self.inbox, contact=self.contact)

    def test_incoming_sets_customer_ts(self):
        Message.objects.create(conversation=self.conv, direction=Message.Direction.IN, body="Привет")
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.conv.last_customer_msg_at)
        self.assertIsNone(self.conv.last_agent_msg_at)

    def test_outgoing_sets_agent_ts(self):
        Message.objects.create(conversation=self.conv, direction=Message.Direction.OUT, body="Здравствуйте")
        self.conv.refresh_from_db()
        self.assertIsNotNone(self.conv.last_agent_msg_at)
        self.assertIsNone(self.conv.last_customer_msg_at)

    def test_internal_does_not_touch_customer_or_agent_ts(self):
        """Служебная заметка не должна переключать ui_status."""
        Message.objects.create(conversation=self.conv, direction=Message.Direction.INTERNAL, body="Служебка")
        self.conv.refresh_from_db()
        self.assertIsNone(self.conv.last_customer_msg_at)
        self.assertIsNone(self.conv.last_agent_msg_at)
```

- [ ] **Step 2: Прогон — FAIL**

```bash
bash scripts/test.sh messenger.tests.test_ui_status.MessageTimestampsTests
```

- [ ] **Step 3: Добавить поля**

В `backend/messenger/models.py` в класс `Conversation`:

```python
    last_customer_msg_at = models.DateTimeField(
        "Последнее сообщение клиента",
        null=True, blank=True, db_index=True,
    )
    last_agent_msg_at = models.DateTimeField(
        "Последнее сообщение оператора",
        null=True, blank=True, db_index=True,
    )
```

- [ ] **Step 4: Обновить Message.save()**

Найти блок `Conversation.objects.filter(pk=self.conversation_id).update(last_activity_at=...)` (около строки 656). Расширить:

```python
update_kwargs = {"last_activity_at": created_at_used}
if self.direction == Message.Direction.IN:
    update_kwargs["last_customer_msg_at"] = created_at_used
elif self.direction == Message.Direction.OUT:
    update_kwargs["last_agent_msg_at"] = created_at_used
# INTERNAL не меняет ни одну из меток — служебная заметка не переключает ui_status
Conversation.objects.filter(pk=self.conversation_id).update(**update_kwargs)
```

- [ ] **Step 5: Миграция**

```bash
cd backend && python manage.py makemigrations messenger -n conversation_msg_timestamps
```

- [ ] **Step 6: PASS + commit**

```bash
bash scripts/test.sh messenger.tests.test_ui_status
git add backend/messenger/models.py backend/messenger/migrations/0020_conversation_msg_timestamps.py backend/messenger/tests/test_ui_status.py
git commit -m "Feat(Messenger): поля last_customer_msg_at и last_agent_msg_at

Нужны для property ui_status: 'Ждёт ответа' vs 'В работе'.
Message.save() обновляет метки для IN/OUT. INTERNAL-сообщения
(служебные заметки) метки не трогают."
```

---

## Task 2: Property `Conversation.ui_status` + template tag

**Files:**
- Modify: `backend/messenger/models.py` (UiStatus + property)
- Modify/Create: `backend/messenger/templatetags/messenger_tags.py` + `__init__.py`
- Create: `backend/templates/messenger/_ui_status_badge.html`
- Modify: `backend/messenger/tests/test_ui_status.py`

- [ ] **Step 1: Тесты (5 штук)** — см. полный список сочетаний в v1 плана, адаптировать под реальный User model (`get_user_model()`) и `Conversation.Status.OPEN/RESOLVED/CLOSED`.

- [ ] **Step 2: Реализация property**

```python
    class UiStatus(models.TextChoices):
        NEW = "new", "Новый"
        WAITING = "waiting", "Ждёт ответа"
        IN_PROGRESS = "in_progress", "В работе"
        CLOSED = "closed", "Завершён"

    @property
    def ui_status(self) -> str:
        if self.status in (self.Status.RESOLVED, self.Status.CLOSED):
            return self.UiStatus.CLOSED
        if self.assignee_id is None:
            return self.UiStatus.NEW
        agent = self.last_agent_msg_at
        customer = self.last_customer_msg_at
        if customer and (not agent or customer > agent):
            return self.UiStatus.WAITING
        return self.UiStatus.IN_PROGRESS
```

- [ ] **Step 3: Template tag + partial** — см. v1 плана (без изменений).

- [ ] **Step 4: Включить в сериалайзер**

В `backend/messenger/serializers.py` `ConversationSerializer` добавить `ui_status = serializers.CharField(read_only=True)` и в `Meta.fields`. Это нужно фронту для JS.

- [ ] **Step 5: PASS + commit**

```bash
bash scripts/test.sh messenger.tests.test_ui_status
git add backend/messenger/models.py backend/messenger/templatetags backend/templates/messenger/_ui_status_badge.html backend/messenger/serializers.py backend/messenger/tests/test_ui_status.py
git commit -m "Feat(Messenger): property ui_status + template tag + сериалайзер

UiStatus = NEW/WAITING/IN_PROGRESS/CLOSED. ui_status в ConversationSerializer
для использования в operator-panel.js."
```

---

## Task 3: `waiting_minutes` + пороги эскалации

**Files:** `backend/messenger/models.py`, `backend/policy/models.py`, `backend/messenger/tests/test_ui_status.py`

Реализация аналогична v1 плана — property `waiting_minutes` (int минут в состоянии WAITING, иначе 0) + classmethod `escalation_thresholds()` с дефолтами `{warn_min:3, urgent_min:10, rop_alert_min:20, pool_return_min:40}` и попыткой загрузить `PolicyConfig.livechat_escalation`.

Добавить `waiting_minutes` в сериалайзер Conversation.

- [ ] Тесты: 2 штуки (zero when not waiting / positive when customer last)
- [ ] Реализация
- [ ] Commit:
  ```
  Feat(Messenger): property waiting_minutes + пороги эскалации

  Минуты ожидания в WAITING для индикатора «молчит N минут».
  Пороги warn/urgent/rop_alert/pool_return из PolicyConfig или дефолтов.
  ```

---

## Task 4: `CannedResponse.is_quick_button` + `sort_order`

**Files:** `backend/messenger/models.py`, `backend/messenger/migrations/0021_cannedresponse_quick_button.py`, `backend/messenger/serializers.py`, `backend/messenger/tests/test_canned_responses.py`

Без изменений относительно v1:
- Поля `is_quick_button` (BooleanField, db_index) + `sort_order` (PositiveIntegerField default=0)
- Meta.ordering = `["sort_order", "title"]`
- Тесты: defaults + ordering (2 теста)
- **Важно:** добавить `is_quick_button` + `sort_order` в существующий `CannedResponseSerializer` (`MessageSerializer` рядом в `serializers.py`). Найти этот сериалайзер и расширить fields.
- Commit: `Feat(Messenger): CannedResponse.is_quick_button + sort_order`

---

## Task 5: Новые endpoint'ы — `/needs-help/`, расширение `/agents/`, `/branches/`

**Files:**
- Modify: `backend/messenger/api.py` (ConversationViewSet: action `needs_help`, расширить action `agents`)
- Create: view `branches_list` для списка филиалов
- Modify: `backend/messenger/urls.py` (только `/branches/`; `/needs-help/` — через `@action(detail=True)`)
- Modify: `backend/messenger/tests/test_operator_actions_api.py`

### Что НЕ делаем (изменение относительно v1)
- ❌ **НЕ создаём** `/resolve/` и `/reopen/` endpoint'ы — используем существующий `PATCH /api/conversations/{id}/` с `{status:'resolved'|'open'}`, как это уже работает в `patchConversation()` в JS
- ❌ **НЕ создаём** `/managers/` — используем существующий `agents` action, только расширяем query-параметрами

### Что делаем

**5.1. `GET /api/conversations/agents/?branch_id=&online=1`**

Расширить существующий action `agents` в `ConversationViewSet`:
```python
@action(detail=False, methods=["get"])
def agents(self, request):
    qs = User.objects.filter(role=User.Role.MANAGER, is_active=True)
    branch_id = request.query_params.get("branch_id")
    if branch_id:
        qs = qs.filter(branch_id=branch_id)
    if request.query_params.get("online") == "1":
        qs = qs.filter(messenger_online=True)
    return Response([
        {"id": u.id, "username": u.username,
         "name": u.get_full_name() or u.username,
         "branch_id": u.branch_id,
         "online": u.messenger_online}
        for u in qs
    ])
```

**5.2. `GET /api/messenger/branches/` — список филиалов**

Новый view. Права `IsAuthenticated`. Возвращает `[{id, name, code}]`.

**5.3. `POST /api/conversations/{id}/needs-help/`**

Новый action в `ConversationViewSet`:
```python
@action(detail=True, methods=["post"], url_path="needs-help")
def needs_help(self, request, pk=None):
    from django.utils import timezone
    conv = self.get_object()  # фильтруется через get_queryset (role visibility)
    Conversation.objects.filter(pk=conv.pk).update(
        needs_help=True,
        needs_help_at=timezone.now(),
    )
    return Response({"ok": True})
```

### Тесты (`test_operator_actions_api.py`)

1. `test_needs_help_sets_flag` — POST → 200, `needs_help=True`, `needs_help_at NOT NULL`
2. `test_needs_help_forbidden_for_foreign_branch` — manager_ekb → POST на conv_tmn → 404 или 403
3. `test_agents_filter_by_branch` — 2 филиала → `?branch_id=X` возвращает только своих
4. `test_agents_filter_by_online` — `?online=1` исключает offline
5. `test_branches_list` — возвращает все филиалы

### Commit
```
Feat(Messenger): API для оператор-панели — needs-help, agents фильтры, branches

POST /api/conversations/{id}/needs-help/ — флаг эскалации.
GET /api/conversations/agents/?branch_id=&online=1 — фильтры.
GET /api/messenger/branches/ — список филиалов для модалки передачи.

Resolve/reopen остаются через существующий PATCH /api/conversations/{id}/.
```

---

## Task 6: Frontend — контекстный главный CTA в шапке диалога

**Files:**
- Modify: `backend/templates/ui/messenger_conversations_unified.html` — шапка центральной колонки (найти `mobileBackBtn`/`mobileInfoBtn` блок ~строка 1027 и добавить primary CTA + ⋯ меню)
- Modify: `backend/messenger/static/messenger/operator-panel.js` — методы `renderPrimaryCTA()`, `renderSecondaryMenu()`, вызов из существующего метода рендера диалога

### Стратегия: минимум разрушений

- Правую колонку (`assignMeBtn`, `closeConvBtn`, `convStatusSelect` и т.д.) **оставляем** — это для продвинутых действий (РОП, директор)
- В шапке ЦЕНТРАЛЬНОЙ колонки (рядом с badge) добавляем **новый** блок: крупная primary CTA + dropdown ⋯
- CTA дублирует часть действий правой колонки (взять себе, завершить) — но визуально выделен для типового операторского флоу

### Разметка (добавить в шаблон после badge в шапке центра)

```html
<div class="flex items-center gap-2 ml-auto" id="conversationPrimaryActions">
  <button id="primaryCtaBtn" class="h-12 px-6 text-base font-semibold rounded-lg transition-colors text-white">
    <span id="primaryCtaLabel">—</span>
  </button>
  <div class="relative">
    <button id="secondaryMenuToggle" class="h-12 w-12 rounded-lg hover:bg-gray-100 text-xl" aria-label="Больше действий">⋯</button>
    <div id="secondaryMenu" class="hidden absolute right-0 mt-1 w-56 bg-white border rounded-lg shadow-lg z-50">
      <button data-action="transfer" class="w-full text-left px-4 py-2 hover:bg-gray-50">Передать</button>
      <button data-action="resolve" class="w-full text-left px-4 py-2 hover:bg-gray-50">Завершить</button>
      <button data-action="needs-help" class="w-full text-left px-4 py-2 hover:bg-gray-50 text-amber-700">🆘 Позвать руководителя</button>
    </div>
  </div>
</div>
```

### JS методы (внутри класса `MessengerOperatorPanel`)

```js
renderPrimaryCTA(conv) {
  const btn = document.getElementById('primaryCtaBtn');
  const label = document.getElementById('primaryCtaLabel');
  if (!btn || !conv) return;

  btn.className = 'h-12 px-6 text-base font-semibold rounded-lg transition-colors text-white';
  const meId = window.currentUserId;  // уже есть в контексте
  const isMine = conv.assignee && conv.assignee.id === meId;
  const status = conv.ui_status || this.computeUiStatusFallback(conv);

  if (status === 'new') {
    label.textContent = '➤ Взять себе';
    btn.classList.add('bg-green-600', 'hover:bg-green-700');
    btn.onclick = () => this.assignSelf(conv.id);
  } else if (status === 'closed') {
    label.textContent = '↻ Переоткрыть';
    btn.classList.add('bg-gray-500', 'hover:bg-gray-600');
    btn.onclick = () => this.patchConversation(conv.id, {status: 'open'});
  } else if (!isMine) {
    label.textContent = '⬇ Забрать себе';
    btn.classList.add('bg-orange-500', 'hover:bg-orange-600');
    btn.onclick = () => {
      if (confirm('Забрать диалог? Текущий исполнитель потеряет доступ к нему как к своему.')) {
        this.assignSelf(conv.id);
      }
    };
  } else {
    label.textContent = '✉ Отправить';
    btn.classList.add('bg-blue-600', 'hover:bg-blue-700');
    btn.onclick = () => document.getElementById('messageBody').focus();
  }
}
```

Вызов — в методе `renderConversation(conv)` (или аналогичном существующем), плюс в обработчике SSE `conversation.updated`.

### Dropdown toggle

```js
bindSecondaryMenu() {
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('secondaryMenu');
    const toggle = e.target.closest('#secondaryMenuToggle');
    if (toggle) { menu.classList.toggle('hidden'); return; }
    if (!e.target.closest('#secondaryMenu')) menu.classList.add('hidden');
  });

  document.getElementById('secondaryMenu').addEventListener('click', (e) => {
    const action = e.target.closest('[data-action]')?.dataset.action;
    if (!action) return;
    document.getElementById('secondaryMenu').classList.add('hidden');
    const convId = this.currentConversationId;
    if (action === 'transfer') this.openTransferModal(convId);
    else if (action === 'resolve') this.resolveConversationWithUndo(convId);
    else if (action === 'needs-help') this.callForHelp(convId);
  });
}
```

### Commit
```
UI(Messenger): контекстный главный CTA + ⋯ меню в шапке диалога

Крупная primary-кнопка меняется по ui_status (Взять/Переоткрыть/
Отправить/Забрать). ⋯ меню: Передать, Завершить, Позвать руководителя.
Правая колонка с селектами сохранена для продвинутых действий.
```

---

## Task 7: Модалка «Завершить» + undo toast (5 секунд)

**Files:** `backend/templates/ui/messenger_conversations_unified.html` (модалка + toast в конце `<body>`), `operator-panel.js` (метод `resolveConversationWithUndo`)

Разметка и JS — см. v1 плана (Task 7) без изменений. Ключевой метод:

```js
resolveConversationWithUndo(convId) {
  const modal = document.getElementById('resolveModal');
  const toast = document.getElementById('undoResolveToast');

  modal.classList.remove('hidden');
  modal.querySelector('[data-modal-cancel]').onclick = () => modal.classList.add('hidden');
  modal.querySelector('[data-modal-confirm]').onclick = () => {
    modal.classList.add('hidden');
    toast.classList.remove('hidden');

    const timer = setTimeout(async () => {
      toast.classList.add('hidden');
      await this.patchConversation(convId, {status: 'resolved'});
    }, 5000);

    toast.querySelector('[data-toast-undo]').onclick = () => {
      clearTimeout(timer);
      toast.classList.add('hidden');
    };
  };
}
```

Использует существующий `patchConversation()` → `PATCH /api/conversations/{id}/`.

Commit:
```
UI(Messenger): модалка подтверждения Завершить + 5с undo-toast

Защита от случайного закрытия. После подтверждения 5 секунд
на отмену, потом patchConversation(status='resolved').
```

---

## Task 8: Модалка «Передать» с обязательной причиной

**Files:** `messenger_conversations_unified.html` (модалка), `operator-panel.js` (метод `openTransferModal`)

### Backend: ничего нового
- Endpoint `POST /api/messenger/conversations/{id}/transfer/` — из Plan 1
- `GET /api/conversations/agents/?branch_id=&online=1` — из Task 5
- `GET /api/messenger/branches/` — из Task 5

### JS (внутри класса)

```js
async openTransferModal(convId) {
  const modal = document.getElementById('transferModal');
  const branchSel = modal.querySelector('#transferBranchSelect');
  const mgrSel = modal.querySelector('#transferManagerSelect');
  const warn = modal.querySelector('#transferCrossBranchWarn');
  const reason = modal.querySelector('#transferReason');
  const submit = modal.querySelector('#transferSubmit');

  const conv = this.currentConversation;  // уже закэширован
  const currentBranchId = conv?.branch?.id || conv?.branch_id;

  const branches = await fetch('/api/messenger/branches/').then(r => r.json());
  branchSel.innerHTML = branches.map(b =>
    `<option value="${b.id}" ${b.id === currentBranchId ? 'selected' : ''}>${b.name}</option>`
  ).join('');

  const loadManagers = async () => {
    const bid = branchSel.value;
    const list = await fetch(`/api/conversations/agents/?branch_id=${bid}&online=1`).then(r => r.json());
    mgrSel.innerHTML = '<option value="">— выбрать —</option>' +
      list.filter(m => m.id !== window.currentUserId).map(m =>
        `<option value="${m.id}">${m.name} ${m.online ? '🟢' : '⚪'}</option>`
      ).join('');
    warn.classList.toggle('hidden', String(bid) === String(currentBranchId));
  };
  branchSel.onchange = loadManagers;
  await loadManagers();

  const validate = () => {
    submit.disabled = reason.value.trim().length < 5 || !mgrSel.value;
  };
  reason.oninput = validate;
  mgrSel.onchange = validate;
  reason.value = '';
  validate();

  submit.onclick = async () => {
    submit.disabled = true;
    const res = await fetch(`/api/messenger/conversations/${convId}/transfer/`, {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-CSRFToken': this.csrfToken},
      body: JSON.stringify({to_user_id: Number(mgrSel.value), reason: reason.value.trim()}),
    });
    if (res.ok) {
      modal.classList.add('hidden');
      this.showToast('Диалог передан');
      this.reloadConversation(convId);
    } else {
      const err = await res.json().catch(() => ({}));
      this.showToast(err.detail || 'Ошибка передачи', 'error');
      submit.disabled = false;
    }
  };

  modal.querySelector('[data-modal-cancel]').onclick = () => modal.classList.add('hidden');
  modal.classList.remove('hidden');
}
```

Разметка модалки — см. v1 плана (Task 8 Step 2), но с id вместо data-*.

Commit:
```
Feat(Messenger): модалка передачи диалога

Выбор филиала + онлайн-менеджера (из существующего /agents/),
обязательная причина (min 5 символов), красное предупреждение
при cross-branch. Использует POST /transfer/ из Plan 1.
```

---

## Task 9: Черновик сообщения в localStorage (contenteditable)

**Files:** `operator-panel.js`, `messenger_conversations_unified.html` (баннер)

### Особенность: #messageBody — contenteditable div, не textarea

Используем `.textContent` (без HTML-форматирования) или `.innerText`. Сохраняем при `input`, `blur`, `beforeunload`. Очищаем после успешной отправки.

```js
const DRAFT_KEY = (convId) => `msgr:draft:${convId}`;

hookDraftAutosave(convId) {
  const input = document.getElementById('messageBody');
  if (!input) return;
  const key = DRAFT_KEY(convId);

  const saved = localStorage.getItem(key);
  if (saved) {
    input.textContent = saved;
    this.showDraftBanner(convId, saved);
  }

  const saveHandler = () => {
    const text = input.textContent.trim();
    if (text) localStorage.setItem(key, text);
    else localStorage.removeItem(key);
  };
  input.addEventListener('input', saveHandler);
  input.addEventListener('blur', saveHandler);

  if (!this._beforeunloadHooked) {
    window.addEventListener('beforeunload', () => {
      const cid = this.currentConversationId;
      if (!cid) return;
      const t = document.getElementById('messageBody')?.textContent.trim();
      if (t) localStorage.setItem(DRAFT_KEY(cid), t);
    });
    this._beforeunloadHooked = true;
  }
}

clearDraft(convId) {
  localStorage.removeItem(DRAFT_KEY(convId));
  const banner = document.getElementById('draftBanner');
  if (banner) banner.classList.add('hidden');
}

showDraftBanner(convId, text) {
  const banner = document.getElementById('draftBanner');
  if (!banner) return;
  banner.querySelector('#draftText').textContent = `Черновик: «${text.slice(0,60)}${text.length>60?'…':''}»`;
  banner.classList.remove('hidden');
  banner.querySelector('#draftDelete').onclick = () => {
    this.clearDraft(convId);
    document.getElementById('messageBody').textContent = '';
  };
  banner.querySelector('#draftDismiss').onclick = () => banner.classList.add('hidden');
}
```

В `sendMessage()` после успеха (строка ~2020) — `this.clearDraft(this.currentConversationId)`.

В методе открытия диалога (`openConversation` или аналог) — `this.hookDraftAutosave(conv.id)`.

Разметка баннера — см. v1 плана.

Commit:
```
UI(Messenger): автосохранение черновика в localStorage

contenteditable #messageBody → localStorage. Сохранение при input/blur/
beforeunload, восстановление при открытии диалога, очистка после отправки.
```

---

## Task 10: UI приватных заметок (через существующий `composeMode=internal`)

**Files:** `messenger_conversations_unified.html` (toggle над полем + визуальное оформление), `operator-panel.js` (улучшить existing composeMode)

### Исходное состояние

В JS уже есть `this.composeMode` который переключает `direction` между `'out'` и `'internal'` при отправке. Нужно понять, **есть ли уже видимый тоггл** для этого режима. Subagent должен найти его через grep `composeMode` и:
- Если тоггл существует — улучшить визуально (крупнее, понятнее)
- Если нет — добавить новый

### Визуализация в ленте сообщений

В методе рендера сообщения (ищем по grep `direction` и `renderMessage` в operator-panel.js) — для `direction === 'internal'`:
- Фон `bg-amber-50`
- Левая полоса `border-l-4 border-amber-400`
- Подпись «🔒 Видно только сотрудникам»

Если уже сделано — проверить и оставить. Если нет — добавить.

### Toggle разметка (если его ещё нет)

```html
<div class="px-3 pt-2 flex items-center gap-2 bg-white border-t" id="composeModeBar">
  <div class="inline-flex rounded-lg border border-gray-300 overflow-hidden text-sm">
    <button type="button" data-compose-mode="out"
            class="px-3 py-1.5 bg-blue-600 text-white font-medium">
      💬 Клиенту
    </button>
    <button type="button" data-compose-mode="internal"
            class="px-3 py-1.5 bg-white text-gray-700 hover:bg-gray-50">
      🔒 Для своих
    </button>
  </div>
  <span id="composeModeHint" class="text-xs text-gray-500">Ответ увидит клиент</span>
</div>
```

JS:
```js
setComposeMode(mode) {
  this.composeMode = mode;  // 'out' или 'internal'
  const input = document.getElementById('messageBody');
  const hint = document.getElementById('composeModeHint');
  const btnOut = document.querySelector('[data-compose-mode="out"]');
  const btnIn = document.querySelector('[data-compose-mode="internal"]');

  if (mode === 'internal') {
    input.classList.add('bg-amber-50', 'border-amber-300');
    input.dataset.placeholder = 'Заметка видна только сотрудникам...';
    hint.textContent = '🔒 Видно только сотрудникам — клиент НЕ увидит';
    hint.classList.add('text-amber-700', 'font-medium');
    btnIn.classList.add('bg-amber-400', 'text-gray-900');
    btnIn.classList.remove('bg-white', 'text-gray-700');
    btnOut.classList.remove('bg-blue-600', 'text-white');
    btnOut.classList.add('bg-white', 'text-gray-700');
  } else {
    input.classList.remove('bg-amber-50', 'border-amber-300');
    input.dataset.placeholder = 'Введите сообщение. Enter — отправить, Shift+Enter — перенос';
    hint.textContent = 'Ответ увидит клиент';
    hint.classList.remove('text-amber-700', 'font-medium');
    btnOut.classList.add('bg-blue-600', 'text-white');
    btnOut.classList.remove('bg-white', 'text-gray-700');
    btnIn.classList.remove('bg-amber-400', 'text-gray-900');
    btnIn.classList.add('bg-white', 'text-gray-700');
  }
}
```

Привязать к кнопкам через `click` в `init`/`bind` методе.

### Важно

- **НЕ используем** `Message.is_private` из Plan 1 — устарело
- В `sendMessage` уже передаётся `direction: this.composeMode === 'internal' ? 'internal' : 'out'` — проверить и убедиться
- Widget API уже фильтрует INTERNAL — ничего не меняем

Commit:
```
UI(Messenger): улучшенный toggle приватных заметок

Крупный переключатель «💬 Клиенту / 🔒 Для своих» над полем ввода.
В приватном режиме — жёлтый фон, предупреждение, жёлтая подсветка
сообщения в ленте. Использует существующий composeMode=internal,
НЕ Message.is_private (оно deprecated после Plan 1).
```

---

## Task 11: Видимые quick-reply кнопки

**Files:** `messenger_conversations_unified.html`, `operator-panel.js`

- Контейнер `<div id="quickRepliesRow" class="px-3 py-2 flex flex-wrap gap-2 bg-white border-t">`
- Метод `loadQuickReplies()`:

```js
async loadQuickReplies() {
  const container = document.getElementById('quickRepliesRow');
  if (!container) return;
  const res = await fetch('/api/canned-responses/');
  const list = await res.json();
  const items = (Array.isArray(list) ? list : list.results || [])
    .filter(r => r.is_quick_button)
    .slice(0, 6);
  container.innerHTML = items.map(r =>
    `<button type="button" class="px-3 py-1.5 text-sm rounded-full bg-gray-100 hover:bg-gray-200 border border-gray-300 transition-colors"
             data-body="${this.escapeHtml(r.body)}">${this.escapeHtml(r.title)}</button>`
  ).join('');
  container.querySelectorAll('[data-body]').forEach(btn => {
    btn.onclick = () => {
      const input = document.getElementById('messageBody');
      input.textContent = btn.dataset.body;
      input.focus();
      // позиционировать каретку в конец
      const range = document.createRange();
      range.selectNodeContents(input);
      range.collapse(false);
      const sel = window.getSelection();
      sel.removeAllRanges();
      sel.addRange(range);
    };
  });
}
```

Вызов — при инициализации панели + при переоткрытии диалога.

Commit:
```
UI(Messenger): ряд quick-reply кнопок

Кнопки с CannedResponse.is_quick_button=True (до 6).
Клик вставляет текст в contenteditable #messageBody
(не отправляет, даёт отредактировать).
```

---

## Task 12: Кнопка «Позвать руководителя» + бейдж в списке

**Files:** `operator-panel.js`

### JS метод

```js
async callForHelp(convId) {
  if (!confirm('Позвать руководителя? Диалог будет помечен как требующий помощи. Филиальный директор/РОП увидит его в своём списке.')) return;
  const res = await fetch(`/api/conversations/${convId}/needs-help/`, {
    method: 'POST',
    headers: {'X-CSRFToken': this.csrfToken},
  });
  if (res.ok) {
    this.showToast('Руководитель уведомлён');
    this.reloadConversation(convId);
  } else {
    this.showToast('Ошибка', 'error');
  }
}
```

### Бейдж в списке диалогов

Найти в `operator-panel.js` функцию рендера элемента списка диалогов (grep по `conversationItem`/`renderConversationListItem`). Для `conv.needs_help === true` добавить:

```html
<span class="text-xs px-2 py-0.5 bg-amber-100 text-amber-800 rounded-full font-medium">🆘 Нужна помощь</span>
```

### Добавить `needs_help` в ConversationSerializer

Проверить `backend/messenger/serializers.py` — если `needs_help`/`needs_help_at` не в fields, добавить (read_only_fields).

Commit:
```
Feat(Messenger): кнопка Позвать руководителя + бейдж в списке

POST /needs-help/ ставит needs_help=True. Conversations с этим
флагом в списке показываются с оранжевым бейджем 🆘. Push/email
РОПу — в Plan 3.
```

---

## Task 13: Прогон тестов + staging deploy + Changelog

- [ ] `bash scripts/test.sh messenger accounts` — всё зелёное
- [ ] `cd backend && python manage.py makemigrations --dry-run --check`
- [ ] `git push origin main`
- [ ] Staging deploy:
  ```bash
  ssh -i ~/.ssh/id_proficrm_deploy sdm@5.181.254.172 "cd /opt/proficrm-staging && git pull && docker compose -f docker-compose.staging.yml exec -T web python manage.py migrate && docker compose -f docker-compose.staging.yml exec -T web python manage.py collectstatic --noinput && docker compose -f docker-compose.staging.yml up -d web"
  ```
- [ ] **Smoke через Playwright Browser MCP:**
  1. Логин manager
  2. Открыть диалог → проверить primary CTA и ⋯ меню
  3. ⋯ → Завершить → модалка → подтвердить → undo toast → отменить → статус не изменён
  4. ⋯ → Передать → модалка → выбрать менеджера + причину → передать → проверка ConversationTransfer
  5. Toggle «Для своих» → отправить → проверить что в ленте оператора есть, в widget нет
  6. Reload → черновик восстановился
  7. Quick-reply кнопка → текст в поле
  8. «Позвать руководителя» → `needs_help=True` в Django admin
- [ ] Changelog за дату деплоя + обновить `docs/current-sprint.md`
- [ ] Commit + push docs

---

## Self-Review Checklist

- [ ] Все новые тесты зелёные
- [ ] Нет новых миграций после `makemigrations --dry-run --check`
- [ ] `is_private` в UI НЕ используется (только `direction=INTERNAL`)
- [ ] Undo toast реально откладывает PATCH на 5с
- [ ] Черновик контекста `#messageBody` (contenteditable) восстанавливается
- [ ] INTERNAL-сообщения не приходят в widget SSE (проверка curl)
- [ ] Primary CTA в шапке меняется корректно по `ui_status`
- [ ] Правая колонка селектов НЕ сломана (regression-чек)

---

## Выход и следующий шаг

**Следующий план:** Plan 3 — Уведомления + эскалация (desktop notifications, звуки, Celery-task эскалации по `waiting_minutes` ≥ порогов, push РОПу при `needs_help`, настройки `/settings/messenger/notifications/`).
