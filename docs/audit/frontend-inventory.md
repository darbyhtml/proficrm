# Инвентаризация фронтенда CRM ПРОФИ
_Снапшот: 2026-04-20. Wave 0.1 (инвентаризация перед CSP/tokens/rewrite-рефакторингом)._
_Автор: Claude Code (агентский аудит фронт-структуры перед Wave 1+)._
_Метод: Glob + Grep + wc + inline Python (regex по `<script>`/`<style>`)._

## Сводка

| Категория | Значение |
|-----------|----------|
| HTML-шаблонов всего | **112** (в `backend/templates/`) |
| из них в `ui/` | 94 |
| из них в `messenger/` (widget demo) | 1 (`backend/messenger/templates/messenger/widget_demo.html`) |
| из них `404/500/registration/notifications` | 5 |
| Суммарный LOC всех шаблонов | ≈ 36 200 строк |
| Самый большой шаблон | `ui/company_detail.html` — **8 781 LOC** (абсолютный god-template) |
| `base.html` | 3 781 LOC |
| JS-файлов активных | **8** (не считая Django admin / DRF vendor) |
| Суммарный объём активного JS | ≈ 370 KB |
| CSS-файлов активных | **3** (compiled Tailwind + widget.css + 689-строчный frontend/src/main.css) |
| Compiled Tailwind `backend/static/ui/css/main.css` | 81 KB (одна строка, минифицирован) |
| Dead JS-файлы | **1** (`backend/staticfiles/ui/custom-datetime-picker.js` — артефакт collectstatic после удаления из исходников 2026-04-20) |
| Шаблоны с inline `<script>` > 50 LOC | **20** (см. красный список ниже) |
| Шаблоны на v2-токенах (`var(--v2-*)`) | **17** файлов, 558 вхождений |
| Шаблоны на v3-токенах (`var(--v3-*)`) | **10** файлов, 84 вхождения (v3 ещё только раскатывается) |
| Дубликаты между v2_styles.html ↔ v3_styles.html | v3 использует алиасы на v2 (обратная совместимость) — дубликаты токенов, но не стилей |
| Extends `ui/base.html` | ≈ 90 шаблонов (прямое наследование от единого master layout) |
| Используемые JS-библиотеки | DOMPurify 3.4.0 (local), Chart.js 4.4.7 (CDN, один раз) — больше ничего |
| Alpine.js / htmx / jQuery-frontend | **отсутствуют** (только jQuery в Django admin и DRF-панели) |
| Bootstrap CSS/JS | **отсутствует** — все "btn", "btn-primary" — собственные классы в `base.html` |

### Архитектурные выводы сводки

1. **Монолит `base.html` + монолит `company_detail.html`** = 12 562 LOC на двоих (34% всего шаблонного кода). Обоим противопоказан CSP strict без рефакторинга.
2. **Ноль кастомных JS-файлов** кроме `company_create.js` (29 KB) и `purify.min.js` (23 KB) в `backend/static/ui/`. Вся логика живёт **inline в HTML**.
3. **Messenger isolated** — у него свой `backend/messenger/static/messenger/` с 4 JS и 1 CSS. Всего 340 KB (operator-panel 209 KB + widget 101 KB — монструозные файлы).
4. **Tailwind-first**: 689 строк `frontend/src/main.css` компилируются в 81 KB `backend/static/ui/css/main.css`. Весь дизайн — Tailwind utility + `base.html`-inline-классы + `--v2-*`/`--v3-*` токены.
5. **v2→v3 миграция на раннем этапе**: v3 токены есть только в 10 шаблонах, из них основное использование — `company_detail_v3/b.html` (231 v2-вхождение + 24 v3) — показывает, что v3 пока просто «v2 + несколько новых `.vb-*` классов». Полной миграции нет.
6. **CSP-nonce проникает**: 89 вхождений `nonce=` в 28 шаблонах. Base.html уже объявляет `<style nonce="{{ csp_nonce }}">` для skip-link, Chart.js CDN тянется с nonce. Но десятки inline `<script>` без nonce остаются.

---

## Templates

### Master layout

#### `backend/templates/ui/base.html`
- **LOC:** 3 781 | **Role:** master layout (единственный) | **Extends:** нет (корень)
- **Blocks:** `title` (L10), `extra_head` (L878), `content` (L1262), `extra_js` (L3595)
- **Includes:** внутри base — нет `{% include %}` (все partials тянутся из наследников)
- **Tokens:** не использует ни `--v2-*`, ни `--v3-*` напрямую (определяет собственные `--brand-teal`, `--brand-orange`, `--brand-dark`, `--brand-soft`)
- **Inline `<style>`:** 2 блока, суммарно **865 LOC**.
  - L15–877: основной CSS (`.btn`, `.badge`, `.input`, `.card`, `.text-xs`-override 14px policy, notifications-widget, mail-progress-2026, sidebar, topbar, tooltip, view-as panel). Эти 862 строки стоит вынести в `static/ui/css/base.css`.
  - L885–888: skip-link (с nonce, 3 строки).
