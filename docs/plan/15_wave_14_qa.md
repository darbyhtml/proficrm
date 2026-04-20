# Волна 14. Final QA — «чтобы каждую буковку протестировал»

**Цель волны:** ИСКЛЮЧИТЬ мануальное тестирование менеджерами. Всё, что можно проверить программой — проверяем программой. Каждая роль, каждая модалка, каждая форма, каждая ссылка, каждая ошибка — покрыты автоматически.

**Это самая важная волна.** Не торопись, не экономь. Результат — уверенность в релизе.

**Параллелизация:** высокая. 10 этапов, многие независимы. Выделить 3 параллельных потока — вполне реально.

**Длительность:** 15–20 рабочих дней.

**Требования:** Все предыдущие волны (W0–W13) завершены.

---

## Этап 14.1. User journey matrix

### Контекст
Без матрицы — тестируем ad-hoc. Матрица даёт гарантию, что ни одно сочетание роль×ресурс×действие не осталось без проверки.

### Цель
Полная матрица user journey с автоматическими Playwright тестами.

### Что делать
1. **Матрица**:
   - **8 ролей** × **15 ресурсов** × **6 главных действий** = 720 cells.
   - Не все cells применимы (MANAGER не может CREATE PolicyRule).
   - Matrix в YAML: `tests/e2e/matrix/user-journey.yaml`.

2. **Journey generator**:
   - Скрипт, который читает YAML и генерирует Playwright test files.
   - Naming: `test_<role>_<resource>_<action>.spec.ts`.

3. **Test fixtures**:
   - Seed data: companies, deals, tasks, users, conversations.
   - Reset between tests (DB snapshot rollback или factory_boy recreate).
   - Auth per-role helper.

4. **Common checks per journey**:
   - Navigate to URL.
   - Assert expected content present.
   - Assert forbidden content absent.
   - Interact with element.
   - Assert result.
   - Check for console errors (browser + server).

5. **Coverage map**:
   - После каждого test run — report: какие cells pass/fail/not-implemented.

### Инструменты
- `mcp__playwright__*`
- `Agent` tool для параллельной генерации test files

### Definition of Done
- [ ] Матрица полная, 720 cells оценены
- [ ] Минимум 150 Playwright tests сгенерированы (критичные cells)
- [ ] Все tests зелёные
- [ ] Coverage report показывает progress

### Артефакты
- `tests/e2e/matrix/user-journey.yaml`
- `tests/e2e/fixtures/*.py`
- `tests/e2e/roles/*.spec.ts` (150+ files)
- `tests/e2e/reports/coverage.json`
- `scripts/generate-journey-tests.py`

### Валидация
```bash
playwright test tests/e2e/roles/ --workers=4
python scripts/coverage-report.py
```

### Откат
Tests only — безопасно.

### Обновить в документации
- `docs/testing/user-journey-matrix.md`

---

## Этап 14.2. Critical user flows E2E

### Контекст
Матрица проверяет permission-correctness. Но есть «золотые» flow, которые идут через несколько страниц.

### Цель
Полное покрытие критичных end-to-end flows.

### Что делать
Напиши E2E для каждого flow:

1. **Flow 1: Новый клиент → первая сделка**:
   - Menu: Widget on site загружает
   - Client fills form → opts in
   - Conversation created → routed to operator
   - Operator responds, gets phone, closes with success
   - Contact created, Company auto-linked (или manually)
   - Manager views Company card
   - Creates Deal → stage 1
   - Adds task «перезвонить через 3 дня»
   - Marks task done → moves deal to stage 2
   - Sends email via Campaign (single recipient)
   - Updates deal stage → won, fills amount + date
   - Checks Activity feed — все события есть
   - Checks Analytics — metrics обновились

2. **Flow 2: Manager работает полный день**:
   - Login → 2FA
   - Dashboard: видит задачи на сегодня
   - Click on task → company card
   - Нажимает «позвонить» → FCM mock → event back
   - Pishet email
   - Creates note
   - Moves deal
   - Закрывает задачу
   - Logout

