# Testing strategy (post-W0.5)

Project supports **dual test runners**:
- **`python manage.py test`** — Django native runner (unittest-based). Faster для single-test iteration.
- **`pytest`** — unified modern runner: parallel (xdist), coverage gate, Playwright E2E, factory_boy fixtures.

Оба runner'а работают — existing 1227 tests — unittest.TestCase subclasses — compatible с pytest via `pytest-django`.

---

## When to use what

| Scenario | Runner |
|----------|--------|
| TDD single test (fast feedback) | `manage.py test <app>.<test_case>` |
| Full suite locally | `manage.py test` OR `pytest` |
| Parallel execution | `pytest -n auto` |
| Coverage measurement | `pytest --cov=backend --cov-report=term-missing` |
| E2E / browser tests | `pytest -m e2e tests/e2e/` (Playwright) |
| CI primary | `manage.py test` (unchanged from W0-W0.4) |
| CI coverage gate | `coverage run manage.py test && coverage report --fail-under=50` |

---

## Quick commands

### Run full suite
```bash
# Django runner (primary)
cd backend && DJANGO_SETTINGS_MODULE=crm.settings_test python manage.py test

# pytest (alternative)
cd backend && pytest -n auto
```

### Coverage check
```bash
cd backend
DJANGO_SETTINGS_MODULE=crm.settings_test coverage run --source=. \
  --omit='*/migrations/*,*/tests*.py,*/test_*.py,*/conftest*.py,manage.py,crm/asgi.py,crm/wsgi.py,amocrm/migrate.py,*/management/commands/*' \
  manage.py test --verbosity=0
coverage report --skip-empty
coverage html -d ../htmlcov/
```

### Run specific test
```bash
# Django
python manage.py test messenger.tests.test_widget_offhours.ContactedBackActionTests

# pytest
pytest backend/messenger/tests/test_widget_offhours.py::ContactedBackActionTests
```

### E2E (Playwright)
```bash
# Install browser first (one-time)
playwright install chromium

# Run with visible browser
pytest -m e2e tests/e2e/ --headed

# Headless (default, CI-friendly)
STAGING_TEST_PASS=<password> pytest -m e2e tests/e2e/
```

---

## Fixtures architecture

`tests/fixtures/` — shared pytest fixtures (auto-discovered via `pytest_plugins` в root `conftest.py`).

### Factories (`tests/fixtures/factories.py`)

Hot-path models only (models with 50+ uses в existing tests). W1 test-infra follow-up — factories для remaining 65 models.

```python
from tests.fixtures.factories import UserFactory, CompanyFactory

def test_something(db):
    user = UserFactory()                  # Default: MANAGER role
    admin = AdminFactory()                # Superuser + ADMIN role
    company = CompanyFactory(created_by=user)
```

Available factories:
- `BranchFactory` — подразделения (ЕКБ/Тюмень/Краснодар).
- `UserFactory` + `AdminFactory` + `ManagerFactory` + `BranchDirectorFactory`.
- `CompanyFactory` — основная сущность CRM.
- `ContactFactory` — прикреплён к company.
- `TaskFactory` — task lifecycle.

### User fixtures (`tests/fixtures/users.py`)

Pytest shortcuts для common patterns:
- `user` — default manager.
- `admin_user` — superuser.
- `branch_ekb`, `branch_tyumen` — branches.
- `manager_ekb`, `branch_director` — users attached to branches.
- `authenticated_client`, `admin_client` — Django test clients, pre-logged in.

### Waffle flag fixtures (`tests/fixtures/waffle_flags.py`)

Context managers для feature flag overrides:
- `ui_v3b_off`, `ui_v3b_on` — UI_V3B_DEFAULT flag (W0.3).
- `messenger_enabled` — MESSENGER_ENABLED (required для messenger tests).
- `policy_engine_enforce` — POLICY_ENGINE_ENFORCE (W2).
- `two_factor_mandatory` — TWO_FACTOR_MANDATORY_FOR_ADMINS (W2).