- **Inline `<script>`:** 14 блоков, суммарно **2 458 LOC**.
  - L1313 (427 LOC) — ядро notifications polling / bell badge
  - L1762 (105) — toast-система
  - L1868 (291) — SSE / WebSocket подключение к channels
  - L2189 (352) — dropdown / menu управление
  - L2542 (180) — theme switcher
  - L2723 (518) — datetime-picker кастомный (ранее был в `custom-datetime-picker.js`, вернули inline!)
  - L3242 (345) — form helpers / autosave
  - L3613 (128) — misc utilities
- **Legacy-классы:** свои `.btn btn-primary/secondary/outline`, не Bootstrap. 5 вхождений в base.html.
- **View-binding:** применяется всеми UI-views (middleware / `render()` во всех Django views внутри `ui/views/*.py`).
- **Red flag:** целевой для CSP nonce + script extraction в Wave 9. Без рефакторинга strict CSP невозможен.

#### `backend/templates/ui/_v2/v2_styles.html`
- **LOC:** 386 | **Extends:** нет (partial, только include) | **Блоки:** нет
- **Inline `<style>`:** 380 LOC — объявление **всех 32 v2-токенов** (`--v2-space-*`, `--v2-radius-*`, `--v2-border`, `--v2-text-*`, `--v2-primary/accent/danger/warn/success`, `--v2-shadow-*`) + 15 компонентов (.v2-h1/2/3, .v2-muted, .v2-card, .v2-grid, .v2-col-*, .v2-sort, .v2-bulk, .v2-chk, .v2-tr--head, .v2-fbar, .v2-fchip, .v2-fpop, .v2-input и т.д.).
- **Tokens:** 88 `var(--v2-*)`.
- **Used by:** 17 шаблонов через `{% include "ui/_v2/v2_styles.html" %}` в `extra_head`.
- **Note:** каноничный источник v2-токенов. Должен быть вынесен в compiled CSS в Wave 9.

#### `backend/templates/ui/_v2/v3_styles.html`
- **LOC:** 316 | **Role:** надстройка над v2_styles.html для Big Release 2026
- **Inline `<style>`:** 306 LOC — v3-токены (алиасы на v2) + новые компоненты (.v3-input, .v3-label, .v3-textarea, .v3-divider, .v3-skip-link, [data-v3-tooltip]).
- **Tokens:** 26 `var(--v2-*)` (для fallback) + 45 `var(--v3-*)`.
- **Used by:** 10 шаблонов через include после v2_styles.html.
- **Note:** на 50% — дубль v2-токенов через алиас `var(--v3-primary: var(--v2-primary, #01948E))`. При слиянии в один файл можно ужать до ~140 строк.

#### `backend/templates/ui/_v2/v2_modal.html`
- **LOC:** 323 | **Role:** универсальный v2-modal include (task create/edit/view)
- **Inline `<script>`:** 1 блок, **253 LOC** — JS открытия/закрытия модалок, load partial via fetch.
- **Inline `<style>`:** 15 v2-токенов на бекдроп/модал.
- **Used by:** `company_list_v2.html`, `dashboard_v2.html`, `company_detail_v3/b.html`, `task_list_v2.html`.

---

### Главные рабочие страницы

#### `backend/templates/ui/company_detail.html` (CLASSIC — текущий прод-варант)
- **LOC:** **8 781** — абсолютный рекордсмен | **Extends:** `ui/base.html`
- **Blocks:** `title`, `content` (только два).
- **Includes:** `ui/partials/company_note_attachment_card.html` (×8, с разными аргументами), `ui/partials/_task_meta.html` (×2), `ui/partials/company_detail_notes_panel_modern.html`, `ui/partials/company_detail_tasks_panel_modern.html`, `ui/_partials/_company_timeline_items.html`.
- **Tokens:** не использует v2/v3 напрямую — собственный classic-CSS (`.company-*`, legacy Tailwind utility).
- **Inline `<style>`:** 1 блок, 156 LOC (компактно).
- **Inline `<script>`:** **33 блока, 4 719 LOC** — абсолютный чемпион.
  - 5 блоков >300 LOC (L1948: 863, L5466: 668, L6176: 448, L4715: 387, L6874: 338).
  - 3 блока в диапазоне 100–250 LOC.
- **Legacy-классы:** 158 вхождений `.btn btn-*` (собственные, не Bootstrap).
- **View:** `ui.views.companies.company_detail` (classic режим, легаси до миграции на v3/b).
- **Note:** КРИТИЧЕСКИЙ CSP-нарушитель №1. Подлежит замене на `company_detail_v3/b.html` в Wave 9 / Этап 6 F4 R3 v3/b (см. `project_f4_r3_v3b_companies`).