3. **Flow 3: Admin управляет системой**:
   - Login → 2FA (mandatory for admin)
   - Updates PolicyRule
   - Adds new user (MANAGER role)
   - User receives magic link (test inbox)
   - New user logs in, sees correct scope
   - Admin assigns CompanyDeletionRequest to approve → executes
   - Admin views audit log — видит действие

4. **Flow 4: Campaign life-cycle**:
   - Select segment (100 contacts)
   - Create campaign with template
   - Preview, test-send
   - Schedule for tomorrow
   - Tomorrow at scheduled time — фактически отправляется
   - Opens come (mock tracking pixel hits)
   - Clicks come (mock redirect)
   - Unsubscribes come
   - Analytics dashboard обновился
   - Next campaign — excluded unsubscribed

5. **Flow 5: Data import**:
   - Upload XLSX with 500 contacts
   - Mapping UI: выбрать колонки
   - Preview
   - Confirm
   - Async import through Celery
   - Errors report downloadable
   - Companies created without duplicates

6. **Flow 6: Incoming call → overlay → notes**:
   - Simulation incoming call from Android app (mock)
   - CRM creates CallRequest in log
   - Manager opens Company card
   - Adds note about call
   - Creates follow-up task

7. **Flow 7: Data export by client request (152-ФЗ)**:
   - Client requests data export
   - ADMIN triggers export → async → email with attachment
   - Archive contains всё: chat history, emails, calls, deals, notes
   - Client requests delete → ADMIN approves → all data gone (with audit)

8. **Flow 8: Security breach response**:
   - Simulate: user reports phishing
   - ADMIN revokes user session
   - ADMIN rotates JWT secret (script)
   - All users forced to re-login
   - Audit log shows the incident

### Инструменты
- `mcp__playwright__*`

### Definition of Done
- [ ] 8 critical flows covered end-to-end
- [ ] Each flow зелёный в isolation
- [ ] Flows run in CI, fail build if red
- [ ] Video recording for failures (Playwright trace)

### Артефакты
- `tests/e2e/flows/*.spec.ts` (8 files)
- `tests/e2e/helpers/*.ts`

### Валидация
```bash
playwright test tests/e2e/flows/ --workers=1 --retries=2
```

### Откат
Tests only.

### Обновить в документации
- `docs/testing/critical-flows.md`

---

## Этап 14.3. Visual regression

### Контекст
После всех стилевых изменений (Wave 9) — нужно убедиться, что ничто не сломалось визуально и future изменения не внесут неожиданной регрессии.

### Цель
Baseline screenshots всех экранов, alerts на diff.

### Что делать
1. **Tool**:
   - Playwright built-in `expect(page).toHaveScreenshot()`.
   - Или Chromatic / Percy — если готовы платить.

2. **Snapshots**:
   - Все main screens × 5 viewports × light/dark = ~150 screenshots.
   - Baseline в `tests/visual/__screenshots__/`.
   - On change > 0.1% pixels — fail, developer review.

3. **Component-level**:
   - Design system gallery — snapshot each component state.
   - Button hovers / focus / disabled.
   - Form states.
   - Modals.

4. **Stable rendering**:
   - Freeze time (`page.clock.setFixedTime`).
   - Mock dynamic data (random values, CSS animations disabled).
   - Disabled network animations.

5. **Review workflow**:
   - PR с visual change — screenshots attached as diff.
   - Manual approve.

### Инструменты
- `mcp__playwright__*`

### Definition of Done
- [ ] 150+ screenshots in baseline
- [ ] CI runs visual tests
- [ ] Diff review workflow documented
- [ ] No flaky tests

### Артефакты
- `tests/visual/*.spec.ts`
- `tests/visual/__screenshots__/`
- `docs/testing/visual-regression.md`

### Валидация
```bash
playwright test tests/visual/ --update-snapshots  # initial
playwright test tests/visual/  # second run — must pass
```

### Откат
Update snapshots if legitimate change.

### Обновить в документации
- `docs/testing/visual-regression.md`

---

## Этап 14.4. Accessibility automated testing

### Контекст
В Wave 9.7 установили WCAG AA. Теперь — continuous checking.

