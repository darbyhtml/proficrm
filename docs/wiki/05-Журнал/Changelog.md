---
tags: [журнал, changelog]
---

# Changelog

> Claude Code автоматически обновляет этот файл при каждом значимом изменении.

---

## 2026-04-06

### Fix: SSE real-time доставка — тройная дедупликация
**Коммиты:** `b26fadb`, `6c3ba20`

**Проблема:** Сообщения оператора не появлялись в виджете через SSE. При перезагрузке страницы сохранённые сообщения тоже не рендерились.

**Корневая причина:** Одна и та же ошибка в 3 местах `widget.js` — `receivedMessageIds.add(msg.id)` вызывался ДО `addMessageToUI()`, которая проверяла тот же Set и возвращала `return` (сообщение не рендерилось).

**Ложный след:** gthread буферизация — curl внутри Docker доказал что gthread стримит SSE инкрементально.

**Исправления:**
- widget.js: удалён `receivedMessageIds.add()` из SSE handler, render() savedMessages, render() initialMessages
- Host nginx (`/etc/nginx/sites-available/crm-staging`): добавлены location-блоки с `proxy_buffering off` для SSE
- Роль admin: `role == MANAGER` → `is_superuser or role in (MANAGER, ADMIN)` в `messenger_panel.py`, `api.py` (3 места)

**Подтверждение:** Playwright Browser MCP — SSE real-time доставка оператор → виджет работает.

---

### Fix: SSE real-time и производительность мессенджера
**Коммиты:** `b9e3f8b`, `18deaa7`

**Проблема:** Сообщения приходили с задержкой, real-time не работал — требовалось обновление страницы.

**Корневая причина:** Gunicorn (2 sync workers) полностью блокировался SSE-стримами. Каждый SSE-стрим (widget 25с + operator per-conv 30с + notifications 55с) занимал воркер, оставляя 0 воркеров для API-запросов.

**Исправления:**
- Gunicorn: переход на `gthread` (4 workers × 8 threads = 32 соединения)
- Widget stream: `changed = False` сбрасывал флаг `read_up_to`
- Operator stream: typing инвертирован (`is False` → `is True`)
- Operator per-conversation: дублировал все сообщения при каждом reconnect
- Offline email: `GlobalMailAccount.reply_to` AttributeError
- gevent → gthread (несовместимость с psycopg3)

---

## 2026-04-05

### Fix: Round 4 production hardening мессенджера
**Коммиты:** `eeb51ac`, `27131ce`, `34c19cb`

**Исправления:**
- operator-panel.js: утечка event listeners в label popup
- markConversationRead: обёрнуто в try-catch
- Date separator: innerHTML → createElement (XSS-защита)
- merge-contacts: авторизация (admin only) + UUID validation
- Serializers: `__all__` → explicit fields (белый список)
- Widget: destroy() для SPA + CSS autoload для внешних сайтов
- Status filter: validation против Conversation.Status.choices
- CORS: разделение nginx preflight + Django response
- WidgetSession: добавлены поля bound_ip, created_at
- Widget campaigns: добавлен CORS handler

---

## 2026-04-02

### Feature: Мессенджер влит в main
- Feature-ветка удалена, одна ветка `main`
- `MESSENGER_ENABLED=1` в .env
- Полная система live-chat (Chatwoot-style)

---

*Claude Code обновляет этот файл автоматически.*
