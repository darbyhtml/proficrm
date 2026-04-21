# Hotlist — Топ-7 «трогать первыми»

_Снапшот: **2026-04-20**. Источник: Wave 0.1 audit, top-20 tech-debt → фильтр по score ≥ 80._

Это **index для следующих сессий**: каждый из 7 файлов появится в одной из волн W1-W3 **точечно**, не целиком. Здесь зафиксирован приоритет, размер, и где именно каждый атакуется.

Если сессия начинается «что рефакторить сегодня?» — смотри сюда до README.

---

## 1. `backend/ui/views/company_detail.py` — 2 698 LOC

- **Score:** 100 (impact 5 × freq 5 × risk 4)
- **Где лечится:** **Wave 1** (Phase 4-5 по плану refactoring-specialist)
- **Что сделано ранее:** Phase 0-3 дали −185 LOC (коммиты `2048f4ef`, `126b7930`, `05b34036`, `785d314a`)
- **Что осталось:**
  - Phase 4: `companies/services/company_overview.py` — context-builder для `company_detail` view (≈300-500 LOC)
  - Phase 5: extract `/settings/cold-call/*` views в `companies/services/cold_call.py`
  - Phase 6 (новое после audit): удалить денормализацию `Company.phone/email/contact_name/position` → CompanyPhone/CompanyEmail/Contact
- **Ожидаемое уменьшение:** 2698 → ≈ 1 800 LOC после W1
- **Риск регрессии:** высокий — god-view трогают каждый день
- **Правило:** каждое удаление сопровождать тестом и запуском `manage.py test companies ui`

## 2. `backend/ui/views/_base.py` — ≈ 1 700 LOC

- **Score:** 100 (impact 5 × freq 5 × risk 4)
- **Где лечится:** **Wave 1** (после company_detail)
- **Что внутри:** helpers + decorators + querysets + policy-check wrappers — всё в одном файле
- **Расщепление:**
  - `ui/views/_helpers/company_filters.py` (уже частично — `_apply_company_filters`)
  - `ui/views/_helpers/task_filters.py`
  - `ui/views/_helpers/access.py` — `_can_edit_company`, `_can_delete_company`, `_detach_client_branches`
  - `ui/views/_helpers/notify.py` — `_notify_branch_leads`, `_notify_head_deleted_with_branches`
  - `ui/views/_helpers/logging.py` — `log_event` (переезжает из здесь в audit.services)
- **Ожидаемое уменьшение:** 1 700 → ≈ 400 LOC (остаётся только import-re-export shim)
- **Правило:** сохранить полную обратную совместимость — любой `from ui.views._base import X` должен работать

## 3. `backend/templates/ui/company_detail.html` — 8 781 LOC

- **Score:** 100 (impact 5 × freq 5 × risk 4)
- **Где лечится:** **Wave 9** (UX унификация) + **Wave 11** (CSP strict)
- **Что внутри:** 33 inline `<script>` блока на ≈ 4 719 LOC JS, 6+ inline `<style>` на ≈ 200 LOC CSS
- **План расщепления:**
  - Выделить JS-логику в `backend/static/ui/company_detail/*.js` (по функциональным блокам: timeline, phone-edit, email-edit, delete-workflow, popup-menu, etc.)
  - Использовать `{% include %}` для повторяющихся partials (popup-menu, input-like edit, phone chip)
  - CSP nonce per-request для оставшихся inline scripts
- **Ожидаемое уменьшение:** 8 781 → ≈ 1 500 LOC HTML + ≈ 3 500 LOC external JS (минификация даст −40%)
- **Риск:** визуальная регрессия → Playwright snapshot tests до/после

## 4. `backend/messenger/static/messenger/operator-panel.js` — 204 KB → **134 KB (−35%)** ✅ MIN BUILT

- **Score:** 48 (impact 4 × freq 3 × risk 4)
- **Статус (2026-04-20, W0.2h):** `.min.js` **сгенерирован** через `npx esbuild`,
  закоммичен в `backend/messenger/static/messenger/operator-panel.min.js` +
  `.min.js.map` (source map для debug). **Экономия: 70 KB на запрос.**
- **Осталось для Wave 10:**
  - Подключить `.min.js` в шаблонах только при `DEBUG=False`:
    ```django
    {% if debug %}
      <script src="{% static 'messenger/operator-panel.js' %}"></script>
    {% else %}
      <script src="{% static 'messenger/operator-panel.min.js' %}"></script>
    {% endif %}
    ```
  - Добавить minify в CI/deploy pipeline (`make build-js`)
  - Playwright визуальная проверка: `.min.js` ведёт себя идентично
- **Путь:** `backend/messenger/static/messenger/` (не `backend/static/ui/` как было в первом audit)

## 5. `backend/messenger/static/messenger/widget.js` — 99 KB → **60 KB (−39%)** ✅ MIN BUILT

- **Score:** 36 (impact 4 × freq 3 × risk 3)
- **Статус:** `.min.js` **сгенерирован** + `.min.js.map`. **Экономия: 39 KB.**
- **Особенность:** **публичный файл** — встраивается через `<script>` на
  сторонних сайтах клиентов GroupProfi. −39% bundle = прямой выигрыш для их PageSpeed.
