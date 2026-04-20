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
