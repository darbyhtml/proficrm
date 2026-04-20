# Metrics baseline — Wave 0.1

_Снапшот: **2026-04-20 17:45 MSK** (коммит `ec67d771` + W0.1)._

## Сводные цифры

| Метрика | Значение | Комментарий |
|---------|----------|-------------|
| **Всего файлов в `backend/`** | 474 | tokei (без `migrations/`, `staticfiles/`) |
| **Всего строк (с пустыми + comments)** | 116 081 | tokei |
| **Python code (SLOC, без пустых)** | 65 960 | |
| **HTML (templates, code lines)** | 22 240 | 112 `.html` templates |
| **JavaScript (inline + static)** | 13 845 | **33 inline `<script>` блоков в одном `company_detail.html` (8 781 LOC)** |
| **CSS** | 4 085 | compiled Tailwind + widget.css |
| **SVG** | 30 | 2 файла |
| **Markdown (docs)** | 3 114 | 12 файлов |

## Метрики качества кода

| Метрика | Значение | Толкование |
|---------|----------|------------|
| **Cyclomatic complexity (avg)** | **A (4.20)** | Rank A (0-5) = отлично. Большинство функций простые |
| **Анализировано блоков** | 3 347 | классы + функции + методы |
| **Maintainability index (sample)** | A (52-100) | Большинство модулей A. Единичные отмечены в W0.1 |
| **Coverage baseline** | **51 %** | fail_under = 50 (запас -2%). Траектория +5%/волна → 85 к W14 |
| **Всего `def test_*`** | 1 240 | не тест-раннов (1179), а тест-функций |

## Полные отчёты

- [`loc-tokei.txt`](./loc-tokei.txt) — детальный breakdown по языкам
- [`complexity-cc.txt`](./complexity-cc.txt) — cyclomatic complexity по всем функциям
- [`maintainability-mi.txt`](./maintainability-mi.txt) — MI по модулям
- [`coverage-baseline.txt`](./coverage-baseline.txt) — покрытие по файлам
- [`coverage-baseline.xml`](./coverage-baseline.xml) — для CI coverage-trend
- [`coverage-baseline-html/`](./coverage-baseline-html/index.html) — интерактивный отчёт
- [`policy-coverage.txt`](./policy-coverage.txt) — 161 раз `@policy_required`/`enforce(...)` в коде (90 + 71)
- [`erd.png`](./erd.png) — ER-диаграмма (3.2 MB, 70 моделей)
- [`test-count.txt`](./test-count.txt) — 1 240 test-функций

## Структура по app (tokei Python)

```
backend/ui         — 13 000+ LOC (god-app, split в W1)
backend/companies  — 10 500+ LOC (16 моделей + services package)
backend/messenger  — 9 000+ LOC (16 моделей + WebSocket/SSE)
backend/mailer     — 5 500+ LOC (11 моделей + Fernet + SMTP)
backend/accounts   — 4 100+ LOC (4 модели + policy engine integration)
backend/tasksapp   — 2 000+ LOC (4 модели + RRULE)
backend/phonebridge — 1 800+ LOC (6 моделей + FCM)
backend/audit      — 1 500+ LOC (2 модели + retention)
backend/policy     — 1 200+ LOC (2 модели + engine)
backend/notifications — 1 000+ LOC (4 модели)
backend/amocrm     — 800+ LOC (legacy, удалить в W1)
backend/crm        — 600+ LOC (settings, urls)
backend/core       — 500+ LOC (crypto, feature flags, request_id)
```

## Траектория gate-coverage

| Веха | Стартовое требование | Обоснование |
|------|----------------------|-------------|
| W0 (сейчас) | **≥ 50 %** | baseline 51% минус запас 2% округлён вниз |
| W1 | ≥ 55 % | основной код хорошо типизированный после рефактора |
| W2 | ≥ 60 % | policy tests добавят покрытие permission-логики |
| W3 | ≥ 65 % | core CRM hardening приносит +5% серверных тестов |
| W4 | ≥ 70 % | notifications/tasks — легко тестируемы |
| W5 | ≥ 72 % | chat — WebSocket тесты сложнее |
| W6 | ≥ 75 % | email — часть теста через webhook/IMAP |
| W7 | ≥ 77 % | phonebridge |
| W8 | ≥ 78 % | analytics — чистая data-layer |
| W9 | ≥ 78 % | UX — снапшот-тесты |
| W10 | ≥ 78 % | infra ≠ app tests |
| W11 | ≥ 80 % | API split + contract tests |
| W12 | ≥ 82 % | integrations |
| W13 | ≥ 83 % | perf tests не добавляют coverage существенно |
| W14 | ≥ **85 %** | финальная цель |
