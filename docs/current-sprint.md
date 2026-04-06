# Текущий спринт

## Текущая задача

Мессенджер (live-chat) — доведение до production-grade SaaS качества.

## Сделано в этом спринте

**[2026-04-06]** — SSE real-time fix + gthread
- Диагностика: 2 sync workers блокировались 3 SSE стримами → 0 воркеров для API
- Переход на gthread (4w×8t=32 потока)
- Исправлено 5 багов: typing инвертирован, stream дублировал сообщения, changed flag, read_up_to, email notify
- Коммиты: `b9e3f8b`, `18deaa7`
- Задеплоено на staging, проверено curl'ом (3 параллельных SSE + health = всё OK)

**[2026-04-06]** — Obsidian wiki + система документации
- Создана структура `docs/wiki/` (21 файл, 5 разделов)
- Создана система `CLAUDE.md` + `docs/architecture.md` + `docs/decisions.md` + `docs/problems-solved.md`
- Claude Code memory обновлена

**[2026-04-05]** — Round 4 production hardening
- operator-panel.js: утечка listeners, XSS в date separator
- merge-contacts: авторизация + UUID validation
- Serializers: `__all__` → explicit fields
- Widget: destroy(), CSS autoload, CORS split
- Коммиты: `eeb51ac`, `27131ce`, `34c19cb`, `50f1efe`, `5a88c6e`, `c024e71` и др.

**[2026-04-04-05]** — Widget на внешнем сайте
- Тестирование на vm-f841f9cb.na4u.ru/chat-test.html
- Решены CORS, CSS autoload, WidgetSession, Inbox branch проблемы
- Inbox #8 создан и работает

**[2026-04-06]** — Комплексное тестирование live-chat (Browser MCP)

Проведено сквозное тестирование с Playwright Browser MCP на staging.

**Результаты по компонентам:**

| Компонент | Статус | Детали |
|-----------|--------|--------|
| Staging health | OK | Все 7 контейнеров UP, celery unhealthy (но работает) |
| Widget загрузка | OK | Виджет загружается на `vm-f841f9cb.na4u.ru/chat-test.html`, CSS autoload работает |
| Prechat-форма | OK | Имя, Email, Телефон, согласие. Кнопка disabled до чекбокса |
| Отправка из виджета | OK | Сообщение доставлено, ✓ отображается, время корректное |
| Оператор-панель | OK | Сообщение видно, диалог в списке, контакт/детали отображаются |
| Auto-reply | OK | "Здравствуйте! Менеджер скоро подключится." — приходит |
| Ответ оператора | OK | Отправляется из панели, msg сохраняется в БД |
| CORS preflight | OK | OPTIONS → 204, nginx обрабатывает корректно |
| Campaigns API | OK | 200, пустой массив (нет активных кампаний) |
| SSE подключение | OK | Widget подключается к `/api/widget/stream/`, reconnect ~25с |
| **SSE доставка** | **OK** | РЕШЕНО: тройная дедупликация + host nginx buffering. Real-time доставка подтверждена |
| JS API | OK | `window.ProfiMessenger` доступен (open/close/toggle/destroy/isOpen) |

**Найденные и исправленные баги:**

1. **P0 — SSE real-time доставка — РЕШЕНО**
   - Корневая причина: тройная дедупликация в `widget.js` — `receivedMessageIds.add()` вызывался ДО `addMessageToUI()`, которая проверяла тот же Set
   - Три места: SSE handler, render() savedMessages, render() initialMessages
   - Дополнительно: host nginx без `proxy_buffering off` для SSE
   - Ложный след: gthread буферизация (curl доказал что стрим инкрементальный)
   - **Коммиты**: `b26fadb`, `6c3ba20`

2. **P1 — Роль admin не может отвечать — РЕШЕНО**
   - Замена `role == MANAGER` на `is_superuser or role in (MANAGER, ADMIN)` в 3 местах
   - **Файлы**: `messenger_panel.py:51`, `api.py:217`, `api.py:559`

3. **P2 — Auto-reply не отображается в виджете при первом подключении**
   - Причина: `since_id` из localStorage уже больше id auto-reply

## Следующий шаг

1. **Typing-индикаторы** — протестировать (SSE работает)
2. **Нагрузочное тестирование** — несколько одновременных виджетов
3. **P2 auto-reply** — пересмотреть since_id при первом подключении
4. **Деплой на прод** — после полного QA

## Стоп-точка

Сессия: SSE P0 баг полностью решён и подтверждён тестами через Playwright Browser MCP. Real-time доставка работает. P1 admin-reply тоже исправлен. HEAD: `6c3ba20`.
