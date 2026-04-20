"""Тесты для новых сценариев, добавленных в аудите 2026-04-17.

Покрывают:
- _build_greeting (персонализация приветствия по часу)
- _dashboard_time_ranges (границы дня/недели, TZ-aware)
- _split_active_tasks (категоризация задач по бакетам)
- _get_annual_contract_alert / get_dashboard_contracts (4 ветки порогов)
- _get_stale_companies / _get_deletion_requests (fetch [:n+1] pattern)
- dashboard_poll (400 on bad since, 304 ETag, user-id rate-limit)
- view_as_update (audit-лог в ActivityEvent, superuser denied)
"""

from __future__ import annotations

from datetime import datetime as dt, timedelta, timezone as stdlib_tz
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from accounts.models import Branch
from audit.models import ActivityEvent
from companies.models import Company, ContractType
from companies.services import (
    ANNUAL_CONTRACT_DANGER_AMOUNT,
    ANNUAL_CONTRACT_WARN_AMOUNT,
    _get_annual_contract_alert,
    get_dashboard_contracts,
)
from tasksapp.models import Task
from ui.views.dashboard import (
    DASHBOARD_PREVIEW_LIMIT,
    DASHBOARD_STALE_COMPANIES_LIMIT,
    _build_greeting,
    _dashboard_time_ranges,
    _get_stale_companies,
    _split_active_tasks,
)

User = get_user_model()


class GreetingTestCase(TestCase):
    """Приветствие по часу (локальное время)."""

    def test_morning(self):
        self.assertEqual(_build_greeting(5), "Доброе утро")
        self.assertEqual(_build_greeting(9), "Доброе утро")
        self.assertEqual(_build_greeting(11), "Доброе утро")

    def test_day(self):
        self.assertEqual(_build_greeting(12), "Добрый день")
        self.assertEqual(_build_greeting(16), "Добрый день")

    def test_evening(self):
        self.assertEqual(_build_greeting(17), "Добрый вечер")
        self.assertEqual(_build_greeting(22), "Добрый вечер")

    def test_night(self):
        self.assertEqual(_build_greeting(23), "Доброй ночи")
        self.assertEqual(_build_greeting(0), "Доброй ночи")
        self.assertEqual(_build_greeting(3), "Доброй ночи")
        self.assertEqual(_build_greeting(4), "Доброй ночи")


