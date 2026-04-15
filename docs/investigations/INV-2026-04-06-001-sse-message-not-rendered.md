---
investigation_id: INV-2026-04-06-001
status: completed
created: 2026-04-06
topic: SSE сообщения оператора не рендерятся в виджете
severity: P0
---

# INV-2026-04-06-001: SSE сообщения оператора не рендерятся в виджете

## Executive Summary

**Проблема**: Оператор отправляет сообщение, но оно не появляется в виджете клиента. Серверные логи подтверждают отправку данных (701 байт), `since_id` обновляется, но DOM виджета не содержит сообщения оператора.

**Корневая причина**: Двойная дедупликация в SSE event handler. Строка 740 в `widget.js` добавляет `msg.id` в `receivedMessageIds` Set при фильтрации, а затем `addMessageToUI()` (строка 1097) видит, что ID уже в Set, и немедленно возвращает `return` -- сообщение никогда не добавляется в DOM.

**Рекомендация**: Убрать `this.receivedMessageIds.add(msg.id)` из SSE filter (строка 740), по аналогии с poll handler (строка 609-614), который НЕ добавляет в Set при фильтрации.

## Problem Statement

### Наблюдаемое поведение
1. Клиент отправляет сообщение через виджет -- оно отображается корректно
2. Оператор отвечает через панель -- сообщение сохраняется в БД
3. SSE стрим отдаёт 701 байт (вместо 276 heartbeat) -- данные доставлены
4. `since_id` обновляется 104 -> 105 в следующем запросе -- JS получил event
5. Но DOM виджета содержит только 1 сообщение (от клиента)

### Ожидаемое поведение
Сообщение оператора должно появляться в виджете в реальном времени через SSE.

### Среда
- Staging: `crm-staging.groupprofi.ru`
- Gunicorn: `--worker-class gthread --workers 4 --threads 8`
- Nginx: `proxy_buffering off` для `/api/widget/stream/`

## Investigation Process

### Гипотезы (начальные)

1. **Буферизация gthread Gunicorn** -- StreamingHttpResponse буферизуется и отдаётся пакетом
2. **Дедупликация через receivedMessageIds** -- msg#105 уже в Set
3. **messagesContainer не существует** в момент SSE event
4. **Формат direction** не совпадает между сервером и клиентом

### Гипотеза 1: Буферизация gthread -- ОПРОВЕРГНУТА

**Доказательства против**:
- `since_id` обновляется (104 -> 105), значит JS-клиент **получил** и **распарсил** SSE event
- Nginx конфигурация корректна: `proxy_buffering off` (staging.conf:95)
- Django ответ содержит `X-Accel-Buffering: no` (widget_api.py:1249)
- Если бы буферизация была проблемой, `since_id` не обновился бы до закрытия стрима

### Гипотеза 2: Двойная дедупликация -- ПОДТВЕРЖДЕНА (КОРНЕВАЯ ПРИЧИНА)

**Механизм сбоя (цепочка)**:

```
SSE event приходит с msg#105
    |
    v
widget.js:737-742 -- фильтр newMessages:
    msg.id = 105
    receivedMessageIds.has(105) = false  -->  OK, пропускаем
    receivedMessageIds.add(105)          -->  ДОБАВИЛИ В SET (строка 740!)
    return true  -->  msg попадает в newMessages
    |
    v
widget.js:743-748 -- цикл по newMessages:
    sinceId обновляется на 105            -->  ОК (строка 744-746)
    this.addMessageToUI(msg)              -->  вызывается (строка 747)
    |
    v
widget.js:1091-1098 -- addMessageToUI:
    message.id = 105
    this.receivedMessageIds.has(105)      -->  TRUE! (добавлен на строке 740!)
    return;                               -->  ВЫХОД. Сообщение НЕ добавлено в DOM.
```

**Сравнение с poll handler** (который работает корректно):

```javascript
// POLL handler (строки 609-614) -- ПРАВИЛЬНО:
const newMessages = data.messages.filter(msg => {
    if (!msg.id) return false;
    if (this.receivedMessageIds.has(msg.id)) return false;
    return true;  // НЕ добавляет в Set
});
// addMessageToUI вызывается, ID ещё не в Set -- сообщение рендерится

// SSE handler (строки 737-742) -- БАГГЕД:
const newMessages = data.messages.filter(msg => {
    if (!msg.id) return false;
    if (this.receivedMessageIds.has(msg.id)) return false;
    this.receivedMessageIds.add(msg.id);  // <-- БАГОВАЯ СТРОКА 740
    return true;
});
// addMessageToUI вызывается, но ID уже в Set -- return, сообщение НЕ рендерится
```

### Гипотеза 3: messagesContainer не существует -- ОПРОВЕРГНУТА

- `render()` проверяет `document.getElementById('messenger-widget-container')` и если контейнер существует -- return (строка 1414-1416)
- `messagesContainer` создаётся один раз при первом `render()` и не пересоздаётся
- Prechat submit (`submitPrechat()`, строка 888) НЕ вызывает `render()` -- только `updatePrechatVisibility()`
- Контейнер стабилен после инициализации

### Гипотеза 4: Формат direction не совпадает -- ОПРОВЕРГНУТА

- `str(msg.direction)` для Django TextChoices возвращает `'out'` (проверено Python-скриптом)
- `widget.js:1190` проверяет `message.direction === 'out'` -- совпадает
- CSS классы `messenger-widget-message-out` и `messenger-widget-message-in` определены (widget.css:276-304)

## Root Cause Analysis

### Корневая причина

