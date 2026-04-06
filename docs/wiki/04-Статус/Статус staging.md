---
tags: [статус, staging]
---

# Статус: Staging

> Последнее обновление: 2026-04-06

## Общее состояние

| Параметр | Значение |
|----------|---------|
| URL | `crm-staging.groupprofi.ru` |
| Путь | `/opt/proficrm-staging/` |
| HEAD | `92615b5` |
| Ветка | `main` |

## Контейнеры

| Сервис | Статус |
|--------|--------|
| web | healthy (gthread 4w×8t) |
| nginx | running |
| db | healthy (PostgreSQL 16) |
| redis | healthy |
| celery | healthy |
| celery-beat | running |
| websocket | running |

## Текущие эксперименты

### Мессенджер Live-Chat
- Виджет работает на `vm-f841f9cb.na4u.ru/chat-test.html`
- Inbox #8 (token: `AMseFuK1...`, branch_id=1)
- SSE стримы работают (gthread fix от 2026-04-06)
- CORS разделён: nginx preflight + Django response
- Widget CSS автозагрузка работает

### Последние фиксы (2026-04-06)
- [x] **SSE real-time доставка**: тройная дедупликация в widget.js — `receivedMessageIds.add()` перед `addMessageToUI()` в 3 местах
- [x] **Host nginx**: добавлены location-блоки с `proxy_buffering off` для SSE
- [x] **Admin reply**: `role == MANAGER` → `is_superuser or role in (MANAGER, ADMIN)` в 3 местах
- [x] Gunicorn: 2 sync → gthread 4w×8t (SSE блокировал все воркеры)
- [x] Widget stream: `changed = False` сбрасывал флаг read_up_to
- [x] Operator typing: инвертирован (`is False` → `is True`)
- [x] Operator stream: дублировал ВСЕ сообщения при reconnect
- [x] Offline email: `GlobalMailAccount.reply_to` AttributeError

### Round 4 фиксы (2026-04-05)
- [x] operator-panel.js: orphaned label popup listeners
- [x] markConversationRead: try-catch
- [x] Date separator: innerHTML → createElement (XSS)
- [x] merge-contacts: admin auth + UUID validation
- [x] Serializers: `__all__` → explicit fields
- [x] Widget: destroy() + CSS autoload
- [x] Status filter: validation vs choices

## Что нужно протестировать

- [x] Real-time сообщения (оператор → виджет через SSE) — подтверждено Playwright
- [ ] Typing-индикаторы
- [ ] Кампании (автоприглашения)
- [ ] Оценка диалога (rating)
- [ ] Push-уведомления
- [ ] Автоматизация (эскалация, auto-resolve)

---

Связано: [[Статус прод]] · [[Мессенджер]] · [[Docker и сервисы]]