#### `backend/templates/ui/company_detail_v3/b.html` (НОВЫЙ — v3/b)
- **LOC:** 1 812 | **Extends:** `ui/base.html`
- **Blocks:** `title`, `extra_head`, `content`, `extra_js` (все 4).
- **Includes:** `ui/_v2/v2_styles.html`, `ui/_v2/v3_styles.html`, `ui/company_detail_v3/_banner.html`, `ui/company_detail_v3/_inline_edit.html`, `ui/_v2/v2_modal.html`.
- **Tokens:** **231 `var(--v2-*)`**, **24 `var(--v3-*)`** — концентрат v3-миграции.
- **Inline `<style>`:** 569 LOC — `.vb-*` классы (variant B: hero, grid, kpi, rail, cards, accordion, popup-menu).
- **Inline `<script>`:** 1 блок, 304 LOC — popup-меню, inline-edit, contenteditable-save.
- **View:** `ui.views.companies.company_detail_v3_b`.
- **Note:** Финальный стиль F4 R3 (98 коммитов 18–19.04.2026). Этап 6 — замена classic на v3/b — ждёт одобрения user.

#### `backend/templates/ui/company_detail_v3/_inline_edit.html`
- **LOC:** 1 059 | **Role:** partial с popup-меню редактирования полей | **Extends:** нет
- **Tokens:** 46 v2 + 2 v3.
- **Inline `<script>`:** 1 блок, **921 LOC** — ядро popup-меню (классик-amoCRM-стиль: клик по полю → меню). Красный флаг №2 для CSP.
- **Inline `<style>`:** 120 LOC.

#### `backend/templates/ui/company_detail_v3/a.html` / `c.html` / `_banner.html`
- **a.html:** 433 LOC, 1 script, включает `_banner.html` — variant A (альтернативная компоновка).
- **c.html:** 424 LOC, включает `_banner.html` — variant C.
- **_banner.html:** 14 LOC — просто баннер «Вы смотрите variant X, переключиться».

#### `backend/templates/ui/company_list_v2.html`
- **LOC:** 624 | **Extends:** `ui/base.html` | **Blocks:** title/extra_head/content.
- **Includes:** `ui/_v2/v2_styles.html`, `ui/_v2/v3_styles.html`, `ui/_v2/v2_modal.html`.
- **Tokens:** 24 v2, 0 v3.
- **Inline `<script>`:** 1 блок, 259 LOC (bulk-actions, filters, sort, pagination).
- **Inline `<style>`:** 21 LOC.
- **View:** `ui.views.companies.company_list`.

#### `backend/templates/ui/task_list_v2.html`
- **LOC:** 1 012 | **Extends:** `ui/base.html`.
- **Includes:** v2+v3 styles, v2_modal.
- **Tokens:** 26 v2, 0 v3.
- **Inline `<script>`:** 2 блока, 533 LOC (фильтры задач, bulk-select, inline-edit дедлайна).
- **Inline `<style>`:** 81 LOC.
- **View:** `ui.views.tasks.task_list`.

#### `backend/templates/ui/dashboard_v2.html`
- **LOC:** 869 | **Includes:** v2/v3 styles, v2_modal.
- **Tokens:** 31 v2, 0 v3.
- **Inline `<script>`:** 1 блок, 382 LOC (виджеты дашборда, API-polling).
- **View:** `ui.views.dashboard.dashboard_v2`.

#### `backend/templates/ui/messenger_conversations_unified.html`
- **LOC:** 989 | **Extends:** `ui/base.html`.
- **External JS:** `<script src="{% static 'messenger/favicon-badge.js' %}?v=20260413a">`, `<script src="{% static 'messenger/operator-panel.js' %}?v=20260403b">` — единственный шаблон, подключающий эти файлы.
- **Tokens:** 2 v2, 1 v3.
- **Inline `<script>`:** 2 блока, 134 LOC (init, CSRF, bulk).
- **Inline `<style>`:** 558 LOC — специфичный chat-CSS (message animations, conversation cards, drag-and-drop, bulk-action-bar).
- **View:** `ui.views.messenger.conversations_unified`.

#### `backend/templates/ui/preferences.html`
- **LOC:** 1 058 | **Inline `<script>`:** 2 блока, 463 LOC (user settings, form-autosave).
- **View:** `ui.views.preferences.preferences`.

#### `backend/templates/ui/mail/campaign_detail.html`
- **LOC:** 1 159 | **Inline scripts:** 5 (три >50 LOC), total 305 LOC — campaign stats, chart rendering, real-time updates.
- **Inline styles:** 3 блока (11 LOC суммарно — мелочь).

#### `backend/templates/ui/mail/campaign_form.html`
- **LOC:** 728 | **External:** `purify.min.js` (подключается тут).
- **Inline script:** 1 блок, 409 LOC (DOMPurify-sanitize вставляемого HTML + HTML-редактор кампании).
- **Uses:** DOMPurify.

#### `backend/templates/ui/mail/admin.html`
- **LOC:** 856 | **Inline script:** 1, 72 LOC.

#### `backend/templates/ui/mail/signature.html`
- **LOC:** 578 | **Inline script:** 1, 375 LOC (signature-editor, preview).

#### `backend/templates/ui/mail/campaigns.html`
- **LOC:** 561 | **Includes:** `ui/mail/_campaign_row.html` ×2.
- **Inline scripts:** 3 (2 >50 LOC), 233 LOC.

#### `backend/templates/ui/help.html`
- **LOC:** 306 | **Includes:** v2/v3 styles.
- **Tokens:** 3 v2, 3 v3.

