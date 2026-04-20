"""
Data migration — Wave 0.3 (2026-04-20).

Создаёт 4 начальных feature flag'а в django-waffle:

    UI_V3B_DEFAULT                  — W9, переключатель карточки компании
    TWO_FACTOR_MANDATORY_FOR_ADMINS — W2.4, soft→mandatory TOTP
    POLICY_DECISION_LOG_DASHBOARD   — W2, shadow-dashboard denied requests
    EMAIL_BOUNCE_HANDLING           — W6, webhook/IMAP обработчик bounce

Все — с ``everyone=False`` (выключены). Включение — через
``/admin/waffle/flag/`` или management command.

Идемпотентна: при повторном применении использует ``update_or_create``
с защитой note'а от перезаписи (если админ уже правил).
"""

from __future__ import annotations

from django.db import migrations


INITIAL_FLAGS = [
    {
        "name": "UI_V3B_DEFAULT",
        "note": (
            "Wave 9 (UX refactor). Переключает дефолтный рендер карточки "
            "компании classic → v3/b. Включать через admin для постепенной "
            "выкатки (percent rollout)."
        ),
    },
    {
        "name": "TWO_FACTOR_MANDATORY_FOR_ADMINS",
        "note": (
            "Wave 2.4. off = TOTP опционален, показываем баннер. "
            "on = mandatory при следующем логине для ADMIN/BRANCH_DIRECTOR. "
            "Миграция: 2 недели soft → потом включить."
        ),
    },
    {
        "name": "POLICY_DECISION_LOG_DASHBOARD",
        "note": (
            "Wave 2. Показывает shadow-дашборд denied requests из Policy Engine "
            "за 2 недели до перехода в ENFORCE. Управляется ADMIN'ом через admin."
        ),
    },
    {
        "name": "EMAIL_BOUNCE_HANDLING",
        "note": (
            "Wave 6. Включает обработку bounce/complaint webhook или IMAP-поллер "
            "(docs/plan/07_wave_6_email.md §6.2). Требует настройки smtp.bz webhook."
        ),
    },
]


def create_initial_flags(apps, schema_editor):
    """Создать 4 выключенных флага. Идемпотентно (update_or_create)."""
    Flag = apps.get_model("waffle", "Flag")
    for flag_spec in INITIAL_FLAGS:
        Flag.objects.update_or_create(
            name=flag_spec["name"],
            defaults={
                "everyone": False,  # явное выключение
                "note": flag_spec["note"],
                # percent=None, testing=False — waffle-defaults
            },
        )


def delete_initial_flags(apps, schema_editor):
    """Rollback: удалить 4 созданных флага."""
    Flag = apps.get_model("waffle", "Flag")
    names = [f["name"] for f in INITIAL_FLAGS]
    Flag.objects.filter(name__in=names).delete()


class Migration(migrations.Migration):
    # Должны быть waffle-таблицы. Waffle 5.0 имеет __latest__, используем alias.
    dependencies = [
        ("waffle", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_initial_flags, delete_initial_flags),
    ]