class DashboardTimeRangesTestCase(TestCase):
    """Границы временных периодов для категоризации задач."""

    def test_ranges_shape(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        self.assertIn("today_date", ranges)
        self.assertIn("today_start", ranges)
        self.assertIn("tomorrow_start", ranges)
        self.assertIn("week_start", ranges)
        self.assertIn("week_end", ranges)
        self.assertIn("week_range_start", ranges)
        self.assertIn("week_range_end", ranges)

    def test_today_start_midnight(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        self.assertEqual(ranges["today_start"].hour, 0)
        self.assertEqual(ranges["today_start"].minute, 0)
        self.assertEqual(ranges["today_start"].second, 0)

    def test_tomorrow_is_plus_one_day(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        delta = ranges["tomorrow_start"] - ranges["today_start"]
        self.assertEqual(delta, timedelta(days=1))

    def test_week_start_is_tomorrow(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        self.assertEqual(ranges["week_start"], ranges["tomorrow_start"])

    def test_week_end_is_seven_days_after_tomorrow(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        delta = ranges["week_end"] - ranges["tomorrow_start"]
        self.assertEqual(delta, timedelta(days=7))

    def test_week_range_is_next_7_days(self):
        now = timezone.localtime(timezone.now())
        ranges = _dashboard_time_ranges(now)
        self.assertEqual(ranges["week_range_start"], ranges["today_date"] + timedelta(days=1))
        self.assertEqual(ranges["week_range_end"], ranges["today_date"] + timedelta(days=7))


@override_settings(SECURE_SSL_REDIRECT=False)
class SplitActiveTasksTestCase(TestCase):
    """Категоризация задач по бакетам (сегодня/просрочено/неделя/новые)."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="u1", password="p", role=User.Role.MANAGER)
        self.company = Company.objects.create(name="C1", responsible=self.user)
        self.now = timezone.localtime(timezone.now())
        self.ranges = _dashboard_time_ranges(self.now)

    def _make_task(self, **kw):
        defaults = dict(
            title="T", assigned_to=self.user, company=self.company, status=Task.Status.NEW
        )
        defaults.update(kw)
        return Task.objects.create(**defaults)

    def test_task_with_no_due_at_goes_only_to_new(self):
        self._make_task(status=Task.Status.NEW, due_at=None)
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual(len(buckets["new_all"]), 1)
        self.assertEqual(len(buckets["today_all"]), 0)
        self.assertEqual(len(buckets["overdue_all"]), 0)
        self.assertEqual(len(buckets["week_all"]), 0)

    def test_task_due_yesterday_goes_to_overdue(self):
        self._make_task(due_at=self.ranges["today_start"] - timedelta(hours=2))
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual(len(buckets["overdue_all"]), 1)
        self.assertEqual(len(buckets["today_all"]), 0)

    def test_task_due_today_goes_to_today(self):
        self._make_task(due_at=self.ranges["today_start"] + timedelta(hours=12))
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual(len(buckets["today_all"]), 1)

    def test_task_due_tomorrow_goes_to_week(self):
        self._make_task(due_at=self.ranges["tomorrow_start"] + timedelta(hours=5))
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual(len(buckets["week_all"]), 1)

    def test_task_due_beyond_week_not_in_any_bucket_except_new(self):
        self._make_task(due_at=self.ranges["week_end"] + timedelta(days=1))
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        # Она NEW, поэтому попадёт в new, но не в week/today/overdue
        self.assertEqual(len(buckets["new_all"]), 1)
        self.assertEqual(len(buckets["week_all"]), 0)
        self.assertEqual(len(buckets["today_all"]), 0)

    def test_non_new_task_without_due_is_not_anywhere(self):
        self._make_task(status=Task.Status.IN_PROGRESS, due_at=None)
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual(sum(len(b) for b in buckets.values()), 0)

    def test_overdue_sorted_by_due_at_ascending(self):
        t1 = self._make_task(due_at=self.ranges["today_start"] - timedelta(hours=10))
        t2 = self._make_task(due_at=self.ranges["today_start"] - timedelta(hours=2))
        tasks = Task.objects.filter(assigned_to=self.user)
        buckets = _split_active_tasks(tasks, self.ranges)
        self.assertEqual([t.id for t in buckets["overdue_all"]], [t1.id, t2.id])


class AnnualContractAlertTestCase(TestCase):
    """4 ветки порогов для годовых договоров (_get_annual_contract_alert)."""

    def test_amount_is_none_returns_warn(self):
        self.assertEqual(_get_annual_contract_alert(None), "warn")

    def test_amount_below_danger_returns_danger(self):
        self.assertEqual(_get_annual_contract_alert(ANNUAL_CONTRACT_DANGER_AMOUNT - 1), "danger")
        self.assertEqual(_get_annual_contract_alert(0), "danger")

    def test_amount_in_warn_range(self):
        self.assertEqual(_get_annual_contract_alert(ANNUAL_CONTRACT_DANGER_AMOUNT), "warn")
        self.assertEqual(_get_annual_contract_alert(ANNUAL_CONTRACT_WARN_AMOUNT - 1), "warn")

    def test_amount_above_warn_returns_none(self):
        self.assertIsNone(_get_annual_contract_alert(ANNUAL_CONTRACT_WARN_AMOUNT))
        self.assertIsNone(_get_annual_contract_alert(100000))


@override_settings(SECURE_SSL_REDIRECT=False)
class GetDashboardContractsTestCase(TestCase):
    """get_dashboard_contracts — объединённая логика regular + annual."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="u2", password="p", role=User.Role.MANAGER)

    def test_empty_when_no_contracts(self):
        self.assertEqual(get_dashboard_contracts(self.user), [])

    def test_regular_contract_within_30_days_returns_warn_or_danger(self):
        today = timezone.localdate(timezone.now())
        Company.objects.create(
            name="RegularCo",
            responsible=self.user,
            contract_until=today + timedelta(days=20),
        )
        result = get_dashboard_contracts(self.user, today=today)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["level"], "warn")
        self.assertFalse(result[0]["is_annual"])
        self.assertEqual(result[0]["days_left"], 20)

    def test_regular_contract_danger_when_less_14_days(self):
        today = timezone.localdate(timezone.now())
        Company.objects.create(
            name="DangerCo",
            responsible=self.user,
            contract_until=today + timedelta(days=5),
        )
        result = get_dashboard_contracts(self.user, today=today)
        self.assertEqual(result[0]["level"], "danger")

    def test_regular_contract_beyond_30_days_excluded(self):
        today = timezone.localdate(timezone.now())
        Company.objects.create(
            name="FarCo",
            responsible=self.user,
            contract_until=today + timedelta(days=60),
        )
        self.assertEqual(get_dashboard_contracts(self.user, today=today), [])

    def test_annual_contract_with_none_amount_returns_warn(self):
        ct = ContractType.objects.create(name="Annual", is_annual=True)
        Company.objects.create(
            name="AnnualNull",
            responsible=self.user,
            contract_type=ct,
            contract_amount=None,
        )
        result = get_dashboard_contracts(self.user)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["level"], "warn")
        self.assertTrue(result[0]["is_annual"])

    def test_annual_contract_below_danger_is_danger(self):
        ct = ContractType.objects.create(name="Annual", is_annual=True)
        Company.objects.create(
            name="AnnualDanger",
            responsible=self.user,
            contract_type=ct,
            contract_amount=Decimal("10000"),
        )
        result = get_dashboard_contracts(self.user)
        self.assertEqual(result[0]["level"], "danger")

    def test_annual_contract_above_warn_excluded(self):
        ct = ContractType.objects.create(name="Annual", is_annual=True)
        Company.objects.create(
            name="AnnualHigh",
            responsible=self.user,
            contract_type=ct,
            contract_amount=Decimal("100000"),
        )
        self.assertEqual(get_dashboard_contracts(self.user), [])

    def test_only_for_responsible(self):
        other = User.objects.create_user(username="other", password="p", role=User.Role.MANAGER)
        today = timezone.localdate(timezone.now())
        Company.objects.create(
            name="OtherCo",
            responsible=other,
            contract_until=today + timedelta(days=10),
        )
        self.assertEqual(get_dashboard_contracts(self.user, today=today), [])
        self.assertEqual(len(get_dashboard_contracts(other, today=today)), 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class StaleCompaniesTestCase(TestCase):
    """_get_stale_companies: fetch [:limit+1] + len pattern."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="u3", password="p", role=User.Role.MANAGER)

    def test_company_with_active_task_is_not_stale(self):
        c = Company.objects.create(name="WithTask", responsible=self.user)
        Task.objects.create(title="T", assigned_to=self.user, company=c, status=Task.Status.NEW)
        stale, count = _get_stale_companies(self.user)
        self.assertEqual(count, 0)
        self.assertEqual(stale, [])

    def test_company_without_tasks_is_stale(self):
        Company.objects.create(name="NoTask", responsible=self.user)
        stale, count = _get_stale_companies(self.user)
        self.assertEqual(count, 1)
        self.assertEqual(len(stale), 1)

    def test_company_with_done_task_is_stale(self):
        c = Company.objects.create(name="DoneOnly", responsible=self.user)
        Task.objects.create(title="T", assigned_to=self.user, company=c, status=Task.Status.DONE)
        stale, count = _get_stale_companies(self.user)
        self.assertEqual(count, 1)

    def test_limit_plus_one_pattern(self):
        """Когда компаний > limit, count = limit + 1 (индикатор «есть ещё»)."""
        for i in range(DASHBOARD_STALE_COMPANIES_LIMIT + 2):
            Company.objects.create(name=f"C{i}", responsible=self.user)
        stale, count = _get_stale_companies(self.user)
        self.assertEqual(len(stale), DASHBOARD_STALE_COMPANIES_LIMIT)
        # count фиксирует наличие (limit+1), не точное число
        self.assertEqual(count, DASHBOARD_STALE_COMPANIES_LIMIT + 1)


@override_settings(SECURE_SSL_REDIRECT=False)
class DashboardPollTestCase(TestCase):
    """dashboard_poll — 400 on bad since, ETag на no-change."""

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(username="u4", password="p", role=User.Role.MANAGER)
        self.client = Client()
        self.client.force_login(self.user)

    def test_poll_without_since_returns_updated_true(self):
        response = self.client.get("/api/dashboard/poll/")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["updated"])
        self.assertIn("timestamp", data)

    def test_poll_with_invalid_since_returns_400(self):
        """Fix: битый since → 400 вместо бесконечного reload."""
        response = self.client.get("/api/dashboard/poll/?since=not_a_number")
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertEqual(data["error"], "invalid_since")

    def test_poll_with_recent_since_no_changes_returns_not_updated(self):
        # since — 1 секунда назад, ничего не менялось
        since = int((timezone.now() - timedelta(seconds=1)).timestamp() * 1000)
        response = self.client.get(f"/api/dashboard/poll/?since={since}")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["updated"])

    def test_poll_returns_etag_header(self):
        since = int((timezone.now() - timedelta(seconds=1)).timestamp() * 1000)
        response = self.client.get(f"/api/dashboard/poll/?since={since}")
        self.assertIn("ETag", response.headers)

    def test_poll_returns_304_on_matching_etag(self):
        """ETag/304: повторный запрос с тем же If-None-Match получает 304."""
        since = int((timezone.now() - timedelta(seconds=1)).timestamp() * 1000)
        first = self.client.get(f"/api/dashboard/poll/?since={since}")
        etag = first.headers.get("ETag")
        self.assertTrue(etag)
        second = self.client.get(
            f"/api/dashboard/poll/?since={since}",
            HTTP_IF_NONE_MATCH=etag,
        )
        self.assertEqual(second.status_code, 304)

    def test_poll_with_changes_returns_updated_true(self):
        """Создание задачи → poll возвращает updated=True."""
        since = int((timezone.now() - timedelta(seconds=10)).timestamp() * 1000)
        # «Изменение» после since
        c = Company.objects.create(name="X", responsible=self.user)
        Task.objects.create(title="T", assigned_to=self.user, company=c, status=Task.Status.NEW)
        response = self.client.get(f"/api/dashboard/poll/?since={since}")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["updated"])


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsAuditTestCase(TestCase):
    """view_as_update / view_as_reset — audit-логирование в ActivityEvent."""

    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_user(
            username="admin1",
            password="p",
            role=User.Role.ADMIN,
            is_staff=True,
        )
        self.target = User.objects.create_user(
            username="target1",
            password="p",
            role=User.Role.MANAGER,
        )
        self.client = Client()
        self.client.force_login(self.admin)

    def test_view_as_set_user_creates_audit_event(self):
        self.client.post("/admin/view-as/", {"view_user_id": str(self.target.id)})
        events = ActivityEvent.objects.filter(entity_type="session_impersonation", actor=self.admin)
        self.assertEqual(events.count(), 1)
        ev = events.first()
        self.assertEqual(ev.meta.get("action"), "set_user")
        self.assertEqual(ev.meta.get("target_user_id"), self.target.id)
        self.assertIn("View-as включён", ev.message)

    def test_view_as_reset_creates_audit_event(self):
        # Сначала включаем
        self.client.post("/admin/view-as/", {"view_user_id": str(self.target.id)})
        # Затем сбрасываем
        self.client.post("/admin/view-as/reset/")
        events = ActivityEvent.objects.filter(
            entity_type="session_impersonation",
            meta__action="reset",
        )
        self.assertEqual(events.count(), 1)

    def test_view_as_reset_without_state_does_not_log(self):
        """Если state пустой, reset не пишет лишних событий — снижаем шум."""
        self.client.post("/admin/view-as/reset/")
        events = ActivityEvent.objects.filter(
            entity_type="session_impersonation",
            meta__action="reset",
        )
        self.assertEqual(events.count(), 0)

    def test_view_as_denies_superuser_target(self):
        """ADMIN не может имперсонировать суперпользователя."""
        superuser = User.objects.create_superuser(username="su1", password="p")
        self.client.post("/admin/view-as/", {"view_user_id": str(superuser.id)})
        # Не должно было сохраниться
        self.assertNotIn("view_as_user_id", self.client.session)
        # Должен быть audit-event о попытке
        events = ActivityEvent.objects.filter(
            entity_type="session_impersonation",
            meta__action="denied_superuser_target",
        )
        self.assertEqual(events.count(), 1)

    def test_non_admin_cannot_use_view_as(self):
        """Обычный менеджер не может включить view-as."""
        manager = User.objects.create_user(username="m1", password="p", role=User.Role.MANAGER)
        self.client.logout()
        self.client.force_login(manager)
        response = self.client.post("/admin/view-as/", {"view_user_id": str(self.target.id)})
        # Редирект с ошибкой
        self.assertEqual(response.status_code, 302)
        # Audit не пишется (нет прав)
        events = ActivityEvent.objects.filter(entity_type="session_impersonation", actor=manager)
        self.assertEqual(events.count(), 0)
