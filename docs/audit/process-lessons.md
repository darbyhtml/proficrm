# Process lessons — уроки процесса

_Живой файл. Пополняется по мере выявления ошибок процесса планирования /
DoD / ops. Уроки — не про конкретный баг, а про то что позволило багу
проскользнуть._

---

## 2026-04-20 / Wave 0.4 — «Deploy complete» ≠ «End-to-end UX works»

### Что случилось

После W0.4 GlitchTip deploy отчитан как **COMPLETE**: TLS OK, `/_health/`
200, контейнеры Up, тестовая команда `manage.py migrate` прошла успешно,
superuser создан.

Пользователь при первой попытке выполнить post-deploy ручные шаги
(login → create project → get DSN) получил **HTTP 500** на `/login` POST.

Root cause: Redis недоступен для glitchtip-web (TimeoutError на
host.docker.internal:6379). `/_health/` endpoint Redis не проверяет,
только DB + liveness. Поэтому контейнер выглядел здоровым при сломанной
end-to-end авторизации.

Детали: `docs/audit/glitchtip-500-diag.md`.

### Что это в процессе

**DoD W0.4 включал**:
- GlitchTip работает на https://glitchtip.groupprofi.ru/ с TLS ✅
- Тестовая ошибка с 5 тегами ⏸ (blocked на login)
- JSON logs структурные ✅
- /health/ и /ready/ работают ✅
- GlitchTip backup ✅
- 3+ тестов на middleware ✅
- Runbooks ✅
- ADR-003 ✅

Формально 6 из 8 DoD пунктов зелёные. Но первая же человеческая
активность после deploy (login) — провал.

### Урок

**«Service deployed» требует end-to-end UX smoke test как часть DoD**,
не только инфраструктурные проверки. Observability endpoint'ы
(`/health`, `/ready`, `/_health/`) — необходимые но **не достаточные**
индикаторы готовности. Они проверяют что процесс жив и зависимости
базово подняты. Они **не проверяют** что первичный пользовательский
путь (login / create object / основная бизнес-функция) работает.

Класс ошибок — тот же, что «написал тесты → прогнал → зелёные → сломано
в проде из-за недотестированной интеграции».

### Правило на будущее

**Для любого нового сервиса DoD обязан включать end-to-end smoke test
основного пользовательского пути**:

| Тип сервиса | Обязательный smoke |
|-------------|---------------------|
| Веб-админка (GlitchTip, Metabase, и т.п.) | Login API + UI login (Playwright) |
| REST API | Получить токен + сделать authed request к core endpoint'у |
| Celery-воркер нового пула | Enqueue task → прочитать результат |
| Email-backend | Отправить test email → получить в inbox / webhook |
| Storage (MinIO) | PUT + GET объекта, проверить content |
| Observability pipeline | Отправить test-error → увидеть в UI с правильными tags |

Один из этих тестов **ОБЯЗАТЕЛЬНО** в DoD. Без него deploy — не deploy.

### Применение

- В `docs/plan/01_wave_0_audit.md` Этап 0.4 DoD — добавлен пункт
  «Login smoke (API + Playwright)» (manual update пользователем, я не
  трогаю docs/plan).
- В `docs/runbooks/glitchtip-setup.md` — раздел «Login smoke tests
  (ОБЯЗАТЕЛЬНЫ после любого restart/recreate)».
- В `docs/runbooks/glitchtip-troubleshooting.md` — полный runbook
  диагностики и фикса login 500.
- В `tests/smoke/prod_post_deploy.sh` (создан в W0.4 B-track) — базовый
  набор smoke-тестов для prod-deploy, расширяется в следующих волнах.

### Обобщение

Каждая волна W0.N через W15 при завершении должна:
1. Чеклист DoD включает end-to-end UX smoke хотя бы одного пути.
2. Smoke test — автоматизированный (bash + curl, Playwright, pytest).
3. Smoke test запускается как часть acceptance, не добавляется задним числом.
4. «Зелёный CI + зелёный monitoring» → deploy **ещё не завершён**.
   Deploy завершён → «зелёный CI + зелёный monitoring + зелёный E2E smoke».

---

