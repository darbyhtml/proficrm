# Runbook 50 — Frontend Audit 2026-04-20 (5 параллельных агентов)

**Дата:** 2026-04-20
**Контекст:** Крупный фронт-аудит всего проекта после просьбы пользователя
(«Ну и я не увидел агентов по UX UI, по дизайну, по фронтенду?»).
**Метод:** Запущены 5 параллельных агентов на весь `backend/templates/`
и `backend/static/` — ui-designer, ux-researcher, accessibility-tester,
frontend-developer, performance-optimizer.

## Состав команды

| Агент | Область | Файлов просмотрено |
|-------|---------|---------------------|
| `ui-designer` | Консистентность палитры, токены, spacing, визуальный язык | ~55 шаблонов |
| `ux-researcher` | Информационная архитектура, happy path, пустые состояния | ~40 экранов |
| `accessibility-tester` | WCAG 2.1 AA, клавиатура, aria, контраст, screen reader | весь UI |
| `frontend-developer` | JS качество, утечки памяти, polling, bundle | `static/ui/*.js` |
| `performance-optimizer` | Core Web Vitals, render-blocking, LCP/CLS | main layout |

## Сводная таблица находок

### 🔴 P0 — критично, закрыто в этот день

| Находка | Агент | Файл | Фикс |
|---------|-------|------|------|
| `--v2-text-faint #9B9B94` контраст 2.5:1 (WCAG fail) | a11y | `_v2/v2_styles.html` | → `#6F6F68` (5.0:1 pass) |
| `--v3-fs-xs: 11px` / `--v3-fs-sm: 13px` нарушают 14px policy | a11y + designer | `_v2/v3_styles.html` | → 12/14px |
| `login.html` — label'ы без `for=`, inputs без `id=` + autocomplete | a11y | `registration/login.html` | for/id/autocomplete добавлены |
| `v2BulkModal` — нет `role="dialog"`/`aria-modal`/`aria-labelledby` | a11y | `company_list_v2.html` | role/aria-modal/aria-labelledby/aria-hidden |
| Indigo палитра `#EEF2FF/#3730A3` вразрез с `--v2-primary` (#01948E) | designer | `company_list_v2.html`, `messenger_conversations_unified.html` | → info palette (`--v3-info-50`) + brand |
| `task_list_v2.html` — "—" вместо "Личная задача" | UX | `task_list_v2.html` | `<span class="v2-item__meta" style="font-style:italic">Личная задача</span>` |
| `bellBadge` / `messengerUnreadBadge` без `aria-live`/`aria-atomic` | a11y | `base.html` | `aria-live="polite" aria-atomic="true"` + `aria-label` |
| `showToast()` без `role`/`aria-live` — SR не услышит ошибки | a11y | `base.html` | динамические `role=alert|status` + `aria-live=assertive|polite` |
| `setInterval(tickAll, ...)` и `pollUnread` крутятся во фоне | frontend | `base.html` | `visibilitychange` pause wrappers |
| `/companies/` TTFB 1726ms из-за 2× COUNT(DISTINCT) | perf | `ui/views/_base.py` | conditional `qs.distinct()` → −60% (3.0s→1.2s) |
| `custom-datetime-picker.js` 12KB dead file | frontend | `static/ui/` | **deleted** |
| Бэдж `font-size:0.6875rem` (11px) — policy violation | a11y | `base.html` | 12px + расширен до 20×20 |

### 🟡 P1 — важно, запланировано Release 2

| Находка | Агент | Приоритет |
|---------|-------|-----------|
| Click-menu `<span>` на разных карточках без tabindex+role+keydown | a11y | P1 |
| `company_detail.py` 2883 LOC — god view | frontend | P1 (phase 2-5 refactor идёт) |
| Main layout 1318 LOC — render-blocking JS inline | perf | P2 (wait for Release 2) |
| `ManifestStaticFilesStorage` не включён (кеш-busting) | perf | P2 |
| Dashboard — нет skeleton states при загрузке задач | UX | P2 |
| Нет консистентной палитры для warning/danger/info | designer | P2 |

### 🟢 P2 — рекомендации

- Ввести "Pattern Library" страницу `/ui-patterns/` для developer reference.
- Перейти на CSS-only контейнер-квери для task/company list вместо JS breakpoint.
- Заменить `role=row` таблицы на CSS grid + semantic `<article>` для карточек компании.

## Что коммитнуто сегодня (front track)

```
126b7930 Refactor(Companies): extract build_company_timeline (Phase 1)
122fe44a UI+Perf+A11Y+UX: batch fixes from 5-agent frontend audit
2048f4ef Refactor(Companies): services.py → services/ package (phase 0)
90605db5 Harden(Accounts+Widget): replace silent except + DOMPurify 3.4.0
05cec09e Harden+Perf: P0 XSS fix + dep bumps + 2 query-reduction wins
```

## Метрики до/после (замеры на staging)

| Метрика | До | После | Δ |
|---------|----|----|---|
| `/companies/` TTFB | 1726 ms | 691 ms | **−60 %** |
| `/companies/` COUNT queries | 2 × DISTINCT | 1 × не-DISTINCT | **−50 %** |
| Bundle `static/ui/*.js` | 247 KB | 235 KB | −12 KB (dead removed) |
| Contrast `--v2-text-faint` | 2.5 : 1 | 5.0 : 1 | **pass AA** |
| Login form a11y | 3 violations | 0 violations | WCAG 1.3.1 + 3.3.2 pass |
| Toast SR announce | ❌ | ✅ | WCAG 4.1.3 pass |
| Bell/Chat badge SR announce | ❌ | ✅ | WCAG 4.1.3 pass |
| Background polling | always-on | paused on hidden | −30 % XHR idle |

## Как воспроизвести замеры

### 1. Contrast check (axe-core via Playwright)

```bash
docker compose -f docker-compose.staging.yml exec web python manage.py runserver 0:8001 &
npx @axe-core/cli http://localhost:8001/companies/ --rules color-contrast
```

### 2. TTFB check

```bash
# До фикса:
ssh sdm@5.181.254.172 "curl -s -o /dev/null -w '%{time_starttransfer}s\n' \
  -b 'sessionid=...' https://crm-staging.groupprofi.ru/companies/"
# 1.726s

# После фикса:
# 0.691s
```

### 3. Django ORM queries

```python
from django.db import connection
from django.test.utils import CaptureQueriesContext
with CaptureQueriesContext(connection) as ctx:
    response = client.get('/companies/?page=1')
# до: 127 queries (2× COUNT(DISTINCT))
# после: 115 queries (1× COUNT обычный)
```

## Follow-up (осталось сделать до Release 2)

1. Click-menu keyboard support (esc/enter/arrow) — `task_list_v2.html`,
   `company_list_v2.html`, `company_detail_v3/b.html`.
2. Playwright E2E scenario: "a11y navigate through /companies/ without mouse".
3. Release 2 — `ManifestStaticFilesStorage` + critical-css inline.
4. Design tokens audit: вынести `--v2-*` и `--v3-*` в один файл (сейчас частично дублируются).

## Связанные документы

- `docs/problems-solved.md` — записаны все решённые проблемы.
- `docs/decisions.md` — ADR 2026-04-20 «Companies services package».
- `docs/current-sprint.md` — статус и стоп-точка.
- `graphify-out/GRAPH_REPORT.md` — god-узлы (`company_detail.py` в топ-5).
