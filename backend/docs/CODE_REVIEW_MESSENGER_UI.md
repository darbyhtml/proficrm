# Код-ревью: Live-chat UI и настройки (feature/messenger-stage2)

## Что проверено

- Шаблоны настроек Messenger (overview, source choose, inbox ready, inbox form, routing, nav)
- View `settings_messenger_*` и контекст для создания/редактирования
- Жёстко прописанные URL, доступность при `inbox=None`, вложенные формы

---

## Исправлено в рамках ревью

### 1. Форма создания Inbox (inbox=None)

- **Проблема:** В шаблоне `messenger_inbox_form.html` блок «Конструктор виджета» и скрипты копирования использовали `inbox.settings` и `inbox.widget_token`. При создании источника `inbox` может быть `None` → AttributeError при рендере.
- **Исправление:** В view добавлен контекст `widget_display` (словарь с title, greeting, color, show_email, show_phone). В шаблоне блок виджета переведён на `widget_display.*`. Скрипты copyToken/copyCode/copyLazyCode обёрнуты в `{% if inbox %}`.

### 2. Жёстко прописанные URL в маршрутизации

- **Проблема:** В `messenger_routing_list.html` использовались `/settings/messenger/routing/new/`, `/settings/messenger/routing/{{ rule.id }}/`, `/settings/messenger/routing/{{ rule.id }}/delete/`.
- **Исправление:** Заменены на `{% url 'settings_messenger_routing_create' %}`, `{% url 'settings_messenger_routing_edit' rule.id %}`, `{% url 'settings_messenger_routing_delete' rule.id %}`.

### 3. Глобальный inbox в форме правила

- **Проблема:** В `messenger_routing_form.html` в опциях выбора inbox выводилось `{{ inbox.branch.name }}`. У глобального inbox `branch` может быть `None` → ошибка при рендере.
- **Исправление:** Вывод филиала заменён на `{% if inbox.branch %}{{ inbox.branch.name }}{% else %}Глобальный{% endif %}`.

### 4. Вложенные формы (invalid HTML)

- **Проблема:** В форме редактирования inbox внутри основной `<form>` были вложенные `<form>` для «Сгенерировать токен» и «Удалить Inbox». В HTML формы не могут быть вложены; браузеры ведут себя непредсказуемо.
- **Исправление:** Формы regenerate и delete вынесены после закрытия основной формы. У каждой указан явный `action="{% url 'settings_messenger_inbox_edit' inbox.id %}"`. Кнопка «Сгенерировать новый токен» вынесена в отдельный блок под основной формой.

### 5. Дубликат импорта в views.py

- **Проблема:** `import logging` был указан дважды подряд.
- **Исправление:** Второй импорт удалён.

---

## Рекомендации (выполнено)

1. **Хлебные крошки:** Во всех шаблонах Live-chat ссылка «Настройки» заменена на `{% url 'settings_dashboard' %}` (messenger_inbox_form, routing_list, routing_form, analytics, health, overview, inbox_ready, source_choose).
2. **Навигация Live-chat:** В `messenger_nav.html` для активного пункта добавлен `aria-current="page"` на `<span>`.

## Рекомендации (не критично, можно позже)

1. **Демо-виджет:** В `messenger_inbox_form.html` ссылка «Открыть демо» ведёт на `/widget-demo/?token=...`. Имеет смысл вынести путь в url name (если такой view есть) или задать через настройки.
2. **Tailwind и вкладки:** Стили активной вкладки в форме inbox переключены на явное управление классами в JS (bg-white, text-brand-teal и т.д.) — не зависит от плагина aria.

---

## Что можно не трогать

- Логика view (редиректы, обработка action, сохранение настроек) — согласована с формой и отдельными формами.
- Widget API, throttling, CORS — выходят за рамки этого ревью UI.