Строка 740 в `widget.js` -- в SSE event handler, `receivedMessageIds.add(msg.id)` вызывается внутри `.filter()`, **до** вызова `addMessageToUI()`. Функция `addMessageToUI()` содержит собственную проверку дубликатов (строка 1097), которая отбрасывает сообщение, т.к. ID уже в Set.

### Почему баг появился

Комментарий в poll handler (строка 607-608) гласит:
> "Фильтруем дубликаты через Set, но не помечаем их здесь как полученные -- это делает addMessageToUI, чтобы все пути добавления сообщений работали одинаково."

SSE handler (строка 721) был написан позже и НЕ следует этому контракту -- добавляет в Set преждевременно.

### Файлы с проблемой

| Файл | Строка | Проблема |
|------|--------|----------|
| `backend/messenger/static/messenger/widget.js` | 740 | `this.receivedMessageIds.add(msg.id)` внутри filter() |

## Proposed Solutions

### Подход 1 (рекомендуемый): Убрать add из SSE filter

**Описание**: Удалить строку 740 (`this.receivedMessageIds.add(msg.id)`) из SSE event handler, сделав его идентичным poll handler.

**Изменение**: 1 строка в 1 файле.

```
Файл: backend/messenger/static/messenger/widget.js
Строка 740: удалить `this.receivedMessageIds.add(msg.id);`
```

**Результат**: `addMessageToUI()` сама добавит ID в Set (строки 1100-1101) после успешного рендеринга.

**Плюсы**:
- Минимальное изменение (1 строка)
- Приводит SSE handler в соответствие с poll handler и документированным контрактом
- Не меняет логику дедупликации -- она остаётся в `addMessageToUI()`

**Минусы**:
- Нет

**Сложность**: Низкая
**Риск**: Низкий

### Подход 2: Убрать дедупликацию из addMessageToUI

**Описание**: Удалить проверку `receivedMessageIds` из `addMessageToUI()` (строки 1097-1101), оставив дедупликацию только в вызывающих функциях.

**Плюсы**:
- Упрощает `addMessageToUI()`

**Минусы**:
- Нарушает принцип defense-in-depth
- Нужно проверить ВСЕ вызовы `addMessageToUI()` (render/initialMessages, poll, SSE, send)
- Рискованнее -- если какой-то путь забудет дедупликацию, будут дубликаты в UI

**Сложность**: Средняя
**Риск**: Средний

### Подход 3: Унифицировать обработку через общую функцию

**Описание**: Создать единую функцию `processIncomingMessages(data)`, которую используют и poll, и SSE handlers.

**Плюсы**:
- Исключает расхождения между handlers навсегда
- DRY принцип

**Минусы**:
- Больше изменений
- Нужно аккуратно объединить логику (SSE сохраняет since_id в другом месте)

**Сложность**: Средняя
**Риск**: Низкий

## Implementation Guidance

### Приоритет: Критический (P0)

### Файлы для изменения

1. `backend/messenger/static/messenger/widget.js` -- строка 740

### Валидация

1. Оператор отправляет сообщение -> оно появляется в виджете в реальном времени (не при reconnect)
2. Повторные SSE events с тем же ID не создают дубликатов
3. Poll fallback продолжает работать корректно
4. `since_id` обновляется корректно

### Тестирование

1. Открыть виджет на `vm-f841f9cb.na4u.ru/chat-test.html`
2. Отправить сообщение от клиента
3. Ответить из оператор-панели
4. Убедиться что ответ появляется в виджете в течение 1-2 секунд
5. Отправить несколько сообщений подряд -- все должны появиться
6. Проверить DevTools Console на отсутствие ошибок

## Risks and Considerations

- **Breaking changes**: Нет. Изменение восстанавливает документированное поведение.
- **Performance**: Нет влияния.
- **Side effects**: Нет. `addMessageToUI()` продолжит добавлять ID в Set.

## Сопутствующие находки (P2/P3)

### Auto-reply не отображается при первом подключении (P2)

`since_id` из localStorage может быть больше ID auto-reply, если сессия была создана ранее. SSE фильтрует `id__gt=last_id` (widget_api.py:1169), поэтому auto-reply не попадает в стрим.

**Рекомендация**: При bootstrap, если есть initial_messages с `direction=out`, отображать их сразу. Или сбрасывать `since_id` при новом bootstrap.

## Documentation References

### Tier 0 (Project Internal)

- `docs/current-sprint.md`: описание бага на строке 56-61
- `widget_api.py:1149-1260`: SSE event_stream generator
- `widget.js:700-795`: SSE event handler (startRealtime)
- `widget.js:1091-1247`: addMessageToUI
- `widget.js:532-643`: poll handler (эталонная реализация)

### Tier 1 (Context7)

Не требовалось -- баг в прикладном JS-коде проекта, не в фреймворке.

## Investigation Log

| Время | Действие |
|-------|---------|
| 1 | Прочитал current-sprint.md -- контекст бага |
| 2 | Прочитал widget_api.py:1149 -- SSE generator, формат данных |
| 3 | Прочитал widget.js:700 -- SSE handler, обнаружил строку 740 |
| 4 | Прочитал widget.js:1091 -- addMessageToUI, обнаружил двойную дедупликацию |
| 5 | Прочитал widget.js:609 -- poll handler, подтвердил отсутствие add в filter |
| 6 | Проверил Django TextChoices str() -- формат direction корректен |
| 7 | Проверил nginx конфигурацию -- proxy_buffering off |
| 8 | Проверил render() -- контейнер не пересоздаётся |
| 9 | Проверил submitPrechat() -- не вызывает render() |
| 10 | Файлов проанализировано: 5, команд выполнено: 12, гипотез проверено: 4 |
