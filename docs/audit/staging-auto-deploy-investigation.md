# Staging auto-deploy investigation — 2026-04-21

**Контекст**: Track T pre-W0.5a cleanup session. Staging FETCH_HEAD застрял на
21 Apr 05:08 UTC (= 08:08 MSK), 7 последующих коммитов НЕ задеплоились.

---

## Факты

### Timeline

| Time (MSK) | Commit | SHA | Files | Staging deployed? |
|-----------|--------|-----|-------|-------------------|
| 08:07 | W0.4 Track D closeout | `18e2ed9a` | (known prior) | ✅ yes (FETCH_HEAD 08:08:26) |
| 09:51 | middleware chain verified | `587926c9` | 1 (docs only) | ❌ skipped |
| 09:52 | /live/ nginx 403 fix | `badfe220` | 3 (nginx configs) | ❌ skipped |
| 10:10 | split-scope Kuma monitoring | `7459231f` | 2 (docs + scripts/) | ❌ skipped |
| 10:11 | real-traffic verification endpoint | `7e834829` | 7 (backend/crm/{health,settings,urls}.py + scripts/) | ❌ skipped |
| 10:24 | SEV2 recovery + smoke | `ec8b85bb` | 5 (tests/smoke + Makefile + docs) | ❌ skipped |
| 10:38 | deploy workflow improvements | `cecf4717` | 5 (.github/workflows + Makefile + docs) | ❌ skipped |
| 10:39 | README update | `90663bd1` | 1 (README.md) | ❌ skipped (expected via new workflow) |

### Последний успешный deploy

- **Staging git HEAD**: `18e2ed9a` (Apr 21 05:08:26 UTC = 08:08:26 MSK).
- **FETCH_HEAD timestamp**: `2026-04-21 05:08:26.410039853 +0000`.
- После этого момента — 7 commits pushed, ни один не подхвачен.

### Проверки на сервере

```bash
$ ssh root@5.181.254.172
$ cat /root/.ssh/authorized_keys | ssh-keygen -lf -
521 SHA256:Q5gWydoDbrjJOPxlZ9Lv6EMCBcxm4TOs+hhGV/mUzQ8 terminal_access_manager (ECDSA)
521 SHA256:FPfoy+u921wOP8OoKVufnQhPvrWHl6duTTiZYtXPsjo filemanager_access_manager (ECDSA)
256 SHA256:fN4kLjFILrEcsiBGIybpJ6pNe8fI4O/6wcQUNyH8D0Y claude-code@WORKBOOK-PROFI_access_manager (ED25519)
```

**ВАЖНО**: 3 ключа, ни один не содержит "github-actions" / "deploy" / "staging" в
комменте. Deploy key от `STAGING_SSH_PRIVATE_KEY` secret на target server **не
найден** (по комменту).

Но: deploy **сработал** 08:08:26 MSK. Либо:
- (a) комментарий deploy key пустой/другой → я не распознал. Нужен сверка fingerprint'а с GitHub secret.
- (b) Ключ был добавлен-использован-удалён за окно `≤ 08:08:26`. Теоретически возможно, если кто-то делал security cleanup.

### CI workflow changes в range

```
cecf4717 — fix(ci/ops): celery rebuild in deploy + Makefile restart wrappers + post-deploy smoke
```

Это единственное изменение `.github/workflows/` файлов между 18e2ed9a и 90663bd1.
И это на `deploy-staging.yml`, не на `ci.yml`.

Значит CI workflow syntax/config **не менялся**, он должен работать.

---

## Гипотезы root cause

### Гипотеза 1: CI runs failed → `workflow_run.conclusion` != 'success' → deploy skip

`deploy-staging.yml` имеет condition:
```yaml
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]

jobs:
  deploy:
    if: ${{ github.event.workflow_run.conclusion == 'success' }}
```

Если CI падал на все 7 commits → deploy skipped all 7 times.

**Probability: HIGH**. Среди 7 commits есть backend changes (`7e834829` — новый endpoint + settings).

**Проверить**: `gh run list --workflow=ci.yml --limit=15` покажет статус каждого CI run.

### Гипотеза 2: `STAGING_SSH_PRIVATE_KEY` secret rotated/expired

Если deploy запускается, но SSH step fails — CI workflow sam passes, но deploy падает на первом шаге.

