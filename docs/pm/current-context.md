# PM current context

_Живое состояние текущей PM-сессии. PM обновляет этот файл перед предсказуемым compact или каждые 30-60 минут активной работы. После compact — читается ПЕРВЫМ для восстановления контекста._

**Last updated:** 2026-04-24 10:45 UTC (PM).

---

## 🎯 Current session goal

**Pivot на дневную работу:** prod session отложена до вечера (prod downtime в час пик не уместен). Вместо этого запускаем серию design prototype промптов в Claude Design — пазл из 16 web модулей + 9 Android модулей. Дмитрий вставляет в Claude Design, я пишу brief, он показывает результат, итерируем.

**Brand palette подтверждён Дмитрием:**
- `#01948E` primary (бирюзовый)
- `#FDAD3A` accent (оранжевый)
- `#003D38` deep (тёмно-зелёный, для текста / dark elements)
- `#C2E2DE` soft (нежно голубо-серый)
- Фон белый, текст чёрный или deep. Brand с нуля (раньше guidelines не было).

**Начинаем с W1 Design tokens** → W2 Controls → W3 Layout components → W8 Company detail (самая сложная) → W6 Dashboard. Цель на сегодня: 4-5 модулей.

### W1 — ✅ APPROVED (2026-04-24)

Design tokens в Claude Design утверждён после 1 iteration правок:
- 12/12 секций брифа покрыты (palette / neutral / semantic / states / typography / spacing / radii / shadows / transitions / accessibility / component states / CSS vars reference).
- 138 CSS custom properties с префиксом `--proficrm-`.
- 40 brand shades (4 × 10) + 10 neutral + state colors.
- Brand hex match: primary-500=#01948E, accent-500=#FDAD3A, deep-500=#003D38, soft-300=#C2E2DE (soft base on 300 — documented as exception).
- Iteration fix: accent-300/400 inversion swapped на smooth ramp; soft legend note добавлен.

Переходим к W2 Component controls.

### W2 — ✅ APPROVED (2026-04-24)

Controls component library утверждён без iteration правок (zero defects):
- 8/8 секций: Buttons / Text inputs / Textarea / Select / Checkbox / Radio+Segmented / Datepicker / Tokens used reference.
- 0 hardcoded hex — все цвета через W1 CSS vars.
- Russian B2B realistic context (ИНН hints, договор 30/70, менеджеры с городом, Telegram disabled).
- Bonus coverage: loading states, edge cases (Критический только для РОП), segmented с view icons, range highlight с endpoints.

Переходим к W3 Layout.

### W3 — ✅ APPROVED (2026-04-24)

Layout component library утверждён без iteration (как и W2):
- 8/8 секций: Cards / Modals / Tables / Tabs / Badges-Tags-Chips / Alerts / Toasts / Tokens used.
- W2 controls explicitly reused без modifications.
- Realistic Russian B2B references (44-ФЗ тендер, финансовые суммы с typographic spaces, договор № 2025-1842, СМТ recovery pattern).
- Bonus patterns: activity log dense с uppercase action badges, W4 preview CTA card, toast progress bar hover-pause showcase.

Переходим к W8 Company detail (самая сложная страница проекта).

## 📋 Active constraints

- Path E: **ACTIVE** — но для этой задачи **security exception** применяется (CLAUDE.md §Деплой R3: «CONFIRM_PROD=yes allowed для security CVEs»). Public postgres exposure попадает под criteria.
- Staging стабилен после W10.2-early (HTTP 200, 7/7 containers).
- pg_dump safety net на прод должен быть проверен ДО любого prod change.

## 🔄 Last decision made

**Timestamp:** 2026-04-24 10:45 UTC.
**Decision:** ranжировка recommendations pokryvaet:
- 1 → prod `0.0.0.0:5432` fix (CRITICAL).
- 2 → revoke CF_API_TOKEN (Дмитрий dashboard).
- 3 → merge feature-ветки в main.
Затем W10.5 Prometheus stack как следующая крупная задача.
**Reasoning:** security first (снижает risk surface), потом housekeeping (revoke + merge), потом substantive work.
**Owner:** Дмитрий approved recommendations. PM пишет промпт #1.

## ⏭️ Next expected action

1. ✅ Обновить `docs/pm/current-context.md`.
2. ✅ Коммит.
3. ⏭️ Передать Дмитрию промпт для prod postgres isolation session.
4. ⏭️ Ждать rapport (~30-60 минут).
5. ⏭️ После rapport: approve CF token revoke (Дмитрий) + merge feature-branch (PM coordinates с Дмитрием).
6. ⏭️ Далее: W10.5 Prometheus + Grafana + Loki (~6-10 часов, multi-session).

## ❓ Pending questions to Дмитрий

- [ ] **Timing prod session.** Текущее время ~10:45 UTC (~13:45 MSK). Рабочий день в РФ в разгаре — staging можно на любое время, но prod downtime (хоть 30 сек) затронет реальных пользователей. Варианты:
  - Сейчас — risk, но быстро закрыть security gap.
  - Вечер MSK (21:00+ MSK = 18:00 UTC) — меньше активных пользователей.
  - Утро (6:00 UTC = 9:00 MSK) — до пика рабочего дня.
  
  **Моя рекомендация:** вечер MSK (после 18:00 UTC) — баланс «закрыть сегодня» × «минимум пользователей affected». Promт готов, когда time window выберешь — передаёшь исполнителю.

## 📊 Post-W10.2-early state

- WAL-G PITR живой, cron активный, restore drill passed.
- Архивация работает: каждую минуту WAL в R2.
- Retention cron на воскресенье 02:00 UTC (завтра или послезавтра? — сегодня четверг 24 Apr, воскресенье = 27 Apr).

## 🚨 Red flags (if any)

**CRITICAL активно:**

- Prod postgres `0.0.0.0:5432` — публичный interface → любой интернет-хост может попытаться TCP connect + postgres-level auth. Только password protection. Не соответствует security best practice.

Задача этого промпта — закрыть.

## 📝 Running notes

### Что включает prod fix session

- Pre-check: prod state, pg_dump cron active, последний backup свежий (не старше 24 часов).
- `ss -tlnp` подтверждение текущего exposure.
- Edit `/opt/proficrm/docker-compose.yml` (или соответствующий prod compose) — db service ports на `127.0.0.1:5432:5432`.
- `docker compose up -d db` — recreate контейнера.
- Breaking action: ~30 сек prod downtime + web/celery могут потребовать restart для новой DNS resolution.
- Verify post-fix: `ss -tlnp | grep 5432` — только localhost; приложения работают; HTTP 200 на prod endpoints.
- Rollback plan: `git revert <compose commit>` + `docker compose up -d db` + restart web/celery.

### Post-session tasks (PM)

- Update hotlist: CRITICAL item → CLOSED.
- Update ADR / runbook (если создаём security runbook).
- Commit prod compose changes в репо (с `CONFIRM_PROD=yes` marker в commit message).
- Recommend Дмитрию — push prod изменений в separate commit для audit trail.

### Update triggers (reminder)

- Перед каждой передачей Дмитрию.
- Каждые 30-60 минут активной работы.
- После получения рапорта исполнителя.
- После принятия решения.
- Перед длительной операцией.
- При приближении к компактификации контекста.