### Цель
axe-core в каждом E2E test. Zero critical violations на prod.

### Что делать
1. **Integration**:
   - `@axe-core/playwright` в каждой фикстуре.
   - После каждого page load — `await expect(page).toPassAxe()`.
   - Custom rules (опционально) для специфичных нужд.

2. **Coverage**:
   - Все main screens.
   - Все forms.
   - All modal open states.

3. **Severity levels**:
   - Critical: fail build.
   - Serious: fail build.
   - Moderate: warn (track as tech debt).
   - Minor: ignore (обычно).

4. **Manual supplement**:
   - Screen reader check (NVDA) в sprint reviews.
   - Keyboard-only navigation audit.

### Definition of Done
- [ ] axe-core в 50+ E2E tests
- [ ] 0 critical violations
- [ ] 0 serious violations
- [ ] Report generated per run

### Артефакты
- `tests/a11y/*.spec.ts`
- `tests/a11y/axe-config.ts`
- `docs/testing/a11y.md`

### Валидация
```bash
playwright test tests/a11y/
```

### Откат
N/A.

### Обновить в документации
- `docs/testing/a11y.md`

---

## Этап 14.5. Security testing: OWASP ZAP + Bandit + Semgrep

### Контекст
Static analysis уже есть (Wave 0.2). Нужны dynamic tests.

### Цель
OWASP ZAP baseline + active scan на staging.

### Что делать
1. **ZAP baseline scan** в CI:
   - Docker: `owasp/zap2docker-stable zap-baseline.py -t https://staging.url`.
   - Config: exclude health check endpoint.
   - Report: HTML + JSON.
   - Fail build on high-severity.

2. **ZAP active scan** (раз в неделю на staging):
   - `zap-full-scan.py`.
   - More invasive — not in CI but scheduled.
   - Runner: отдельный GitHub Actions с schedule.

3. **Bandit + Semgrep** (Wave 0.2 уже есть — перепроверить что актуально).

4. **Dependency audit**:
   - `pip-audit`, `npm audit`.
   - Dependabot alerts on GitHub.

5. **Secrets scan**:
   - gitleaks (уже есть).
   - TruffleHog как backup.

6. **Penetration test checklist** (manual, раз в полгода):
   - SQL injection через weird inputs.
   - Auth bypass attempts.
   - Rate limit bypass.
   - Session fixation.
   - CSRF.
   - Privilege escalation (manager → admin).

### Инструменты
- OWASP ZAP Docker
- bandit, semgrep, pip-audit, gitleaks

### Definition of Done
- [ ] ZAP baseline в CI, passing
- [ ] ZAP active scan scheduled
- [ ] Static analysis clean
- [ ] No known CVE в deps
- [ ] No secrets в repo
- [ ] Penetration test runbook

### Артефакты
- `.github/workflows/security-scan.yml`
- `docs/security/zap-config.yaml`
- `docs/security/penetration-test-runbook.md`

### Валидация
```bash
# CI: зелёный
docker run -v $(pwd)/zap:/zap/wrk/:rw \
  owasp/zap2docker-stable zap-baseline.py -t https://staging.url
```

