# W0.5a-safe — planning status + recommendation

**Status**: ⚠️ **SELECTIVE BACKPORT INFEASIBLE** после двух попыток (Option C revert, Option A cherry-pick). Требуется альтернативная стратегия (см. §Recommendation).
**Input**: `docs/release/classification-reviewed.csv` (446 коммитов классифицированы).
**Session 2026-04-21 attempt**: Both options failed due to file-level coupling.

---

## Что было попытано

### Option C — revert 229 🟠 UX-gated commits from main

```
git checkout -b release/w0-5a-safe origin/main
while read sha; do
  git revert --no-edit --strategy-option=theirs "$sha" || git revert --abort
done < held-shas-reverse.txt
```

**Результат**: 208 successful reverts + **21 conflicts** (см. `docs/release/w0-5a-option-c-conflicts.txt`).

Конфликтные commits — это **мажорные feature commits** с broad file impact:
- `785d314a`, `05b34036`, `126b7930` — F4 R3 Phase 1-3 company refactoring (extract services).
- `2a3f9ea3`, `cdb58410`, `33950339` — F4 R3 card redesign.
- `4ecb0cb6`, `01dbf4d3`, `862abe93`, `a9f19a24`, `bc0061fc` — Messenger Phase 1-3 (WebSocket, campaigns, automations, sound, push).
- `e9db8f0e` — Android F9 admin UI.
- `c7ccbaae` — Analytics F7 R1 KPI dashboard.
- `bdcc8ec2`, `3cc9ca19` — Mailer F6 R1/R2 SMTP onboarding UI.
- `be88074d` — Dashboard P0 security + refactor + 40 tests.
- `190aee3f` — Harden master-plan Phases 1-5 (Security/DB/Perf/DevOps).
- `45572f98` — templates has_role templatetag.
- `60b9f87b`, `41dedddc` — messenger merge/fix commits.
- `47c510e6` — Chatwoot-style messenger UI/UX.

Причина conflict: subsequent commits **дополняют** файлы, созданные этими — revert одного пытается удалить file, который потом был расширен.

Strategy `-X theirs` также не разрешает — это rename/delete structural conflicts, не content diff.

### Option A — cherry-pick 217 🟢🔵🟡⚫ safe commits on empty branch from prod HEAD

```
git checkout -b release/w0-5a-safe f015efb1
while read sha; do
  git cherry-pick --no-edit --allow-empty "$sha" || git cherry-pick --abort
done < safe-shas.txt
```

**Результат**: 72 successful + **145 conflicts** (67% failure rate) — см. `docs/release/w0-5a-option-a-conflicts.txt`.

Причина: safe commits часто applying patch на файл, ещё не созданный held commit'ом (который предшествует в хронологии). Cherry-pick ожидает base state, но base никогда не создан в branch.

---

## Root cause — почему selective infeasible

**Fundamental file-level coupling** между safe и held commits:

1. **Messenger** (🟡 featured за `MESSENGER_ENABLED`): 40+ hardening commits работают на `backend/messenger/api.py` и `views.py`, **которые были созданы** в held commits (Chatwoot migration, Phase 1-3 redesign).
2. **Company refactoring** (🔵 refactor Phase 1-3 Services): extract functions **из** god-view `backend/ui/views/company_detail.py`, который сам был **изменён** в F4 R3 v3/b redesign (held).
3. **Dashboard hardening** (`be88074d`): безопасность P0 + refactor 40 tests **на** dashboard view, который переписан в Notion redesign (held).
4. **Settings hardening** (`4378f3ed`, `58747499`): applies rate-limit **на** messenger routes, которые добавлены в messenger Phase 1-3 (held).
5. **Templates has_role** (`45572f98`): template tag используется в held template changes для проверки ролей.

Попытка разделить эти коммиты чисто через cherry-pick/revert не работает потому что они **genetically связаны** с intermediate held commits через shared file state.

---

## Recommendation: Alternative strategy

Selective-subset подход отклонён. Три реальные опции:

### Option R1 — Full main deploy + UI_V2 feature flag для UX gating

Deploy **весь main** на prod, но все **user-visible UX changes** за новым waffle flag `UI_V2_ENABLED` (default OFF).

**Как это выглядит**:

1. Создать waffle flag `UI_V2_ENABLED` + middleware/template helper.
2. Для templates, которые изменились в 🟠 UX-gated commits — сохранить legacy версии как `<name>_legacy.html`, обернуть в `{% if user_sees_v2 %}...{% else %}...{% endif %}`.
3. Для views которые refactored — добавить route guard: `if UI_V2_ENABLED: new_view else: legacy_view`.
4. Deploy main на prod. Flag OFF → менеджеры видят **прежнюю** UI.
5. После manager training — activate flag gradually (per-user, per-branch, или all at once).

