"""F7 R1 tests (2026-04-18): ролевой дашборд MANAGER + router.

Проверяем:
- analytics_service.get_manager_dashboard() корректно считает метрики.
- /analytics/v2/ рендерит manager.html для MANAGER и stub.html для прочих.
- Период: day / week / month.
- % задач в срок.
- Workload (компании с активными задачами / без).
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.utils import timezone

from accounts.models import Branch
from companies.models import Company
from tasksapp.models import Task
from ui.analytics_service import (
    get_manager_dashboard,
    period_this_month,
    period_this_week,
    period_today,
)

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class ManagerDashboardServiceTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="analytics", name="Analytics")
        self.mgr = User.objects.create_user(
            username="mgr_analytics",
            email="m@a.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )

    def _make_task(self, status=Task.Status.DONE, on_time=True, updated_delta_days=0):
        """Создаёт задачу менеджера с контролируемыми updated_at и due_at."""
        now = timezone.now()
        updated = now - timedelta(days=updated_delta_days)
        due = updated + timedelta(days=1) if on_time else updated - timedelta(days=1)
        t = Task.objects.create(
            assigned_to=self.mgr,
            title="Test",
            status=status,
            due_at=due,
        )
        # updated_at — auto_now; обойдём через update.
        Task.objects.filter(pk=t.pk).update(updated_at=updated)
        return t

    def test_tasks_counters_today_week_month(self):
        # Все дельты < текущего дня месяца, чтобы гарантированно попасть
        # в текущий месяц независимо от даты запуска теста.
        # Для today: 0 дней назад. Для week: 0/2/3. Для month: все.
        today_day = timezone.localdate().day
        # Cap на 2/3 — точно в неделе, если сегодня вторник+.
        safe_month_delta = max(1, min(today_day - 1, 25))

        self._make_task(updated_delta_days=0)
        self._make_task(updated_delta_days=2)
        self._make_task(updated_delta_days=3)
        self._make_task(updated_delta_days=safe_month_delta)

        data = get_manager_dashboard(self.mgr)
        # today: минимум 1 (тот что 0 дней назад)
        self.assertGreaterEqual(data["tasks"]["today"], 1)
        # month: ровно 4 (все попали в этот месяц при safe_month_delta).
        self.assertEqual(data["tasks"]["month"], 4)
        # week: не больше чем month
        self.assertLessEqual(data["tasks"]["week"], data["tasks"]["month"])

    def test_on_time_ratio_all_on_time_gives_100(self):
        for _ in range(4):
            self._make_task(on_time=True, updated_delta_days=2)
        data = get_manager_dashboard(self.mgr)
        self.assertEqual(data["on_time"]["total"], 4)
        self.assertEqual(data["on_time"]["on_time"], 4)
        self.assertEqual(data["on_time"]["ratio"], 100)

    def test_on_time_ratio_half_late(self):
        for _ in range(2):
            self._make_task(on_time=True, updated_delta_days=2)
        for _ in range(2):
            self._make_task(on_time=False, updated_delta_days=2)
        data = get_manager_dashboard(self.mgr)
        self.assertEqual(data["on_time"]["total"], 4)
        self.assertEqual(data["on_time"]["on_time"], 2)
        self.assertEqual(data["on_time"]["ratio"], 50)

    def test_on_time_ratio_none_when_empty(self):
        data = get_manager_dashboard(self.mgr)
        self.assertEqual(data["on_time"]["total"], 0)
        self.assertIsNone(data["on_time"]["ratio"])

    def test_workload_separates_active_and_idle(self):
        # 3 компании менеджера: 2 с активной задачей (NEW/IN_PROGRESS), 1 без.
        c1 = Company.objects.create(name="C1", responsible=self.mgr, branch=self.branch)
        c2 = Company.objects.create(name="C2", responsible=self.mgr, branch=self.branch)
        Company.objects.create(name="C3", responsible=self.mgr, branch=self.branch)
        Task.objects.create(assigned_to=self.mgr, title="t1", status=Task.Status.NEW, company=c1)
        Task.objects.create(assigned_to=self.mgr, title="t2", status=Task.Status.IN_PROGRESS, company=c2)

        data = get_manager_dashboard(self.mgr)
        self.assertEqual(data["workload"]["total"], 3)
        self.assertEqual(data["workload"]["with_active_tasks"], 2)
        self.assertEqual(data["workload"]["idle"], 1)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class AnalyticsV2RouterTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="router", name="Router")
        self.mgr = User.objects.create_user(
            username="router_mgr",
            email="r@m.ru",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.rop = User.objects.create_user(
            username="router_rop",
            email="r@r.ru",
            role=User.Role.SALES_HEAD,
            branch=self.branch,
        )

    def test_manager_gets_manager_template(self):
        self.client.force_login(self.mgr)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Моя продуктивность", resp.content.decode("utf-8"))

    def test_non_manager_gets_stub(self):
        self.client.force_login(self.rop)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Ваш дашборд в разработке", resp.content.decode("utf-8"))

    def test_unauthenticated_redirected_to_login(self):
        resp = self.client.get("/analytics/v2/")
        # login_required → 302 на /login/.
        self.assertEqual(resp.status_code, 302)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class PeriodHelpersTests(TestCase):
    def test_period_today_has_24h_span(self):
        p = period_today()
        self.assertEqual((p.end - p.start).days, 1)

    def test_period_week_starts_on_monday(self):
        p = period_this_week()
        self.assertEqual(p.start.weekday(), 0)  # monday

    def test_period_month_starts_on_first(self):
        p = period_this_month()
        self.assertEqual(p.start.day, 1)
        self.assertEqual(p.end.day, 1)  # next month's first
