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

## 2026-04-21 / Wave 0.4 SEV2 — «Tests pass + CI green» ≠ «staging healthy»

### Что случилось

Коммит `7e834829 test(w0.4): real-traffic verification — /_staff/trigger-test-error/ endpoint + script` 
был запушен в main в **10:09 UTC** 21 апреля. Staging auto-deploy прошёл (git pull + build).

Я **закрыл сессию** с репортом «W0.4 FINAL CLOSEOUT — all DoD achieved».

В **10:12 UTC** Uptime Kuma прислал в Telegram-чат пользователя `[CRM Staging] is DOWN`. 
В **~10:15 UTC** пользователь написал «SEV2: Staging DOWN 1.5+ часа после твоего коммита 7e834829».

Staging был DOWN **~8 минут фактически** (MTTR 10:09 → 10:17), но пользователь это обнаружил 
через Telegram alert уже ПОСЛЕ того, как я отчитался «complete». Двойная неудача:
1. Не проверил post-deploy health — закрыл сессию без валидации.
2. Celery crash-loop (waffle missing в старом образе) + nginx DNS кэш на старый IP web-контейнера 
   проявились только при real HTTP traffic, не в shell-тесте.

### Что это в процессе

**DoD W0.4 включал** все функциональные проверки (middleware tests, real-traffic event, 
Playwright UI flow). Ни одного пункта «staging external reachability после deploy».

«Tests pass + commit pushed + CI green» было моим сигналом «готово». Это ложный сигнал.
CI прогоняет тесты в изолированной среде (docker-in-docker, fresh containers). Он **не воспроизводит**:
- Реальный rolling restart staging контейнеров
- nginx DNS кэш на старый IP (specific к `docker compose` force-recreate)
- Celery image drift (staging целеры мог остаться от предыдущего билда)
- IP whitelist поверх /live/ /ready/ /health/ endpoint'ов

**Класс ошибок**: «CI green поэтому deploy готов». Но CI green означает только что код 
компилируется и тесты изоляционно проходят. Не означает что staging инфраструктура принимает 
этот код.

### Урок

**После ЛЮБОГО изменения staging (push в main, docker compose up, build, restart) 
ОБЯЗАТЕЛЬНО выполнить `make smoke-staging` в течение 5 минут**. Зелёный прогон — 
жёсткое условие закрытия сессии.

«Всё задеплоено» ≠ «staging здоров». Разрыв закрывается только external-reachability 
probe через ту же сеть, что у внешнего пользователя (host nginx → staging nginx → web).

### Правило на будущее (hard rule)

**ЖЁСТКОЕ правило (hard rule)**: сессия, которая НЕ заканчивается зелёным `make smoke-staging`, 
считается незакрытой. Либо чинишь, либо `git revert` до зелёного состояния.

Механика:
| Триггер | Обязательное действие |
|---------|------------------------|
| `git push` в main → auto-deploy staging | `make smoke-staging` в течение 5 минут |
| Ручной `docker compose up/build/restart` на staging | `make smoke-staging` сразу после |
| Любое изменение staging config (nginx, env, volumes) | `make smoke-staging` после применения |
| Закрытие сессии, в которой были deploy-события | `make smoke-staging` финальный прогон |

**Если smoke красный**:
1. Не пиши «complete» / «готово» / «закрываю сессию».
2. Диагностируй через `docker compose logs`, `docker ps`, nginx logs.
3. Либо fix (пересобрать зависший контейнер, restart nginx для DNS), либо `git revert`.
4. Re-run smoke до зелёного.

### Применение

- **Создан** `tests/smoke/staging_post_deploy.sh` — 6 probe'ов (3 health + home + login + API).
- **Makefile targets** `smoke-staging` и `smoke-prod` (alias на bash скрипт).
- **CLAUDE.md** — раздел «MANDATORY: End-of-session staging health check» в самом начале файла.
- **Rule violations log** в CLAUDE.md — инцидент 7e834829 зафиксирован как первая запись.

### Обобщение

Каждая волна W0.N через W15 при завершении:
1. Автоматизированный smoke (`make smoke-staging`) — зелёный.
2. Без зелёного smoke — сессия не завершена, никаких «complete» в отчёте.
3. Smoke запускается **после последнего коммита** push, не до.
4. Если smoke красный 5+ минут — rollback (`git revert` + push) приоритетнее чем диагностика.

---

## 2026-04-21 / Wave 0.4 SEV2 — Monitoring alerts = PRIMARY events, не noise

### Что случилось

Пользователь получил в Telegram `[CRM Staging] is DOWN` alert в 10:12 UTC. Между этим моментом 
и его сообщением «SEV2» прошло ~3 минуты. Я в это время был в процессе закрытия сессии 
(писал финальный отчёт W0.4), alert я **не видел** — он пришёл в личный чат пользователя, 
не в мою tool-output.