- **Осталось для Wave 10:**
  - Подключить `.min.js` в embed-коде виджета
  - **Обязательно**: SRI (Subresource Integrity) `integrity="sha384-..."` в tag
  - Опционально: CDN-hosting для кеширования (MinIO + nginx proxy из W10.1+10.3)

## 6. `backend/audit/tasks.py::purge_old_activity_events` — P0 runtime risk

- **Score:** 75 (impact 5 × freq 3 × risk 5)
- **Где лечится:** **Wave 3** (core CRM hardening)
- **Статус сейчас:** **Disabled в beat** (2026-04-20, коммит post-W0.1 cleanup). Функция остаётся импортируемой — `tests_retention.py` её вызывает на тестовом наборе.
- **Что переписать:**
  ```python
  # BEFORE: ActivityEvent.objects.filter(created_at__lt=cutoff).delete()
  # AFTER:
  CHUNK_SIZE = 100_000
  while True:
      ids = list(
          ActivityEvent.objects.filter(created_at__lt=cutoff)
          .values_list("id", flat=True)[:CHUNK_SIZE]
      )
      if not ids:
          break
      deleted, _ = ActivityEvent.objects.filter(id__in=ids).delete()
      logger.info("purge: batch %d rows", deleted)
      time.sleep(2)  # даём ATO-репликации вдохнуть
  ```
- **После фикса:** восстановить beat entry в `settings.py::CELERY_BEAT_SCHEDULE`

## 7. `ActivityEvent` composite index — `(actor_id, created_at)`

- **Score:** 80 (impact 5 × freq 4 × risk 4)
- **Где лечится:** **Wave 13** (performance optimization)
- **Контекст:** 9.5M → 87K строк после Release 0 purge (через RULE + batch DELETE). Но при росте снова упрётся в медленные queries на `/audit/?user=X&days=30`.
- **Миграция:**
  ```python
  # audit/migrations/0012_activityevent_actor_created_index.py
  migrations.AddIndex(
      model_name="activityevent",
      index=models.Index(
          fields=["actor_id", "-created_at"],
          name="audit_activity_actor_created_idx",
      ),
  )
  ```
- **Верификация:** `EXPLAIN ANALYZE` до/после на запросе из `settings_audit_log` view. Ожидаем → Index Scan вместо Seq Scan, 700ms → <50ms.

## 10. Prod код без `sentry_sdk.init()` + без `SentryContextMiddleware` — errors невидимы

- **Score:** 85 (impact 5 × freq 5 × risk 4-5 depending on error rate)
- **Где лечится:** **W0.5a Release 1 sync wave** (tag `release-v1.0-w0-complete`)
- **Контекст:** prod HEAD `be569ad` (2026-03-17) не содержит:
  - `sentry_sdk.init(...)` в `settings.py` (интеграция появилась в `397eb85e`)
  - `SentryContextMiddleware` в `MIDDLEWARE` (появилась в `09e1f94e`)
  - `/live/` `/ready/` `/_debug/sentry-error/` endpoints (`crm/health.py`)
  - `core.feature_flags` + `core.sentry_context` модули
- **Сейчас в prod `.env`** (после W0.4 closeout 2026-04-21):
  - `SENTRY_DSN=...` лежит безвредно (код не читает)
  - `SENTRY_ENVIRONMENT=production` лежит безвредно (тоже)
- **Что это значит бизнесу:** любой uncaught exception на prod **не доходит до GlitchTip**. Ошибки видны только в:
  1. Django `ErrorLog` модель (через `crm.middleware.ErrorLoggingMiddleware`)
  2. User-facing 500 ("Внутренняя ошибка сервера") — менеджер сообщает вручную
  3. `docker logs proficrm-web-1` (ограничено retention Docker, последние ~N MB)
- **После W0.5a sync** (`git checkout release-v1.0-w0-complete` + `docker compose up -d`):
  - SDK сам подхватит SENTRY_DSN из env
  - Middleware начнёт обогащать events 5 тегами (branch, role, request_id, feature_flags, + user.id/username через scope.user)
  - `/live/` + `/ready/` появятся (можно мониторить через Kuma с более гранулярным health-check)
- **Риск если W0.5a задержать:** каждая prod-ошибка до sync невидима. При росте трафика или рефакторинге (W1+) — критично. Максимум разумной задержки — **7 дней** от W0.4 closeout.

---

## 9. `proficrm-celery-1` unhealthy на prod 11+ часов — Release 1 drift

- **Score:** 75 (impact 5 × freq 3 × risk 5)
- **Где лечится:** **Release 1 verification checklist** (не отдельный рефактор)
- **Обнаружено:** Wave 0.4 pre-flight (`docs/open-questions.md` Q3), `docker ps` показал
  prod-контейнер `proficrm-celery-1` в статусе `Up 11 hours (unhealthy)`
- **Контекст:** healthcheck-fix применён в коммите `242fcf2a` (Release 0, 2026-04-20),
  но prod HEAD остался на `be569ad` (2026-03-17). Between: **333 коммита** прогресса
  не развёрнуто
