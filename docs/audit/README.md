# Wave 0.1 — Сводный отчёт аудита

_Снапшот: **2026-04-20**. Коммит baseline: `ec67d771`._

Эта директория — **единый источник правды** для технического долга проекта.
Все последующие волны используют отсюда приоритеты и метрики.

## Что в директории

| Файл | Объём | Назначение |
|------|-------|-----------|
| [`metrics-baseline.md`](./metrics-baseline.md) | — | **СВОДКА** всех метрик (LOC, complexity, coverage) |
| [`models-inventory.md`](./models-inventory.md) | 1 267 LOC | 70 моделей по apps с полями/FK/тестами |
| [`views-inventory.md`](./views-inventory.md) | 2 237 LOC | 236 views, 83 mutating без @policy_required |
| [`celery-inventory.md`](./celery-inventory.md) | 468 LOC | 18 задач, 13 в beat, 3 red-flag |
| [`frontend-inventory.md`](./frontend-inventory.md) | 566 LOC | 112 templates, JS/CSS bundle analysis |
| [`api-inventory.md`](./api-inventory.md) | 785 LOC | 150 endpoints, 0 @extend_schema |
| [`coverage-baseline.txt`](./coverage-baseline.txt) | — | 51% baseline |
| [`coverage-baseline.xml`](./coverage-baseline.xml) | — | Для CI |
| [`coverage-baseline-html/`](./coverage-baseline-html/index.html) | — | HTML отчёт |
| [`loc-tokei.txt`](./loc-tokei.txt) | — | LOC по языкам |
| [`complexity-cc.txt`](./complexity-cc.txt) | — | CC по функциям |
| [`maintainability-mi.txt`](./maintainability-mi.txt) | — | MI по модулям |
| [`policy-coverage.txt`](./policy-coverage.txt) | — | 161 @policy_required/enforce() вхождений |
| [`test-count.txt`](./test-count.txt) | — | 1 240 test-функций |
| [`erd.png`](./erd.png) | 3.2 MB | 70 моделей, полная ER-схема |

**Всего inventory-документов**: 5 файлов × ~1000 строк = **5 323 строк текста**.

## Top-20 Tech Debt Items

Приоритет рассчитан как **(impact × frequency × risk)** по шкале 1-5.
Impact — влияние на пользователя/бизнес. Frequency — как часто трогаем этот код. Risk — вероятность регрессии.

| # | Долг | Score | Impact | Freq | Risk | Где лечится |
|---|------|-------|--------|------|------|-------------|
| 1 | **83 mutating endpoints без `@policy_required`** — нельзя перейти в ENFORCE | 125 | 5 | 5 | 5 | **W2** |
| 2 | `ui/views/company_detail.py` 2698 LOC + `ui/views/_base.py` 1700 LOC — god-files | 100 | 5 | 5 | 4 | **W1** (Phase 4-5) |
| 3 | `company_detail.html` **8781 LOC**, 33 inline `<script>` блока — блокирует CSP strict | 100 | 5 | 5 | 4 | **W9** |
| 4 | 100% API endpoints без `@extend_schema` — OpenAPI пустая | 80 | 4 | 4 | 5 | **W11** |
| 5 | `ActivityEvent` 9.5M строк, нет composite index на `(actor_id, created_at)` | 80 | 5 | 4 | 4 | **W13** |
| 6 | `audit.tasks.purge_old_activity_events` DELETE 9.5M без chunking — OOM/lock contention (P0) | 75 | 5 | 3 | 5 | **W0.6→W3** |
| 7 | 130 endpoints без throttling — DDoS-риск (включая SSE-стримы, bulk, token-refresh) | 75 | 5 | 5 | 3 | **W2** |
| 8 | 56 мест с `enforce()` в теле функции вместо `@policy_required` декоратора — скрытая логика | 64 | 4 | 4 | 4 | **W2** |
| 9 | `/api/...` и `/api/v1/...` дублируют 70+ endpoint — ложное версионирование | 60 | 4 | 3 | 5 | **W11** |
| 10 | `operator-panel.js` 209 KB **не минифицирован** (prod serve) | 48 | 4 | 3 | 4 | **W10/W13** |
| 11 | Denormalized `Company.phone/email/contact_name/position` — нарушают CompanyPhone/Contact | 48 | 3 | 4 | 4 | **W1** (миграция) |
| 12 | 35 моделей без `verbose_name` → плохой UX в Django admin | 45 | 3 | 5 | 3 | **W9** (мелкий PR) |
| 13 | 10 моделей без прямых тестов (Channel, AutomationRule, Macro, PushSubscription...) | 45 | 3 | 3 | 5 | **W5** (chat) + **W3** |
| 14 | `mailer.Campaign` vs `messenger.Campaign` — коллизия имён, одна путаница в импорте | 40 | 3 | 4 | 4+ | **W6** (переименование) |
| 15 | `companies.Contact` vs `messenger.Contact` — та же проблема | 40 | 3 | 4 | 4 | **W5/W6** |
| 16 | `widget.js` 101 KB **не минифицирован** на паблик-виджете | 36 | 4 | 3 | 3 | **W10** (tooling) |
| 17 | 5 singleton-моделей через `load()` без DB-constraint `pk=1` — риск дубликатов в БД | 36 | 3 | 3 | 4 | **W3** (add CHECK constraint) |
| 18 | `Messenger ViewSets` (Campaign/AutomationRule/Macro/Push/Label) **без PolicyPermission** | 36 | 4 | 3 | 3 | **W2** |
| 19 | `legacy amocrm/` app — 800+ LOC, не используется после перехода на внутренний CRM | 32 | 2 | 2 | 4 | **W1** (удаление) |
| 20 | 29 god-views >200 LOC: settings_messenger_inbox_edit (434), campaigns (362), task_list (356) | 30 | 3 | 3 | 3 | **W1/W9** постепенно |

## Блокирующие предусловия (ждут W0.2-W0.6)

- **W0.2** — pre-commit + mypy + bandit (до того как W1 начнётся — новый код должен проходить gating).
- **W0.3** — feature flags (для postepennoi выкатки в W1, W5, W9).
- **W0.4** — **GlitchTip + structlog** (прежде чем коснёмся security в W2, Sentry уже должен работать).
- **W0.5** — factory_boy + pytest-playwright (массовая генерация тестов в W2-W14 полагается на фикстуры).
- **W0.6** — `pg_stat_statements` + `k6` baseline (до Wave 13 perf — замеры чтобы сравнивать).

## Траектория волн (из tech-debt table)

```
W1 (refactor):   #2, #11, #19, часть #20
W2 (security):   #1, #7, #8, #18 — главный приоритет
W3 (core CRM):   #6, #13, #17
W5 (chat):       #13, #15
W6 (email):      #14
W9 (UX):         #3, #12, #20
W10 (infra):     #10, #16 (tooling)
W11 (API):       #4, #9
W13 (perf):      #5
```

## Ссылки на предыдущие артефакты

- `graphify-out/GRAPH_REPORT.md` — граф знаний (5281 узлов, 227 сообществ, 20558 рёбер)
- `docs/decisions.md` — все ADR
- `docs/problems-solved.md` — решённые проблемы
- `docs/plan/00_MASTER_PLAN.md` — план всех 15 волн
- `docs/plan/01_wave_0_audit.md` — промпт текущей волны

---

**Что дальше**: Wave 0.2 (tooling baseline). В конце W0 → старт Wave 1.