Когда пользователь написал «SEV2», первая моя реакция могла бы быть «странный alert, 
возможно ложный» (так я делал в прошлых сессиях с Kuma probe'ами). Это было бы ошибкой: 
staging был реально down.

### Что это в процессе

В предыдущих сессиях (W0.4 setup) я не раз характеризовал Kuma alerts как «probe noise» 
или «Kuma false positive» — когда Kuma ловила 403 на /live/ из-за IP whitelist. Это 
сформировало у меня **ложный prior**: «alert может быть шумом».

Это prior неприменим к production-level monitoring. Uptime Kuma настроен на user-facing 
HTTPS endpoint. Если он говорит DOWN — он реально DOWN для внешнего пользователя, 
независимо от того что видит `docker ps`.

User-reported alert — даже сильнее чем Kuma native alert, потому что у пользователя 
есть контекст (он видел предыдущие alerts, он знает что «странно» а что нет).

### Урок

**User-reported monitoring alerts («[CRM Staging] Down», любой Telegram alert переслан 
пользователем) — PRIMARY events**. Никогда не характеризовать как «странный / возможно 
ложный / проверю потом». Всегда верифицировать target environment **НЕМЕДЛЕННО** — 
до любого другого обсуждения.

### Правило на будущее (hard rule)

**ЖЁСТКОЕ правило**: при получении от пользователя сообщения, содержащего:
- «DOWN» / «down» / «недоступен» / «502» / «503» / «504»
- Имя монитора (`[CRM Staging]`, `[CRM Production]`)
- Скриншот Telegram alert

→ **Первое действие ВСЕГДА**: `curl -sSk -o /dev/null -w '%{http_code}\n' https://<env>/live/` 
или `make smoke-staging`. ДО того как отвечать на любые вопросы, ДО того как писать отчёт, 
ДО reasoning о причинах.

**Если smoke красный**: немедленно переходим в incident response mode:
1. Assess (что именно сломано).
2. Rollback vs fix (rollback предпочтительнее если fix > 10 минут).
3. Verify (зелёный smoke + пользователь подтвердил UP).
4. RCA (постфактум, после восстановления).

**Запрещённые фразы** в ответ на alert:
- «Странный alert»
- «Возможно ложный positive»
- «Kuma probe flake»
- «Сейчас посмотрю, но скорее всего OK»
- «Это из-за whitelist / networking / race condition [без verification]»

### Применение

- `CLAUDE.md` §«MANDATORY: End-of-session staging health check» — указано что alert = PRIMARY.
- Этот урок (process-lessons #3) — фиксация hard rule.
- Incident file `docs/audit/incidents/2026-04-21-staging-502.md` — timeline показывает 
  что user-report пришёл до моей финальной валидации.

### Обобщение

User + automated monitoring — **два независимых сигнала**. Если хоть один говорит DOWN — 
система DOWN до доказательства обратного. Моё внутреннее убеждение («я только что пушил 
зелёный коммит, CI прошёл») — **не доказательство**. Доказательство — зелёный external 
probe.

---

## 2026-04-21 / Public-readiness cleanup — Never commit live credentials in docs (hard)

### Что случилось

Во время W0.4 debug sessions live GlitchTip DSN (staging + prod) и SECRET_KEY 
были записаны **как есть** в:
- `docs/audit/glitchtip-dsn-mapping.md` (commit `a30689fc` от W0.4 Track C) — 
  полные DSN strings `https://<token>@glitchtip.groupprofi.ru/<id>` для staging + prod.
- `docs/audit/glitchtip-500-diag.md` (commit `6eeb585d`) — первые 40 символов 
  67-символьного `GLITCHTIP_SECRET_KEY`.

Plus commit messages в `a30689fc` и recreated-version commit тоже содержали 
DSN prefixes.

Обнаружено: public-readiness scan session 2026-04-21 (gitleaks + trufflehog + 
custom patterns). Стало критическим блокером для public-repo transition.

### Что это в процессе

Debug sessions часто требуют **reference конкретных env values** («вот какой 
DSN прописан в prod»). Natural instinct — скопировать raw value в docs для 
наглядности. Proper reference — **masked prefix** или **external lookup command**.

При работе с private repo это выглядит безвредным («никто кроме меня не 
увидит»). Но репо **может стать public** в будущем (как произошло для решения 
Q12 billing). История коммитов хранит эти values permanently.

### Урок

**Live credentials не принадлежат git — только env files mode 600**. Никогда, 
ни в каких условиях, ни в каких типах файлов (`.md`, `.py`, `.yml`, даже 
debug-`.txt`) не commit'ить:
- API keys, DSN tokens, SECRET_KEY fragments.
- Access tokens, refresh tokens.
- Passwords, passphrases.
- DB connection strings с embedded credentials.

Это relevant для **private repos too** — потому что:
- Repo может стать public через policy change / billing fix / acquisition.
- Insider threats: contractor / former employee clones the private repo.
- Backup leaks: сторонние системы (issue trackers, chat logs, fork analytics) 
  могут иметь cached history fragments.

### Правило на будущее (hard rule)

| Документация-задача | Правильный способ |
|---------------------|-------------------|
| «Где лежат credentials» | `/etc/proficrm/env.d/<file>.conf` (mode 600, never in git) |
| «DSN mapping для projects» | `https://<8chars>...@host/id` (masked prefix only) |
| «Debug: какой SECRET_KEY работает» | `grep ^SECRET_KEY= /path/conf \| head -c 20` (dynamic command) |
| Incident runbook | `<ENV_VAR_NAME> value in /path/to/conf` (name + path, not value) |
| Rotation procedure | Django shell / CLI command to read + mask (dynamic lookup) |

**Запрещённые форматы в git-tracked файлах**:
- `SENTRY_DSN=https://abc...xyz@host/id` (полный literal)
- `SECRET_KEY=base64url-40-chars-then-...` (truncated prefix — raises entropy attack)
- `API_KEY: abc123...` (even partial prefix — identifies ownership)

### Применение

- **`docs/audit/glitchtip-dsn-mapping.md`** recreated с masked format только.
- **`docs/audit/glitchtip-500-diag.md`** purged from history via `git filter-repo`.
- **Old DSN + SECRET_KEY** rotated + deactivated в GlitchTip DB.
- **Commit messages** с prefix fragments тоже rewritten через `--replace-text`.
- **Repo now public** — дополнительный stake против повторения.
- **scripts/release/gen_scan_summaries.py** — reusable tool для masked scan summaries.

### Обобщение

Каждая session где нужно **reference a credential**:

1. **Ask first**: это действительно нужно показать целиком? Или достаточно 
   prefix + path to env file?
2. **Default to masked**: `<first-8-chars>...<last-4-chars>` или `<REDACTED>`.
3. **Command-based lookup**: instead of copying value, document command to 
   retrieve — `ssh root@host 'grep ^VAR= /path/to/conf'`.
4. **Pre-commit hook**: `detect-secrets` + `gitleaks` в `.pre-commit-config.yaml` 
   (detect-secrets already configured). Если tool flag'ит — разобраться, не 
   suppressed blindly.
5. **Scan pre-visibility-change**: при любой policy change (private→public, 
   fork creation, new collaborator) — deep scan (gitleaks, trufflehog, custom 
   patterns) **before** change, не после.

**Recommendation**: добавить `gitleaks` в pre-commit + CI (public repo → GitHub 
Advanced Security free tier включит secret scanning на push automatically).

---

## 2026-04-21 / Post-public cleanup — Token scope audit after visibility change

### Что случилось

После toggle'а репозитория PRIVATE → PUBLIC (Phase F public-readiness cleanup)
проведён audit токенов, которые живут **вне git** но могли leak через другие 
каналы (screenshots в bug reports, chat logs, старые backup archives):

- **TG bot token** (`@proficrmdarbyoff_bot`) — проверен grep'ом всего public git 
  tree + docs. **0 matches** pattern `[0-9]{10}:[A-Za-z0-9_-]{35}`. Token 
  только в `/etc/proficrm/env.d/telegram-alerts.conf` (mode 600, root owner).
- **STAGING_SSH_PRIVATE_KEY** — GitHub secret, never в git.
- **GlitchTip admin password** — только `/etc/proficrm/env.d/glitchtip.conf` (mode 600).
- **GH_TOKEN (viewer scope)** — только `/etc/proficrm/env.d/github-actions-viewer.conf` 
  (mode 600, создан 2026-04-21 для Claude Code sessions).

### Что это в процессе

При переключении visibility нет automated guarantee что все tokens outside git 
**НЕ** have leaked. Scan tools (gitleaks, trufflehog) видят только git. 
Out-of-git paths requires manual audit.

### Урок

**После любой policy change (PRIVATE→PUBLIC, new collaborator, external fork 
request) → audit все tokens stored outside git**. Не assume что они safe just 
because git clean.

### Правило на будущее

Checklist post-visibility-change:

1. ✅ gitleaks + trufflehog scan (git history) — blocked visibility change 
   до clean.
2. ✅ `grep -rE '[0-9]{10}:[A-Za-z0-9_-]{35}'` (TG token pattern) в всём 
   repo — должно быть 0.
3. ✅ Проверить permissions всех config files в `/etc/proficrm/env.d/` — 
   mode 600, root owner.
4. ✅ Check CI secrets (GitHub Settings → Secrets and variables) — должны 
   быть в GitHub vault, не в git.
5. ⚠️ Optional paranoia: rotate tokens even если scan clean, if stakes high.

### Применение

- **`docs/audit/public-readiness/REPORT.md`** — документирует предварительный 
  scan + rotation of live secrets (GlitchTip DSN + SECRET_KEY).
- **This lesson** — fixation rule for future visibility changes.
- **No TG bot rotation performed** — scan clean, rotation not needed per rule 5.

### Обобщение

Каждый visibility change:
1. Pre-scan git history (already enforced via Phase A-E cleanup procedure).
2. Audit out-of-git tokens (this rule).
3. Document decision (rotation / leave-as-is / fresh key) с rationale.

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
