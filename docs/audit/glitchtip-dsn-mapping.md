# GlitchTip DSN mapping

_Rotated 2026-04-21 (post public-readiness cleanup). Original file purged from
git history via `git filter-repo` — it contained plaintext DSN values._

| Project | ProjectID | DSN format | Env var location |
|---------|-----------|------------|-------------------|
| `crm-staging` | 1 | `https://<32-hex>@glitchtip.groupprofi.ru/1` | `/opt/proficrm-staging/.env` |
| `crm-prod` | 2 | `https://<32-hex>@glitchtip.groupprofi.ru/2` | `/opt/proficrm/.env` |

Full DSN values — stored in env files only (mode 600). **Never commit DSN values
to this file or any other versioned file**.

## How to retrieve DSN (for debugging / new deploy)

On staging/prod server:
```bash
# Staging
ssh root@<staging-ip> 'grep ^SENTRY_DSN= /opt/proficrm-staging/.env'

# Prod (requires CONFIRM_PROD=yes marker for Claude Code)
ssh root@<prod-ip> 'grep ^SENTRY_DSN= /opt/proficrm/.env'
```

Or via GlitchTip Django shell:
```bash
ssh root@<host> "docker exec proficrm-observability-glitchtip-web-1 ./manage.py shell -c \"
from apps.projects.models import ProjectKey
for k in ProjectKey.objects.filter(is_active=True):
    pub = str(k.public_key).replace('-','')
    print(f'{k.project.slug} -> https://{pub}@glitchtip.groupprofi.ru/{k.project.id}')
\""
```

## How to rotate DSN (security event)

```bash
ssh root@<host> "docker exec proficrm-observability-glitchtip-web-1 ./manage.py shell -c \"
from apps.projects.models import Project, ProjectKey
for p in Project.objects.all():
    new = ProjectKey.objects.create(project=p, name='post-rotation-<date>', is_active=True)
    ProjectKey.objects.filter(project=p).exclude(id=new.id).update(is_active=False)
    print(f'{p.slug}: new DSN prefix <{str(new.public_key).replace(\"-\",\"\")[:8]}>')
\""
```

Then update `SENTRY_DSN` в соответствующих `.env` и restart web/celery контейнеры.
Verify event receipt через trigger test exception + check in GlitchTip Issues.

## Historical context

- 2026-04-20 Wave 0.4 Track C — GlitchTip self-hosted deploy, initial DSN wired.
  Mapping file created (with plaintext DSN — security bug).
- 2026-04-21 — public-readiness scan identified leak.
- 2026-04-21 — both DSN rotated, old keys `OLDSTAG*` (staging) and `OLDPROD*`
  (prod) deactivated. New keys active. This file recreated sanitized.
- 2026-04-21 — `git filter-repo` purged original file from git history,
  force-push to origin main.

Related files:
- `docs/runbooks/glitchtip-setup.md` — operational setup
- `docs/audit/incidents/2026-04-21-staging-502.md` — related incident context
- `docs/audit/public-readiness/REPORT.md` — scan that prompted rotation
