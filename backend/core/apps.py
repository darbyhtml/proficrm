"""Django app config для core — Wave 0.3.

До W0.3 core был utility-модулем без регистрации в INSTALLED_APPS.
С добавлением feature_flags data-миграции (0001_initial_feature_flags)
нужна регистрация — чтобы Django знал где искать миграции core.
"""

from __future__ import annotations

from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "core"
    verbose_name = "Core (shared utilities)"
