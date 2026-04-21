"""Cold-call reports permission + month / date utilities.

Extracted из backend/ui/views/_base.py в W1.1 refactor.
Used by cold-call analytics views.
"""

from __future__ import annotations

from datetime import date as _date

from django.db.models import F, Q

from accounts.models import User


def _can_view_cold_call_reports(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return bool(
        user.is_superuser
        or user.role
        in (
            User.Role.ADMIN,
            User.Role.GROUP_MANAGER,
            User.Role.BRANCH_DIRECTOR,
            User.Role.SALES_HEAD,
            User.Role.MANAGER,
        )
    )


def _cold_call_confirm_q():
    return Q(
        Q(company__primary_cold_marked_call_id=F("id"))
        | Q(contact__cold_marked_call_id=F("id"))
        | Q(company__phones__cold_marked_call_id=F("id"))
        | Q(contact__phones__cold_marked_call_id=F("id"))
    )


def _month_start(d):
    return d.replace(day=1)


def _add_months(d, delta_months):
    import calendar

    y = d.year
    m = d.month + int(delta_months)
    while m <= 0:
        y -= 1
        m += 12
    while m > 12:
        y += 1
        m -= 12
    return _date(y, m, 1)


def _month_label(d):
    months = {
        1: "Январь",
        2: "Февраль",
        3: "Март",
        4: "Апрель",
        5: "Май",
        6: "Июнь",
        7: "Июль",
        8: "Август",
        9: "Сентябрь",
        10: "Октябрь",
        11: "Ноябрь",
        12: "Декабрь",
    }
    return f"{months.get(d.month, str(d.month))} {d.year}"