Example:
```python
def test_messenger_api(authenticated_client, messenger_enabled):
    response = authenticated_client.get("/api/conversations/")
    assert response.status_code == 200
```

---

## Markers

Registered в `[tool.pytest.ini_options]` `markers` секции:

- `@pytest.mark.e2e` — browser-based tests (slow, separate workflow).
- `@pytest.mark.slow` — tests > 1s (CI может skip через `-m 'not slow'`).
- `@pytest.mark.integration` — DB + external mocks.
- `@pytest.mark.smoke` — quick smoke checks.
- `@pytest.mark.flaky(reruns=2)` — auto-retry (via `pytest-rerunfailures`).

---

## Coverage

**Current baseline** (2026-04-21, W0.5 restored): **~55% total**, `fail_under=50` gate.

Key modules (W0.3/W0.4 additions):
- `core/sentry_context.py` — 100%
- `core/feature_flags.py` — 95%
- `core/permissions.py` — 100%
- `crm/health.py` — 74%
- `crm/middleware.py` — 84%
- `crm/views.py` — 61%

Excluded from coverage (`pyproject.toml` `[tool.coverage.run] omit`):
- `amocrm/migrate.py` — legacy, marked для removal в W1 refactor.
- `*/management/commands/*` — manual-invocation scripts, not tested by design.
- Standard exclusions: migrations, tests, manage.py, asgi/wsgi.

**Trajectory** (see `docs/plan/00_MASTER_PLAN.md` §2.5):
- W0.5: 50 (restored, buffer from 55% actual).
- W1: temporary 48 (legacy deletion может просесть coverage).
- W1 end: 53.
- W2-W14: +2-5%/wave → 85 final.

---

## Parallel execution (pytest-xdist)

```bash
# Auto-detect CPU count
pytest -n auto

# Fixed worker count
pytest -n 4
```

**Known issues**:
- Some tests assume serial execution (shared DB state, file fixtures, singleton models).
- If parallel fails but serial passes — add `@pytest.mark.serial` OR revert to single-threaded for that test.
- Current: full suite parallel — **TODO measure speedup в W1** (locally ~34s serial).

---

## E2E — separate workflow

E2E tests **не запускаются в каждом CI run** (slow, require browser binary).

Planned: `.github/workflows/e2e.yml` (W1 deliverable):
- Triggers: `workflow_dispatch` (manual), nightly cron, PR с label `run-e2e`.
- Install browser: `playwright install chromium`.
- Run: `pytest -m e2e tests/e2e/`.

Current `tests/e2e/test_login_flow.py` — example template.

---

## Out of scope (deferred)

- **Mutation testing** (mutmut / cosmic-ray) — W14 QA wave.
- **Performance benchmarks** (pytest-benchmark) — W13.
- **Factories for all 70 models** — W1 refactor follow-up.
- **Full E2E workflow в CI pipeline** — W1 deliverable.
- **Visual regression testing** (Playwright snapshots) — W9 UX wave.

---

## Troubleshooting

### `AppRegistryNotReady` во время pytest collection
Проверь `DJANGO_SETTINGS_MODULE` в `[tool.pytest.ini_options]` — должно быть `crm.settings_test`.
`pytest-django` auto-init Django до collection.

### Tests pass locally, fail в CI
Обычно env var difference. CI имеет: `crm.settings_test`, POSTGRES_HOST=localhost, no messenger env vars. Check `.github/workflows/ci.yml` env section.

### `MessengerEnabledApiMixin` → 404
Default `MESSENGER_ENABLED=False`. Test должен использовать `@override_settings(MESSENGER_ENABLED=True)` на class level ИЛИ fixture `messenger_enabled` (pytest).

### Parallel run fails, serial passes
Test shares state. Add `@pytest.mark.serial` (register в markers) OR restructure test.
