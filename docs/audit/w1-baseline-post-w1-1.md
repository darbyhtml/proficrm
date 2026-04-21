# W1 Baseline — Post-W1.1, Pre-W1.2

**Snapshot**: 2026-04-21 (после завершения W1.1 `_base.py` split).

Эти числа — baseline для W1.2 и последующих волн W1. Метрики не должны ухудшиться после W1.2 (`company_detail.py` split).

---

## Tests

| Metric | Value | Source |
|--------|-------|--------|
| Test count | **1140 passing** | `manage.py test --settings=crm.settings_test` on staging |
| Test time | 53.9s | same run |
| Status | ✅ all green | — |

Команда измерения:
```bash
ssh sdm@5.181.254.172 "cd /opt/proficrm-staging && docker compose -f docker-compose.staging.yml -p proficrm-staging \
  exec -T web python manage.py test --settings=crm.settings_test --verbosity=1 | grep -E '^(Ran |OK|FAILED|ERROR)'"
```

---

## Coverage

| Metric | Value | Source |
|--------|-------|--------|
| Total coverage | **52%** | `coverage report` on staging (Makefile pattern) |
| Statements | 24 694 | same |
| Missing | 11 902 | same |
| fail_under gate (CI) | 50% (local pyproject) / 45% (staging image — pre-W0.5 pin) | `pyproject.toml` |

Команда измерения (через Makefile target):
```bash
cd /app/backend && DJANGO_SETTINGS_MODULE=crm.settings_test \
  coverage run --source=. --omit='*/migrations/*,*/tests*.py,*/test_*.py,*/conftest*.py,manage.py,crm/asgi.py,crm/wsgi.py' \
  manage.py test --verbosity=0 && coverage report --skip-empty
```

**Note**: staging image coverage-gate=45% — артефакт Q15 pin (временно 50→45 пока `amocrm/` не удалён был, W0.5). Локальный `pyproject.toml` уже вернулся на **50**. После W1.2 → цель **53** к концу W1.

---

## File sizes (Hotlist #1-2 focus)

| File | LOC | Status |
|------|-----|--------|
| `backend/ui/views/_base.py` | **371** | ✅ W1.1 closed (был 1251, −70%) |
| `backend/ui/views/company_detail.py` | **3022** | 🔴 W1.2 target (baseline в audit был 2698 — реальный LOC вырос post-audit snapshot из-за F4 R3 v3b additions 18-19.04) |
| Помощники | `ui/views/helpers/*.py` — 1002 LOC total | ✅ W1.1 new |

---

## Conditions for W1.2 "no regression"

W1.2 (`company_detail.py` split) must not break:

- [ ] **Test count**: стабильно 1140. Меньше — откат.
- [ ] **Coverage**: ≥ 52%. Меньше — добор тестов или rollback.
- [ ] **All 30+ URL routes** обслуживаются той же функцией (только переместилась).
- [ ] **CI 8/8 jobs green** на каждом коммите W1.2.
- [ ] **Staging smoke** зелёный после каждой extraction.
- [ ] **Kuma monitor** Up после финального деплоя.

---

## Next W1 waves (из MASTER_PLAN)

- **W1.2** — `company_detail.py` split (текущая, этот baseline).
- **W1.3** — `company_detail.html` template split (8 781 LOC → partials).
- **W1.4** — switch callers с `_base.py` re-exports на прямые импорты из `helpers/` (deprecation cleanup).