---

### Settings / админ-страницы (все extends `ui/base.html`)

| Шаблон | LOC | Inline script LOC (>50) | v2 | v3 | Заметка |
|--------|-----|-------------------------|----|----|---------|
| `settings/amocrm_migrate.html` | 968 | 1 блок, 193 | 0 | 0 | AmoCRM миграция, dry-run |
| `settings/users.html` | 841 | 1 блок, 377 | 0 | 0 | Пользователи, роли, фильтры |
| `settings/messenger_inbox_form.html` | 568 | 1 блок, 90 | 0 | 0 | Copy-code widget snippet |
| `settings/user_form.html` | 440 | 1 блок, 171 | 0 | 0 | Создание/редактирование юзера |
| `settings/dicts.html` | 318 | 1 блок, 146 | 0 | 0 | Справочники |
| `settings/error_log.html` | 304 | 1 блок, 107 | 0 | 0 | Логи ошибок |
| `settings/dashboard_v2.html` | 268 | 0 | 14 | 0 | Настройки дашборда |
| `settings/mail_setup.html` | 257 | 0 | 0 | 1 | Почтовые аккаунты |
| `settings/amocrm_contacts_dry_run.html` | 252 | 0 | 0 | 0 | AmoCRM contacts dry run |
| `settings/calls_stats.html` | 232 | 0 | 0 | 0 | Статистика звонков |
| `settings/announcements.html` | 175 | 0 | 0 | 0 | Объявления |
| `settings/mobile_apps.html` | 169 | 0 | 2 | 2 | Мобильные приложения |
| `settings/amocrm_debug_contacts.html` | 176 | 0 | 0 | 0 | AmoCRM debug |
| `settings/mobile_device_detail.html` | 163 | 0 | 0 | 0 | QR-спаривание устройства |
| `settings/amocrm.html` | 164 | 0 | 0 | 0 | AmoCRM основные настройки |
| `settings/calls_manager_detail.html` | 145 | 0 | 0 | 0 | Детали звонков |
| `settings/messenger_automation.html` | 128 | 0 | 0 | 0 | Авто-сценарии месенджера |
| `settings/mobile_devices.html` | 125 | 0 | 0 | 0 | Устройства |
| `settings/security.html` | 122 | 0 | 0 | 0 | Безопасность |
| `settings/mobile_overview.html` | 120 | 0 | 0 | 0 | Mobile обзор |
| `settings/messenger_analytics.html` | 120 | 0 | 0 | 0 | Инклюдит `messenger_nav.html` |
| `settings/messenger_campaigns.html` | 116 | 0 | 0 | 0 | Инклюдит `messenger_nav.html` |
| `settings/activity.html` | 107 | 0 | 0 | 0 | Активность |
| `settings/access_dashboard.html` | 106 | 0 | 0 | 0 | Access-панель |
| `settings/user_form_inline.html` | 103 | 0 | 0 | 0 | Inline user form (modal) |
| `settings/import_tasks.html` | 98 | 0 | 0 | 0 | Импорт задач |
| `settings/messenger_routing_form.html` | 96 | 0 | 0 | 0 | Роутинг |
| `settings/messenger_overview.html` | 96 | 0 | 0 | 0 | Мессенджер обзор |
| `settings/access_role.html` | 92 | 0 | 0 | 0 | Роли |
| `settings/messenger_health.html` | 91 | 0 | 0 | 0 | Health-check |
| `settings/dict_form_modal.html` | 90 | 0 | 0 | 0 | Modal-справочник |
| `settings/messenger_routing_list.html` | 89 | 0 | 0 | 0 | Роутинг список |
| `settings/dict_form.html` | 87 | 0 | 0 | 0 | Справочник форма |
| `settings/import.html` | 76 | 0 | 0 | 0 | Импорт |
| `settings/messenger_canned_list.html` | 68 | 0 | 0 | 0 | Canned-ответы |
| `settings/messenger_canned_form.html` | 56 | 0 | 0 | 0 | Canned-форма |
| `settings/messenger_source_choose.html` | 50 | 0 | 0 | 0 | Выбор источника |
| `settings/sphere_delete_modal.html` | 44 | 0 | 0 | 0 | Удаление сферы |
| `settings/company_columns.html` | 41 | 0 | 0 | 0 | Колонки списка компаний |
| `settings/branches.html` | 41 | 0 | 0 | 0 | Подразделения |
| `settings/messenger_inbox_ready.html` | 38 | 0 | 0 | 0 | Inbox готов + copy-code |
| `settings/branch_form.html` | 36 | 0 | 0 | 0 | Форма подразделения |
| `settings/messenger_nav.html` | 32 | 0 | 0 | 0 | Навигация messenger settings (включается в 7 шаблонов) |

---

### Task-шаблоны