**Cons**: огромная работа (~1-2 недели dev). Требует preservation legacy templates в parallel.

**Pros**: Чёткая rollback модель (flag OFF). Не трогаем историю. Можно rollout постепенно.

### Option R2 — Full main deploy ПОСЛЕ manager training

1. User проводит 1-2 training sessions с менеджерами на staging.
2. Screenshots + сравнение old/new UI.
3. Deploy весь main на prod (без selective, без feature flags).
4. Kuma + GlitchTip monitoring + Telegram alerts для быстрого rollback.
5. Rollback plan: prod снапшот + `git checkout release-v0.0-prod-current` если плохо.

**Cons**: Big-bang approach. Если регрессия у менеджера — rollback всего.

**Pros**: Simplest. Не требует extra dev. Можно сделать в один night session.

### Option R3 — Hybrid (Recommended)

Смешанный:

1. **Сейчас (W0.5a-safe-lite, если нужно срочно)**: deploy ТОЛЬКО **изолированные infrastructure commits** не трогающие user-facing files. Узкая подмножество — ~30-50 коммитов (`.github/`, `scripts/`, `Makefile`, `requirements*.txt`, `backend/crm/settings.py`). Нет messenger, нет refactor god-views.

2. **W0.5b (следующая итерация)**: полный main deploy после manager training + screenshot review.

Это даёт пользователю **GlitchTip + feature flags infra + Celery healthcheck fix + security settings hardening** без UX touches. Meaning: observability уедет на prod, UX — подождёт.

---

## Что на самом деле блокирует prod deploy

Прямо сейчас prod висит на `f015efb1` (2026-03-20). Main — на `90663bd1` (2026-04-21). Всё что попытались разделить — coupled. Реальный blocker — **не technical**, а **organizational**:

1. **Manager training** — нужно 1-2 часа user sessions с менеджерами на staging для ознакомления с новым UX.
2. **Screenshot review** — side-by-side comparisons old/new для каждой crucial страницы (dashboard, tasks, companies, settings, messenger).
3. **Decision**: активировать v3b карточку компании, live-chat redesign, dashboard Notion-стиль — **когда и как**?

Без этих решений selective backport **не даёт ценности** — все messenger, dashboard, analytics hardening commits **тащат за собой** UI changes.

---

## Что делать в этой сессии

### ✅ Done
- Q12 resolved (CI failure root cause: **GitHub Actions billing / spending limit**).
- Classification 446 commits верна и остаётся valid.
- Documented attempt failures (Option C: 21 conflicts, Option A: 145 conflicts).
- Recommendation R1/R2/R3 для user decision.

### ❌ Не делал в этой сессии
- НЕ push release branch (не создан жизнеспособно).
- НЕ cherry-pick дальше вручную.
- НЕ создавать tag `release-v1.0-w0-safe`.

### 🔄 Staging текущее состояние
- Git HEAD: `18e2ed9a` (drift от main из-за billing block).
- Docker containers: healthy (SEV2 manual rebuild).
- /live/, /ready/, /health/: 200.
- После unlock billing + first success CI → auto-pull до main через новую deploy-staging.yml.

---

## Next session — зависит от user decision

### Путь R2 (Full deploy after training)
User prompt: "W0.5a-full: deploy main to prod after manager training completed" + `DEPLOY_PROD_TAG=release-v1.0-w0-full` + `CONFIRM_PROD=yes`.
- Pre-deploy snapshot.
- `git tag release-v1.0-w0-full main`.
- Deploy.
- Monitor 24h.

### Путь R1 (UI_V2 flag development)
User prompt: "Start W0.5b: implement UI_V2_ENABLED feature flag for selective UX rollout".
- 1-2 weeks work.
- Preserve legacy templates parallel.
- Then deploy main, flag OFF by default.

### Путь R3 (Infrastructure-only W0.5a-safe-lite)
User prompt: "Prepare W0.5a-safe-lite: extract only pure infrastructure commits (no user-facing files)".
- Narrower subset (~30-50 commits).
- Investigate file coupling с нуля.
- Если опять не-тривиально — R2/R1.

### Перед любой из опций — обязательно fix Q12
Без работающего CI auto-deploy pipeline gated promotion невозможен. User action: billing → settings/billing/spending_limit → fix payment / increase limit / make repo public.

---

## Артефакты в этой сессии

- `docs/release/classification-summary.md` — валидна (classification correct).
- `docs/release/classification-reviewed.csv` — source of truth.
- `docs/release/w0-5a-option-c-conflicts.txt` — 21 коммит conflict при revert.
- `docs/release/w0-5a-option-a-conflicts.txt` — 145 коммит conflict при cherry-pick.
- `docs/audit/gh-actions-timeline-2026-04-21.txt` — GH Actions billing issue audit.
- `docs/open-questions.md` Q12 — RESOLVED (billing, user action required).
