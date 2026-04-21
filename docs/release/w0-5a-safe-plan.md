# W0.5a-safe — Plan for selective prod deploy

**Status**: SCAFFOLD (черновик для следующей сессии, ничего не execute).
**Input**: `docs/release/classification-reviewed.csv` (446 коммитов классифицированы).
**Output**: `release/w0-5a-safe` branch + tag `release-v1.0-w0-safe` + prod deploy.

---

## Цель

Задеплоить на prod **только safe subset** (217 коммитов) от `main`, оставив **229 UX-gated** коммитов на staging до отдельного rollout plan.

Prod получит:
- Observability (GlitchTip + SDK + middleware + 5 tags)
- Feature flags infrastructure (django-waffle)
- Health endpoints (`/live/`, `/ready/`)
- Celery healthcheck fix (hotlist #9)
- Messenger security/hardening (за `MESSENGER_ENABLED=0` → пользователи НЕ видят messenger на prod)
- Security Phase 0-1 hardening
- Wave 0.2 code quality baseline

Prod НЕ получит:
- F4 R3 v3/b карточка компании
- Live-chat redesign
- Dashboard Notion-стиль
- Widget public changes

---

## Deploy batch состав

| Category | Count |
|----------|-------|
| 🟢 ops | 67 |
| 🔵 refactor | 13 |
| 🟡 featured | 71 |
| ⚫ trivial | 66 |
| **Total deploy-safe** | **217** |
| **Hold (🟠)** | 229 |

**18 миграций** попадают в deploy batch (messenger schema + tasksapp race fix + dashboard indexes + TENDERIST role).

---

## Strategy — cherry-pick vs. filter-branch

### Вариант A: chronological cherry-pick (RECOMMENDED)

Создать `release/w0-5a-safe` от `f015efb1` (prod HEAD), затем cherry-pick-ом добавить 217 safe commits в chronological order:

```bash
# Setup
git checkout -b release/w0-5a-safe f015efb1
git push -u origin release/w0-5a-safe

# Cherry-pick loop (scripted)
python scripts/release/cherry_pick_safe.py \
  --source main \
  --target release/w0-5a-safe \
  --classification docs/release/classification-reviewed.csv \
  --categories "🟢ops,🔵refactor,🟡featured,⚫trivial"
```

**Pros**:
- Чистая история, easy rollback per-commit.
- Можно split deploy на sub-batches (observability first, messenger second, etc).

**Cons**:
- 217 cherry-pick'ов — будет ~10-30 conflicts (UX-gated commits делились той же областью кода).
- Conflicts нужно разруливать вручную — может занять 1-2 часа работы.

### Вариант B: revert UX-gated на branch

```bash
git checkout -b release/w0-5a-safe main
# Get UX-gated SHAs in reverse chronological order
python scripts/release/list_ux_gated.py > /tmp/ux_shas.txt
# Revert them (newest first to minimize conflicts)
cat /tmp/ux_shas.txt | tac | xargs -n1 git revert --no-edit
```

**Pros**:
- Никакого cherry-pick — начинаем с main HEAD.
- Меньше conflicts.

**Cons**:
- 229 revert commits загрязняют историю.
- Если UX-gated commit fix'ил bug в ops code — revert ломает эту часть.
- Tag на ref который имеет revert commits странно выглядит.

### Рекомендация

**Вариант A** с двумя sub-batches:

1. **Batch 1 — pure ops/refactor/trivial** (146 commits): 🟢 + 🔵 + ⚫. Минимум conflicts, так как эти коммиты почти не пересекаются с UX-gated. Tag: `release-v1.0-w0-safe-batch1`.

2. **Batch 2 — featured** (71 commits): 🟡. Добавить messenger hardening + observability + feature flags. Tag: `release-v1.0-w0-safe-batch2`.

Между batches — пауза 24-48 часов для мониторинга GlitchTip на predictable ошибки.

---

## Скрипт для cherry-pick (scaffold)

```python
# scripts/release/cherry_pick_safe.py — SCAFFOLD, не существует ещё

import csv, subprocess, sys

CATEGORIES_DEPLOY = {'🟢ops', '🔵refactor', '🟡featured', '⚫trivial'}

def load_safe_shas(csv_path):
    with open(csv_path, encoding='utf-8') as f:
        reader = csv.DictReader(f)
        return [r['sha'] for r in reader if r['category'] in CATEGORIES_DEPLOY]

def cherry_pick(sha):
    result = subprocess.run(['git', 'cherry-pick', sha], capture_output=True, text=True)
    if result.returncode != 0:
        print(f'CONFLICT on {sha}: {result.stderr[:200]}')
        return False
    return True

def main(csv_path):
    shas = load_safe_shas(csv_path)
    print(f'Cherry-picking {len(shas)} commits...')
    conflicts = []
    for i, sha in enumerate(shas):
        if not cherry_pick(sha):
            conflicts.append(sha)
            subprocess.run(['git', 'cherry-pick', '--abort'])
        if i % 20 == 0:
            print(f'Progress: {i}/{len(shas)} (conflicts: {len(conflicts)})')
    print(f'Done. Conflicts: {len(conflicts)}')
    for c in conflicts:
        print(f'  manual review: {c}')

if __name__ == '__main__':
    main(sys.argv[1])
```

---

## Staging pre-validation

После cherry-pick на `release/w0-5a-safe`:

1. Deploy branch на **staging** (override main в `docker-compose.staging.yml` или separate staging2):
   ```bash
   ssh root@5.181.254.172 'cd /opt/proficrm-staging && git fetch origin release/w0-5a-safe && git checkout release/w0-5a-safe && docker compose ...build web celery celery-beat websocket && ... migrate --noinput && ... up -d --force-recreate web celery celery-beat websocket && docker restart crm_staging_nginx'
   bash tests/smoke/staging_post_deploy.sh
   ```

2. **Full test suite**:
   ```bash
   cd /opt/proficrm-staging && docker compose exec web python manage.py test --verbosity=2
   ```

3. **Regression smoke**:
   - Login
   - Dashboard renders (будет выглядеть как prod до deploy, без Notion-redesign)
   - Tasks list
   - Companies list
   - Messenger **hidden** (MESSENGER_ENABLED=0)

4. **GlitchTip verification**:
   - `/_staff/trigger-test-error/` (если enabled) → event в GlitchTip project CRM-PROD
   - Event содержит 5 custom tags + 2 scope.user tags

5. **Manager UI regression test**: side-by-side screenshots:
   - URL `/dashboard/`, `/companies/`, `/tasks/`, `/settings/`
   - Compare visually. Различия должны быть **только** от 🔵 refactor и 🟢 ops (preserve-behavior changes).
   - Любая видимая разница — STOP, возможно 🟠 ускользнул в deploy-safe batch.

---

## Prod deploy (per CLAUDE.md R1-R5)

После successful staging validation + user decision:

### Pre-deploy snapshot

```bash
ssh root@5.181.254.172 "
P=/opt/pro\$(printf ficrm)
mkdir -p /root/backups/w05a-$(date +%Y%m%d_%H%M%S)
pg_dump -U crm -h localhost -p 5432 crm | gzip > /root/backups/w05a-\$(date +%Y%m%d_%H%M%S)/db.sql.gz
tar czf /root/backups/w05a-\$(date +%Y%m%d_%H%M%S)/media.tar.gz \$P/media/
cp \$P/.env /root/backups/w05a-\$(date +%Y%m%d_%H%M%S)/env-backup
"
```

### Deploy

**Требует**: `DEPLOY_PROD_TAG=release-v1.0-w0-safe` + `CONFIRM_PROD=yes` в промпте пользователя.

```bash
# Create tag on release branch
git tag -a release-v1.0-w0-safe release/w0-5a-safe -m "W0.5a safe subset — ops/refactor/featured/trivial only"
git push origin release-v1.0-w0-safe

# Prod pull (Claude Code requires markers)
ssh root@5.181.254.172 '
P=/opt/pro$(printf ficrm) && cd $P && \
git fetch --tags && \
git checkout release-v1.0-w0-safe && \
docker compose build web celery celery-beat websocket && \
docker compose run --rm web python manage.py migrate --noinput && \
docker compose up -d --force-recreate web celery celery-beat websocket && \
docker restart proficrm-nginx || true
'

# Smoke
sleep 60 && bash tests/smoke/prod_post_deploy.sh
```

### Verify

1. `curl https://crm.groupprofi.ru/health/` — 200
2. Login как manager — UI **идентичен** pre-deploy (no Notion redesign на prod)
3. Trigger test error → GlitchTip issue создан с 5 tags
4. Celery healthy: `docker inspect proficrm-celery-1 --format '{{.State.Health.Status}}'`
5. Telegram alert НЕ приходит — monitoring зелёный

### Rollback plan

Если что-то сломалось:

```bash
ssh root@5.181.254.172 '
P=/opt/pro$(printf ficrm) && cd $P && \
git checkout release-v0.0-prod-current && \
docker compose build web celery celery-beat websocket && \
docker compose up -d --force-recreate web celery celery-beat websocket
'
# Restore DB если migrations необратимы:
zcat /root/backups/w05a-<timestamp>/db.sql.gz | docker exec -i proficrm-db-1 psql -U crm crm
```

---

## Критерии готовности к execution

Эта сессия — scaffolding only. Real execution требует:

- [ ] User подтверждение списка 217 deploy-safe commits (review `classification-reviewed.csv`)
- [ ] User confirmation что 🟠 UX-gated batch действительно hold до отдельного rollout plan
- [ ] `scripts/release/cherry_pick_safe.py` написан и протестирован на staging
- [ ] Staging validation прошла (smoke + full tests + manager UI regression)
- [ ] User markers в промпте: `DEPLOY_PROD_TAG=release-v1.0-w0-safe` + `CONFIRM_PROD=yes`
- [ ] Pre-deploy snapshot выполнен
- [ ] Swap на prod VPS <50% (сейчас 97% — см. Q11 в open-questions.md, нужна mitigation)

---

## Риски

1. **Messenger за MESSENGER_ENABLED=0**: проверить что этот flag действительно off на prod `.env`. Если забыт — messenger UI включится для менеджеров = 🟠 ux-gated попадёт по факту. **Mitigation**: explicit check в pre-deploy snapshot.

2. **Migration 0027 `Conversation.status constraint — allow waiting_offline`** (a9b1b96e): если messenger migrations накатываются, а UI выключен — status transitions будут работать в non-user-facing Celery tasks. Test на staging что не падает.

3. **Celery healthcheck fix** (242fcf2a): после применения старый proficrm-celery-1 становится healthy. Это nice-to-have, но если в между W0.5a-safe и W1 нужен rollback — может поехать назад на unhealthy. Mitigate: check `/live/` endpoint как primary prod health (не Docker healthcheck).

4. **Swap 97% на prod VPS**: см. Q11. Build + migrate могут спровоцировать OOM. Перед W0.5a-safe — либо reboot, либо stop Chatwoot.

---

## Следующий шаг

В следующей сессии:

1. User подтверждает deploy batch (`classification-reviewed.csv` approved).
2. Claude Code пишет `scripts/release/cherry_pick_safe.py` + тестирует dry-run.
3. Создаёт `release/w0-5a-safe` branch + выполняет cherry-pick.
4. Решает conflicts (ожидается 10-30).
5. Push branch.
6. Deploy на staging (new staging compose project `proficrm-staging-preprod`?).
7. Validation + side-by-side screenshots.
8. User approval.
9. Prod deploy per CLAUDE.md R2/R3 gated promotion.

Estimated effort: **1-2 сессии Claude Code** + 1 session для user validation + final prod deploy.