| Шаблон | LOC | Inline script | Note |
|--------|-----|---------------|------|
| `ui/task_create.html` | 243 | 141 LOC | extends base, classic-форма |
| `ui/task_create_modal.html` | 94 | 0 | Обёртка `_v2/task_create_partial` |
| `ui/task_edit.html` | 78 | 12 LOC | Лёгкий |
| `ui/task_edit_modal.html` | 63 | 0 | Обёртка `_v2/task_edit_partial` |
| `ui/task_view_modal.html` | 305 | 52 LOC | Просмотр задачи |
| `ui/_v2/task_create_partial.html` | 375 | 189 LOC | Partial для модалки |
| `ui/_v2/task_edit_partial.html` | 133 | 13 LOC | Partial для модалки |
| `ui/_v2/task_view_partial.html` | 324 | 89 LOC | Partial для модалки |

---

### Аналитика

| Шаблон | LOC | Inline script | v2 | v3 | Note |
|--------|-----|---------------|-----|----|------|
| `ui/analytics.html` | 60 | 0 | 0 | 0 | Простой redirect/stub |
| `ui/analytics_user.html` | 271 | 0 | 0 | 0 | Аналитика одного пользователя |
| `ui/analytics_v2/manager.html` | 193 | 0 | 3 | 2 | Для роли MANAGER |
| `ui/analytics_v2/group_manager.html` | 144 | 28 LOC (Chart.js) | 0 | 2 | **Единственный подключает CDN Chart.js 4.4.7** |
| `ui/analytics_v2/branch_director.html` | 90 | 0 | 0 | 0 | Для BRANCH_DIRECTOR |
| `ui/analytics_v2/sales_head.html` | 94 | 0 | 0 | 0 | Для SALES_HEAD |
| `ui/analytics_v2/tenderist.html` | 57 | 0 | 0 | 0 | Для TENDERIST |
| `ui/analytics_v2/stub.html` | 36 | 0 | 0 | 0 | Заглушка |
| `ui/analytics_v2/_shared.html` | 78 | 0 | 3 | 2 | Общая часть (партиал) |

---

### Reports

| Шаблон | LOC | Note |
|--------|-----|------|
| `ui/reports/cold_calls_day.html` | 103 | Отчёт холодных звонков (день) |
| `ui/reports/cold_calls_month.html` | 103 | Отчёт (месяц) |

---

### Error-страницы / auth / notifications

| Шаблон | LOC | Extends | Note |
|--------|-----|---------|------|
| `404.html` | 45 | `ui/base.html` | Standard 404 |
| `500.html` | 61 | нет | Standalone (без base — страхуется от падения base) |
| `registration/login.html` | 132 | нет | Standalone login, 42 LOC inline JS |
| `registration/magic_link_error.html` | 21 | нет | Сообщение об истёкшей magic-link |
| `notifications/all_notifications.html` | 79 | `ui/base.html` | Все уведомления |
| `notifications/all_reminders.html` | 197 | `ui/base.html` | Все напоминания, 74 LOC inline JS |
| `messenger/_ui_status_badge.html` | 5 | partial | Бейдж статуса (включается из других) |

---

### Companies / Contacts auxillary

| Шаблон | LOC | Inline script | Note |
|--------|-----|---------------|------|
| `ui/company_create.html` | 331 | 1 включает `company_create.js` | Подключает внешний JS |
| `ui/company_edit.html` | 493 | 222 LOC | Редактирование (classic) |
| `ui/company_list_rows.html` | 107 | 0 | Partial-ряды (Django partial render) |
| `ui/contact_form.html` | 27 | 0 | Обёртка include |
| `ui/contact_form_modal.html` | 192 | 0 | Modal-контакт |
| `ui/_contact_form_fields.html` | 138 | 0 | Поля формы (partial) |
| `ui/partials/company_detail_notes_panel_modern.html` | 131 | 0 | Современная панель заметок |
| `ui/partials/company_detail_tasks_panel_modern.html` | 32 | 0 | Панель задач |
| `ui/partials/company_tasks_history.html` | 78 | 0 | История задач |
| `ui/partials/company_note_attachment_card.html` | 36 | 0 | Карточка вложения |
| `ui/partials/_task_meta.html` | 54 | 0 | Мета-инфо задачи |
| `ui/partials/task_type_badge.html` | 104 | 0 | Бейдж типа задачи |
| `ui/_partials/_company_timeline_items.html` | 199 | 0 | Timeline (partial) |
| `ui/_pagination.html` | 15 | 0 | Пагинатор (partial) |

### Preferences / mobile / mail / misc

| Шаблон | LOC | Inline script | Note |
|--------|-----|---------------|------|
| `ui/preferences.html` | 1 058 | 463 | Настройки пользователя (классик) |
| `ui/preferences_ui.html` | 199 | 62 | UI-preferences (шрифт, тема) |
| `ui/preferences_mail.html` | 29 | 0 | Mail-preferences (stub) |
| `ui/mobile_app.html` | 336 | 182 | Mobile-app page |
| `ui/mail/_campaign_row.html` | 72 | 0 | Partial ряда кампании |
| `ui/mail/templates.html` | 71 | 0 | Шаблоны писем |
| `ui/mail/settings.html` | 165 | 0 | Настройки рассылок |
| `ui/mail/html_preview.html` | 49 | 0 | Preview HTML-письма |
| `ui/mail/unsubscribe.html` | 44 | 0 | Отписка от рассылки |

