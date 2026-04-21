"""Root pytest conftest (Wave 0.5).

Provides:
- django settings init via DJANGO_SETTINGS_MODULE=crm.settings_test (via pyproject.toml).
- Shared fixtures via pytest_plugins.

Django native runner (manage.py test) всё ещё работает — pytest просто второй runner.
"""

from __future__ import annotations

# Shared fixture modules — pytest auto-discovers fixtures в these modules.
pytest_plugins = [
    "tests.fixtures.factories",
    "tests.fixtures.users",
    "tests.fixtures.waffle_flags",
]