**Probability: LOW** — потому что тогда был бы failed run в deploy-staging (он отображается в Actions UI), но пользователь не видел такой ошибки.

**Проверить**: `gh run list --workflow=deploy-staging.yml --limit=15`.

### Гипотеза 3: `deploy-staging.yml` triggered но пропущен из-за concurrency

Если deploy-staging.yml имеет concurrency group и несколько коммитов подряд pushed — все deploys кроме последнего могут cancel.

**Probability: MEDIUM**. Смотрим текущий workflow:
```yaml
on:
  workflow_run:
    workflows: ["CI"]
    types: [completed]
    branches: [main]
```

`concurrency` не определён явно. Значит GitHub может использовать default behavior.

**Не объясняет полный skip** — хотя бы один run должен быть (последний).

### Гипотеза 4: `branches: [main]` filter на workflow_run не fires

`on.workflow_run.branches` может иметь специфику — фильтрует по `head_branch` upstream workflow.

**Probability: LOW** — раньше работало.

### Гипотеза 5: GitHub Actions quota exceeded / outage

Free tier: 2000 минут в месяц на private repo. Если проект переехал с free-tier на другой план или quota exhausted — deploys не запускаются.

**Probability: LOW-MEDIUM**. Нужно проверить billing/usage в GitHub UI.

---

## Текущий статус (2026-04-21 ~10:50 UTC после push 90663bd1)

Commit `90663bd1` pushed в 10:39 MSK (= 07:39 UTC).
По состоянию на 10:50 UTC:
- Staging HEAD: `18e2ed9a` (без изменений).
- Staging /live/: 200 (контейнеры работают на docker images от manual SEV2 rebuild).
- FETCH_HEAD: 2026-04-21 05:08:26 (не обновлён — значит deploy-staging workflow не запускался last 10+ минут).

**Вердикт**: deploy-staging.yml действительно не триггерится. Гипотеза 1 (CI failures) наиболее вероятна.

---

## Что НЕ делал в этой сессии

- **Не добавлял deploy key** в `/root/.ssh/authorized_keys` — security action, требует user approval.
- **Не изменял GitHub secrets** — нет auth.
- **Не попытался manually deploy** — staging OK и без pull (docker containers running, smoke green).
- **Не пытался `ssh authkeys restore`** — неизвестно был ли ключ.

Всё расследование read-only.

---

## Что нужно от пользователя

1. **`gh auth login` в следующей сессии** — даст Claude Code возможность видеть
   `gh run list --workflow=ci.yml --limit=30` и точно понять failure cause.

2. **Рекомендация на этой сессии** — откройте GitHub Actions UI:
   `https://github.com/darbyhtml/proficrm/actions`
   
   Проверьте:
   - Вкладка "CI" → runs на коммитах с `587926c9` по `90663bd1`. Все green/failed?
   - Вкладка "Deploy Staging" → были ли runs? Какой conclusion?
   
   Скриншот или текстовое описание даст точный ответ.

3. **Verify deploy key** — в GitHub Settings → Secrets and variables → Actions:
   - Есть ли `STAGING_SSH_PRIVATE_KEY` secret?
   - Last updated timestamp?
   
4. **Verify authorized_keys на сервере**:
   ```bash
   ssh root@5.181.254.172 'cat /root/.ssh/authorized_keys'
   ```
   Сравнить public counterpart GitHub secret с ключами на сервере.

---

## Что НЕ блокирует другие задачи

Отсутствие auto-deploy **не блокирует**:
- W0.5a-safe planning — это scaffold, не execute.
- Prod deploy — это gated promotion runbook, не через auto-deploy staging.
- Manual staging deploys через `make restart-staging-web` / `restart-staging-all` — работают.

Блокирует только automated CI → deploy → smoke → rollback pipeline для staging.

---

## Next session

Когда пользователь логинится `gh auth login` или предоставляет Actions UI info:

1. Получить `gh run list --workflow=ci.yml --limit=15`.
2. Если CI failed — посмотреть `gh run view <id> --log-failed`. Fix root cause.
3. Если secret expired — user ротирует + новый public key добавляет на `/root/.ssh/authorized_keys`.
4. Trigger dummy commit, verify deploy fires.

Документ закроется в `docs/audit/staging-auto-deploy-investigation.md` → addendum
«Resolved 2026-04-XX».