---

## Static assets

### Активные JS

#### `backend/static/ui/company_create.js` (29 KB, 593 LOC)
- **Minified:** нет.
- **Used in:** `ui/company_create.html` (line 330).
- **External libs:** нет.
- **Dead:** NO (единственное использование, но активное).
- **Note:** автокомплит ИНН, normalizer телефонов, валидация формы создания.

#### `backend/static/ui/purify.min.js` (23 KB, 3 LOC — минифицирован)
- **Library:** DOMPurify 3.4.0 (vendored).
- **Used in:** `ui/mail/campaign_form.html` (line 263, import DOMPurify.sanitize).
- **Dead:** NO.

#### `backend/static/ui/css/main.css` (81 KB, 1 LOC — minified Tailwind build)
- **Compiled from:** `frontend/src/main.css` (689 LOC source) via `npm run build:css` (Tailwind 3.4.17).
- **Used in:** `ui/base.html` L14 (`<link rel="stylesheet" href="{% static 'ui/css/main.css' %}">`) — применяется ко всем страницам.
- **Minified:** YES.

### Активные JS для Messenger / Widget

#### `backend/messenger/static/messenger/operator-panel.js` (209 KB, 4 660 LOC)
- **Minified:** NO (абсолютный чемпион по размеру).
- **Used in:** `ui/messenger_conversations_unified.html` L949.
- **External libs:** нет (vanilla JS).
- **Dead:** NO.
- **Note:** весь operator UI (список диалогов, сообщения, typing indicator, SSE, drag-drop, bulk-actions). КАНДИДАТ НА РАСПИЛ в будущем (модульная архитектура).

#### `backend/messenger/static/messenger/widget.js` (101 KB, 2 342 LOC)
- **Minified:** NO.
- **Used in:** `ui/settings/messenger_inbox_form.html`, `ui/settings/messenger_inbox_ready.html`, `messenger/templates/messenger/widget_demo.html` (как snippet для embed на внешних сайтах).
- **External libs:** DOMPurify (inlined как `__DOMPurifyInlined`).
- **Dead:** NO (публичный виджет для встраивания на сайты клиентов).

#### `backend/messenger/static/messenger/widget-loader.js` (2.8 KB, 70 LOC)
- **Minified:** NO.
- **Used in:** snippet для `data-load-on-scroll="1"` варианта (lazy-load widget.js).
- **Dead:** NO (опциональный loader).

#### `backend/messenger/static/messenger/favicon-badge.js` (2.4 KB, 60 LOC)
- **Minified:** NO.
- **Used in:** `ui/messenger_conversations_unified.html` L948.
- **Dead:** NO.
- **Note:** добавляет бейдж непрочитанных на фавиконку.

#### `backend/messenger/static/messenger/sw-push.js` (1.8 KB, 61 LOC)
- **Minified:** NO.
- **Used in:** регистрируется через `navigator.serviceWorker.register()` в base.html или operator-panel.js (нужна уточняющая проверка).
- **Dead:** LOW SUSPICION — проверить использование.

#### `backend/messenger/static/messenger/widget.css` (27 KB, 1 205 LOC)
- **Minified:** NO.
- **Used in:** загружается widget.js программно / через `<link>` в demo-шаблоне.
- **Dead:** NO.

### Dead / orphan files

#### `backend/staticfiles/ui/custom-datetime-picker.js` (10.8 KB) — STALE ARTEFACT
- **Status:** DEAD. Оригинал в `backend/static/ui/` был удалён 2026-04-20 (commit из `current-sprint.md`: «custom-datetime-picker.js 12KB dead file удалён»).
- Остатки в `backend/staticfiles/ui/` — это коллекция `collectstatic` из предыдущего запуска. Заменится при следующем `collectstatic`.
- **Action:** удалить вручную или дождаться `collectstatic --clear` на staging.
- **Note:** Функциональность переехала **inline** в `base.html` L2723 (518 LOC) — парадоксально вернулось обратно в HTML.

### Django Admin / DRF static

- `backend/staticfiles/admin/**` — Django admin (jQuery, select2, xregexp, calendar) — **не трогать**, генерируется Django.
- `backend/staticfiles/rest_framework/**` — DRF browsable API (bootstrap.min.js, jquery-3.7.1) — **не трогать**, генерируется `rest_framework`.
- Эти vendor-файлы **не используются в пользовательском UI** CRM.

---

## RED LIST: шаблоны с inline `<script>` > 50 LOC (blocker для CSP strict-src)

Всего **20 шаблонов**, от самого тяжёлого:

| # | Шаблон | Inline scripts / блоков | Total JS LOC | Приоритет |
|---|--------|-------------------------|--------------|-----------|
| 1 | `ui/company_detail.html` | 33 | **4 719** | P0 — legacy god-template, подлежит замене на v3/b |
| 2 | `ui/base.html` | 14 | **2 458** | P0 — master layout, миграция в Wave 9 |
| 3 | `ui/company_detail_v3/_inline_edit.html` | 1 | **921** | P0 — popup-меню ядро v3/b |
| 4 | `ui/task_list_v2.html` | 2 | 533 | P1 |
| 5 | `ui/preferences.html` | 2 | 463 | P1 |
| 6 | `ui/mail/campaign_form.html` | 1 | 409 | P1 (DOMPurify-интеграция) |
| 7 | `ui/dashboard_v2.html` | 1 | 382 | P1 |
| 8 | `ui/settings/users.html` | 1 | 377 | P2 |
| 9 | `ui/mail/signature.html` | 1 | 375 | P2 |
| 10 | `ui/company_detail_v3/b.html` | 1 | 304 | P1 (variant B) |
| 11 | `ui/mail/campaign_detail.html` | 5 | 305 | P2 |
| 12 | `ui/company_list_v2.html` | 1 | 259 | P1 |
| 13 | `ui/_v2/v2_modal.html` | 1 | 253 | P1 (partial) |
| 14 | `ui/company_edit.html` | 1 | 222 | P2 |
| 15 | `ui/settings/amocrm_migrate.html` | 3 | 193 | P2 |
| 16 | `ui/_v2/task_create_partial.html` | 1 | 189 | P1 (partial) |
| 17 | `ui/mobile_app.html` | 1 | 182 | P2 |
| 18 | `ui/settings/user_form.html` | 1 | 171 | P2 |
| 19 | `ui/settings/dicts.html` | 1 | 146 | P2 |
| 20 | `ui/task_create.html` | 1 | 141 | P2 |

**Итого inline JS-объём ≥ 12 700 LOC** (только >50 LOC блоки) — нужно вынести в `backend/static/ui/` перед включением strict CSP.

---

## Dead / orphan files

| Файл | Размер | Почему dead | Действие |
|------|--------|-------------|----------|
| `backend/staticfiles/ui/custom-datetime-picker.js` | 10.8 KB | Оригинал удалён в commit 2026-04-20, остался collectstatic-артефакт | `collectstatic --clear` на staging |
| `backend/messenger/static/messenger/sw-push.js` | 1.8 KB | Не нашли явного `src=".../sw-push.js"` в шаблонах (регистрируется программно либо не используется) | проверить через `grep -r "sw-push" backend/` |

---

## Top-10 bundle sizes (активный контент)

| # | Файл | Размер | LOC |
|---|------|--------|-----|
| 1 | `backend/messenger/static/messenger/operator-panel.js` | **209 KB** | 4 660 |
| 2 | `backend/messenger/static/messenger/widget.js` | 101 KB | 2 342 |
| 3 | `backend/static/ui/css/main.css` (compiled Tailwind) | 81 KB | 1 (min) |
| 4 | `backend/static/ui/company_create.js` | 29 KB | 593 |
| 5 | `backend/messenger/static/messenger/widget.css` | 27 KB | 1 205 |
| 6 | `backend/static/ui/purify.min.js` (DOMPurify 3.4.0) | 23 KB | 3 (min) |
| 7 | `backend/messenger/static/messenger/widget-loader.js` | 2.8 KB | 70 |
| 8 | `backend/messenger/static/messenger/favicon-badge.js` | 2.4 KB | 60 |
| 9 | `backend/messenger/static/messenger/sw-push.js` | 1.8 KB | 61 |
| 10 | — | — | — |

Суммарно активный front-payload (compiled CSS + messenger + ui): **≈ 476 KB** (несжатый).

---

## Шаблоны, использующие v2-токены (Wave 9 — миграция на v3)

17 файлов, 558 вхождений `var(--v2-*)`:

| Файл | Вхождений |
|------|-----------|
| `ui/company_detail_v3/b.html` | 231 |
| `ui/_v2/v2_styles.html` | 88 (источник) |
| `ui/company_detail_v3/_inline_edit.html` | 46 |
| `ui/dashboard_v2.html` | 31 |
| `ui/task_list_v2.html` | 26 |
| `ui/_v2/v3_styles.html` | 26 (fallback-алиасы) |
| `ui/company_list_v2.html` | 24 |
| `ui/_v2/task_create_partial.html` | 21 |
| `ui/_v2/task_edit_partial.html` | 17 |
| `ui/_v2/v2_modal.html` | 15 |
| `ui/settings/dashboard_v2.html` | 14 |
| `ui/reports/cold_calls_day.html` | 4 |
| `ui/reports/cold_calls_month.html` | 4 |
| `ui/analytics_v2/manager.html` | 3 |
| `ui/analytics_v2/_shared.html` | 3 |
| `ui/help.html` | 3 |
| `ui/messenger_conversations_unified.html` | 2 |

## Шаблоны на v3-токенах (начало миграции)

10 файлов, 84 вхождения `var(--v3-*)`:

| Файл | Вхождений |
|------|-----------|
| `ui/_v2/v3_styles.html` | 45 (источник) |
| `ui/company_detail_v3/b.html` | 24 |
| `ui/help.html` | 3 |
| `ui/analytics_v2/_shared.html` | 2 |
| `ui/analytics_v2/manager.html` | 2 |
| `ui/analytics_v2/group_manager.html` | 2 |
| `ui/company_detail_v3/_inline_edit.html` | 2 |
| `ui/settings/mobile_apps.html` | 2 |
| `ui/messenger_conversations_unified.html` | 1 |
| `ui/settings/mail_setup.html` | 1 |