### Откат
N/A (tests don't affect prod).

### Обновить в документации
- `docs/security/testing.md`

---

## Этап 14.6. Load testing: k6

### Контекст
50 менеджеров одновременно? 100 RPS? Нужно убедиться, что выдерживаем.

### Цель
Documented capacity. p95 < 500ms при 100 RPS на главных endpoint'ах.

### Что делать
1. **Scenarios** (`tests/load/*.js`):
   - **Normal day**: 50 VU за 30 мин, симулируют типичный рабочий день.
   - **Peak hour**: 100 VU за 1 час (утром).
   - **Spike**: 500 VU за 5 мин (маркетинговая акция).
   - **Endurance**: 50 VU за 4 часа (memory leaks?).

2. **Targets**:
   - `/` (dashboard)
   - `/companies/` list
   - `/company/<id>/` detail
   - `/api/v1/internal/companies/`
   - WebSocket chat (20 одновременных).
   - Email campaign send (100 recipients в minute).

3. **Metrics to watch**:
   - Response times (p50, p95, p99).
   - Error rate.
   - Throughput (RPS).
   - Server: CPU, RAM, DB connections, Redis memory.

4. **Performance gates**:
   - p95 < 500ms on main endpoints.
   - p99 < 1500ms.
   - Error rate < 0.1%.
   - No deadlocks в Postgres.

5. **Automation**:
   - k6 + Prometheus remote-write → Grafana dashboard.
   - CI: smoke test на PR (5 VU, 30 сек). Full — manual or scheduled.

### Инструменты
- k6, Grafana

### Definition of Done
- [ ] 4 scenarios написаны
- [ ] Baseline capacity документирован
- [ ] Performance gates passing
- [ ] Dashboard в Grafana для runs

### Артефакты
- `tests/load/scenarios/*.js`
- `tests/load/README.md`
- `docs/ops/capacity-planning.md`

### Валидация
```bash
k6 run tests/load/scenarios/normal-day.js
# Grafana: check metrics
```

### Откат
N/A.

### Обновить в документации
- `docs/ops/capacity-planning.md`

---

## Этап 14.7. Chaos testing (basic)

### Контекст
Полноценный Chaos Engineering — overkill. Но базовые chaos сценарии для уверенности в recovery — нужны.

### Цель
Убедиться, что система handles gracefully типичные сбои.

### Что делать
1. **Scenarios**:
   - **Postgres restart** during peak load (on staging).
     - Expected: приложение ретрает; некоторые запросы могут fail; через 30с — recovered.
   - **Redis restart**.
     - Expected: sessions потеряны (acceptable); Celery задачи re-queued; чаты дисконнектятся и реконнектятся.
   - **Celery worker kill**.
     - Expected: tasks picked up by other workers.
   - **Network partition** (iptables drop 50% на 1 мин).
     - Expected: graceful degradation, circuit breakers работают.
   - **Disk full** на /var/log.
     - Expected: alert, не падает app (logs drop).
   - **High memory** (simulate load).
     - Expected: OOM-killer hit right process (monit), restart.

2. **Runbooks**:
   - Each scenario → expected behavior + actual observed → runbook.

3. **Drill schedule**:
   - Quarterly на staging.
   - Never на prod (пока нет команды, только solo admin).

### Инструменты
- `tc` (traffic control), `stress-ng`, `iptables`

### Definition of Done
- [ ] 6 scenarios проведены на staging
- [ ] Для каждого — behavior документирован
- [ ] Выявленные проблемы исправлены

### Артефакты
- `scripts/chaos/*.sh`
- `docs/chaos/scenarios.md`

### Валидация
Manual runs с observation.

### Откат
Сценарии только на staging.

### Обновить в документации
- `docs/chaos/scenarios.md`

---

## Этап 14.8. Mutation testing (опционально)

### Контекст
Tests есть, но покрывают ли они реальные баги?

### Цель
Убедиться, что тесты ловят регрессии, а не только формально покрывают строки.

### Что делать
1. `mutmut` на критичных модулях: policy, services, billing-like.
2. Run (долго — часы).
3. Mutants killed % — целевой ≥ 80%.
4. Surviving mutants — анализ: (a) добавить тест, (b) accept, если mutant безобидный.
5. CI — не обязательно (слишком долго), но scheduled weekly.

### Definition of Done
- [ ] mutmut запущен на critical modules
- [ ] Killed % ≥ 80% на policy engine
- [ ] Report в docs

### Артефакты
- `docs/testing/mutation-report.md`

### Валидация
```bash
mutmut run --paths-to-mutate=backend/policy
mutmut results
```

### Откат
N/A.

### Обновить в документации
- `docs/testing/mutation.md`

---

## Этап 14.9. UAT scripts для ручной валидации менеджерами

### Контекст
Несмотря на автоматизацию — перед большим релизом нужна ручная проверка менеджерами. Нужны скрипты, по которым они идут, фиксируют проблемы.

### Цель
Подготовить UAT-скрипты для 3 филиалов, провести, собрать фидбэк.

### Что делать
1. **Scripts**:
   - 10-15 user-level задач для MANAGER («Создайте новую компанию, добавьте контакт, позвоните, зафиксируйте разговор»).
   - 5-8 для SALES_HEAD («Посмотрите выручку филиала за месяц, сравните с прошлым, экспортируйте отчёт»).
   - 3-5 для ADMIN («Добавьте нового менеджера, настройте ему роль, сбросьте 2FA»).

2. **Feedback form**:
   - Google Forms / Яндекс Forms с полями: Task ID, Status (pass/fail/partially), Issue description, Screenshot.

3. **Period**:
   - 2 недели parallel с существующей системой.
   - Каждую проблему трекаем в GitHub Issues, приоритезируем, фиксим.

4. **Sign-off**:
   - После 2 недель + all critical issues fixed — формальный sign-off от SALES_HEAD каждого филиала.

### Definition of Done
- [ ] UAT scripts готовы
- [ ] Проведён с 3+ менеджерами в каждом филиале
- [ ] Все critical / high — исправлены
- [ ] Sign-off получен

### Артефакты
- `docs/uat/scripts/manager.md`
- `docs/uat/scripts/sales-head.md`
- `docs/uat/scripts/admin.md`
- `docs/uat/feedback/YYYY-MM-DD/*.md`

### Валидация
Human sign-off.

### Откат
N/A.

### Обновить в документации
- `docs/uat/README.md`

---

## Этап 14.10. Final QA sign-off

### Контекст
Всё ли проверено? Нужен формальный gate.

### Цель
Go/no-go решение для production release.

### Что делать
1. **Checklist**:
   - [ ] All автоматические tests зелёные (15000+ tests? 500 E2E?)
   - [ ] Все 15 global DoD criteria (из 00_MASTER_PLAN.md) выполнены
   - [ ] Visual regression baseline зафиксирован
   - [ ] Accessibility: zero critical violations
   - [ ] Security: ZAP baseline clean
   - [ ] Load testing: passes gates
   - [ ] Chaos testing: scenarios passed
   - [ ] UAT sign-off от SALES_HEAD × 3
   - [ ] DR runbooks проверены
   - [ ] Backup validation drill passing

2. **Report**:
   - Executive summary «CRM готова к production».
   - Known issues (accepted risks).
   - Recommendations для первого месяца prod.

3. **Go-live plan**:
   - Feature flag `production_ready=true`.
   - Soft launch: один филиал (Екатеринбург) первым.
   - 2 недели — мониторинг.
   - Далее — остальные филиалы.

4. **Hypercare period**:
   - 30 дней после релиза: daily Sentry review, быстрый фикс issues.
   - Увеличенная support availability.

### Definition of Done
- [ ] Checklist passed
- [ ] Report подписан
- [ ] Go-live plan согласован
- [ ] Hypercare schedule на календаре

### Артефакты
- `docs/release/qa-signoff-report.md`
- `docs/release/go-live-plan.md`
- `docs/release/hypercare-schedule.md`

### Валидация
Human sign-off.

### Откат
N/A.

### Обновить в документации
- `docs/release/`

---

## Checklist завершения волны 14

- [ ] User journey matrix: 720 cells оценены, 150+ автоматизированы
- [ ] Critical flows: 8 E2E passing
- [ ] Visual regression: 150+ snapshots
- [ ] A11y: 0 critical
- [ ] Security: ZAP + Bandit + Semgrep clean
- [ ] Load: passes gates
- [ ] Chaos: scenarios passed
- [ ] UAT: sign-off
- [ ] Final QA report подписан

**После этого — go-live с soft launch.**

---

## Суммарная метрика Wave 14

- **E2E tests**: 150+
- **Visual snapshots**: 150+
- **A11y checks**: 50+
- **Load scenarios**: 4
- **Chaos scenarios**: 6
- **UAT scripts**: 20+
- **Combined CI time**: ~30 минут (parallel)

Менеджеры **НЕ тестируют вручную** — они только проводят UAT по готовым скриптам. Регрессия ловится автоматически.