- **Проверка (Release 1 smoke-test):**
  ```bash
  # Перед Release 1 — confirmed что healthcheck-fix применится:
  ssh root@prod
  docker inspect proficrm-celery-1 --format '{{json .State.Health}}' | jq
  # Ожидаем status=unhealthy, последний check с ошибкой `celery inspect ping` или similar
  ```
- **Действие при Release 1:** в checklist `docs/runbooks/21-release-1-ready-to-execute.md`
  добавить шаг post-deploy:
  ```bash
  # После git pull + docker compose build + docker compose up -d
  sleep 90
  docker ps --filter name=proficrm-celery-1 --format '{{.Status}}'
  # Ожидаем Up N seconds (healthy) — подтверждает применение 242fcf2a
  ```
- **Риск, если не проверить:** Celery-task генерации напоминаний / FTS rebuild / расписание
  могут быть остановлены, а healthcheck будет показывать ложно-healthy (никто не узнает)
- **НЕ чинить сейчас** (вне W0.4 scope — prod policy запрещает touching из Claude Code)

---

## 8. `backend/messenger/tasks.py::escalate_waiting_conversations` — Notification без dedupe

- **Score:** 80 (impact 4 × freq 5 × risk 4)
- **Где лечится:** **Wave 3** (core CRM hardening, вместе с escalate_stalled)
- **Статус сейчас:** работает, но 3 прямых `Notification.objects.create(...)` внутри task, курсор `escalation_level` обновляется **после** create, beat каждые 30 секунд — двойной тик beat = 2× уведомлений одному и тому же ROP.
- **Обнаружено:** Wave 0.2 deep audit Celery tasks (`docs/audit/celery-unsafe-patterns.md`).
- **Что переписать:**
  ```python
  # BEFORE: прямые create вне transaction.atomic, курсор escalation_level
  # ставится после, beat каждые 30 секунд → race при overlap beat-тиков.
  if target_level == 3 and conv.branch_id:
      for rop in rops:
          Notification.objects.create(...)    # прямой create без dedupe
      stats["rop_alert"] += 1
  ...
  Conversation.objects.filter(pk=conv.pk).update(escalation_level=target_level, ...)

  # AFTER: весь блок в transaction.atomic + dedupe_seconds + Redis-lock на task
  with transaction.atomic():
      if target_level == 3 and conv.branch_id:
          for rop in rops:
              notify(
                  user=rop,
                  kind=Notification.Kind.INFO,
                  title=f"Клиент ждёт {int(waiting)} мин — требуется вмешательство",
                  body=...,
                  url=f"/messenger/?conv={conv.id}",
                  payload={"conversation_id": conv.id, "level": "rop_alert"},
                  dedupe_seconds=60,   # <<< защита от двойного beat-тика
              )
          stats["rop_alert"] += 1
      ...
      Conversation.objects.filter(pk=conv.pk).update(
          escalation_level=target_level,
          last_escalated_at=now,
      )
  ```
  Плюс Redis-lock на уровне task (30с timeout, как в `generate_recurring_tasks`):
  ```python
  LOCK_KEY = "messenger:escalate_waiting:lock"
  if not cache.add(LOCK_KEY, "1", timeout=30):
      return {"skipped": "locked"}
  ```
- **Верификация:** Playwright-сценарий «2 оператора, 5 диалогов в waiting 10 мин» → в колокольчике ровно 5 Notification, не 10.

---

## Как использовать этот файл

1. **Начало сессии рефактора:** прочитать этот hotlist + соответствующий `docs/plan/0N_wave_*.md`.
2. **Планирование следующего PR:** выбрать ОДИН item из hotlist → открыть его соответствующую волну → взять конкретный Этап.
3. **После завершения item:** обновить статус здесь (✅ DONE, cross-reference на коммит).

## Что НЕ в hotlist (намеренно)

- **35 моделей без `verbose_name`** — мелочь, пакетный PR в W9
- **5 singleton-моделей без `pk=1` constraint** — риск реальный, но единичная миграция, в W3
- **100% API без `@extend_schema`** — большая работа (~3 дня), но не блокер runtime → W11
- **70 duplicate endpoints `/api/` vs `/api/v1/`** — косметика, W11
- **10 моделей без тестов** — распределяется по волнам вместе с рефактором кода, не отдельный item

---

## История изменений

| Дата | Изменение |
|------|-----------|
| 2026-04-20 | Создан после Wave 0.1 audit. Baseline для W1-W13. |
| 2026-04-20 | Wave 0.2 deep audit celery tasks → добавлен item 8 (`escalate_waiting_conversations`, score 80). |
| 2026-04-20 | Wave 0.2h: items #4 и #5 отмечены как `.min.js` BUILT (экономия 109 KB); подключение в шаблонах остаётся в Wave 10. |
| 2026-04-20 | Wave 0.4 pre-flight → добавлен item 9 (`proficrm-celery-1 unhealthy`, score 75, Release 1 checklist). |
| 2026-04-21 | Wave 0.4 closeout → добавлен item 10 (prod без sentry init + middleware, score 85, W0.5a блокер). |