---

## Дубликаты кода между v2_styles.html и v3_styles.html

Из-за аккуратной политики «v3 — алиасы на v2 для обратной совместимости», **прямых дубликатов стилей нет**. Есть **дубликаты-переобъявления токенов**:

| Токен | v2 | v3 (через var-fallback) |
|-------|-----|-------------------------|
| цвета (bg/border/text/primary/accent/danger/warn/success) | 14 токенов | 14 `var(--v2-*, #...)` алиасов |
| пробелы/радиусы | 10 токенов | 10 новых токенов (`--v3-space-0..12`, `--v3-radius-xs..xl..round`) |
| shadow | 5 токенов | 3 токена (перекрытие) |
| type scale | отсутствует в v2 | 7 новых `--v3-fs-*` (14/15/16/18/22/26/32 px) |

При консолидации в Wave 9 можно:
- слить токены в `frontend/src/tokens.css` (новый файл);
- оставить только прямые значения без алиасов;
- удалить `_v2/v2_styles.html` и `_v2/v3_styles.html` как inline-блоки.
- Оценочный выигрыш: ~600 строк inline `<style>` → compiled CSS.

---

## Навигация (какой шаблон откуда тянет ресурсы)

```
ui/base.html  (master)
 ├─ <link>  static/ui/css/main.css                 [compiled Tailwind]
 ├─ <style> inline 865 LOC                          [base.css + skip-link]
 └─ <script> inline 2 458 LOC (14 блоков)           [notifications, SSE, dropdown, datetime, autosave]
    │
    └── наследуются (90 шаблонов):
        ├─ ui/company_detail.html      [+ 33 inline scripts, 4 719 LOC]
        ├─ ui/company_detail_v3/b.html [+ v2_styles + v3_styles + _banner + _inline_edit + v2_modal]
        │   ├─ include ui/_v2/v2_styles.html       [380 LOC styles, 88 v2-токенов]
        │   ├─ include ui/_v2/v3_styles.html       [306 LOC styles, 45 v3-токенов]
        │   ├─ include ui/company_detail_v3/_banner.html
        │   ├─ include ui/company_detail_v3/_inline_edit.html [921 LOC inline JS!]
        │   └─ include ui/_v2/v2_modal.html         [253 LOC inline JS]
        ├─ ui/company_list_v2.html   [+ v2_styles + v3_styles + v2_modal]
        ├─ ui/task_list_v2.html      [+ v2_styles + v3_styles + v2_modal]
        ├─ ui/dashboard_v2.html      [+ v2_styles + v3_styles + v2_modal]
        ├─ ui/messenger_conversations_unified.html
        │   ├─ <script src=".../favicon-badge.js">  [2.4 KB external]
        │   └─ <script src=".../operator-panel.js"> [209 KB external, единственный шаблон]
        ├─ ui/mail/campaign_form.html
        │   └─ <script src=".../purify.min.js">     [23 KB DOMPurify]
        ├─ ui/company_create.html
        │   └─ <script src=".../company_create.js"> [29 KB валидация ИНН]
        ├─ ui/analytics_v2/group_manager.html
        │   └─ <script src="cdn.jsdelivr/chart.js@4.4.7">  [CDN, единственное внешнее подключение]
        └─ ... (остальные settings/*)

messenger/templates/messenger/widget_demo.html  (standalone)
 └─ <script src=".../messenger/widget.js">            [101 KB публичный виджет]
```

---

## Рекомендации на Wave 9 (выжимка)

1. **Вынести inline JS** из топ-3 god-template (base.html, company_detail.html, company_detail_v3/_inline_edit.html) в `backend/static/ui/js/` — это **освободит ≈ 8 000 inline LOC** и сделает CSP strict реалистичным.
2. **Удалить `company_detail.html` (8 781 LOC)** после миграции F4 R3 Этапа 6 на `company_detail_v3/b.html`.
3. **Консолидировать v2+v3 токены** в один `frontend/src/tokens.css` + compiled Tailwind. Убрать include v2_styles + v3_styles, подключить глобально.
4. **operator-panel.js** (4 660 LOC, не минифицирован) — кандидат на распил на модули (conversation-list / composer / sidebar / bulk-actions) + esbuild production minify.
5. **widget.js** (2 342 LOC, не минифицирован) — как публичный продукт заслуживает minify + source-map (ещё будет отдаваться клиентам на их сайтах).
6. **Проверить `sw-push.js`** — возможно dead.
7. **`backend/staticfiles/ui/custom-datetime-picker.js`** — вычистить `collectstatic --clear` на staging.
8. **Подключить Chart.js 4.4.7 локально** вместо CDN (сейчас единственный CDN-ресурс, усложняет CSP connect-src).

---

_Wave 0.1 закончен. Следующий шаг — Wave 1 (анализ CSP-готовности и токенов по зонам)._