## 2026-04-21 / Wave 0.4 closeout — «Shell-level middleware test ≠ real HTTP request»

### Что случилось

В W0.4 closeout я рапортовал middleware DoD ACHIEVED со ссылкой на event
`d0b4cd50` с 5 правильными тегами. Event был создан так:

```python
from django.test import RequestFactory
req = RequestFactory().get("/smoke")
req.user = real_user
SentryContextMiddleware(lambda r: None)._enrich_scope(req)  # ← вручную!
sentry_sdk.capture_exception(RuntimeError("..."))
```

Это **shell-level** тест: я вручную вызвал `_enrich_scope()`, затем отправил
event. Scope был обогащён — теги прилетели в GlitchTip.

**На реальном HTTP traffic** пользователь увидел `role=anonymous`, `branch=none`
в большинстве events. Он справедливо указал что я пропустил проверку.

### Что это в процессе

Shell-тест валидирует **функциональную корректность** `_enrich_scope()`, но
**не интеграцию** с Django MIDDLEWARE chain:
- Не проходит через `AuthenticationMiddleware` (request.user может быть не
  тот, что я ожидаю в shell).
- Не проходит через `SecurityMiddleware` (SSL redirect и т.п.).
- Не проходит через все handler'ы в правильном порядке.

DoD W0.4 включал: «Тестовая ошибка видна с 5 тегами». Я интерпретировал это как
«хоть какое-то событие с 5 тегами». Правильная интерпретация: «событие от
**real HTTP request** через Django MIDDLEWARE chain».

### Урок

**Middleware verification требует полного request-cycle через Django dispatch**:

```python
# Валидный test:
from django.test import Client
c = Client(raise_request_exception=False)
c.force_login(user)
c.get("/_debug/sentry-error/", secure=True)  # через MIDDLEWARE chain
sentry_sdk.flush()
# → проверить event в GlitchTip
```

**ИЛИ** через настоящий HTTP-запрос (curl → nginx → gunicorn → Django).

`RequestFactory` + manual `_enrich_scope()` вызов — не эквивалент. Дают ложный
positive при правильной функции но сломанной интеграции.

### Правило на будущее

Для любой middleware в DoD:

| Test level | Что проверяет | Достаточно для DoD? |
|-----------|----------------|---------------------|
| Unit test с `RequestFactory` + ручной вызов `_enrich_scope()` | Функция корректна | **Нет** — complement only |
| `django.test.Client` (`c.get()`) | Django MIDDLEWARE chain OK | **Да** — минимальный уровень |
| curl через nginx (real HTTP) | Full path включая nginx+TLS | **Да + рекомендуется** |
| Playwright (browser-level) | User-facing UX + redirects + JS | Best для UI-critical |

**DoD-пункт**: «верифицировано через Client.force_login + API query» или
«верифицировано через curl с real cookies + API query».

### Применение

- **Today's fix**: repro через `Client.force_login()` + `secure=True` — event
  `1798b12f` подтвердил middleware работает. Anonymous events в real traffic
  (Kuma probes, public curl, non-login paths) — by design, не баг.
- Обновлён `docs/runbooks/glitchtip-setup.md` §«Login smoke tests» — теперь
  использует `Client.force_login` в smoke commands (не factory-based).
- `docs/plan/01_wave_0_audit.md` §0.4 DoD **не трогаю** (пользователь правит
  план сам), но зафиксировано замечание в `open-questions.md` для next revision.

### Обобщение

Каждая волна где middleware/auth/scope manipulation:
1. **Unit test**: RequestFactory+patch — проверяет функции.
2. **Integration test**: `Client.force_login()` — проверяет Django chain.
3. **E2E smoke**: curl или Playwright — проверяет full stack.

**В DoD — минимум (2), предпочтительно (3)**.

---

## Template для новых уроков

```markdown
## YYYY-MM-DD / Wave X.Y — <краткое название>

### Что случилось
(факты)

### Что это в процессе
(какое место в процессе позволило этому проскочить)

### Урок
(правило / принцип для будущего)

### Правило на будущее
(конкретное применение — таблица / чек-лист)

### Применение
(где именно обновлено)

### Обобщение
(как применять к следующим волнам)
```
