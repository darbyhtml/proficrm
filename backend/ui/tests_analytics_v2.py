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
    get_branch_director_dashboard,
    get_group_manager_dashboard,
    get_manager_dashboard,
    get_sales_head_dashboard,
    get_tenderist_dashboard,
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
        self.director = User.objects.create_user(
            username="router_director",
            email="r@d.ru",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.branch,
        )
        self.gm = User.objects.create_user(
            username="router_gm",
            email="r@g.ru",
            role=User.Role.GROUP_MANAGER,
        )
        self.tend = User.objects.create_user(
            username="router_tend",
            email="r@t.ru",
            role=User.Role.TENDERIST,
        )

    def test_manager_gets_manager_template(self):
        self.client.force_login(self.mgr)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Моя продуктивность", resp.content.decode("utf-8"))

    def test_sales_head_gets_team_dashboard(self):
        self.client.force_login(self.rop)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Аналитика отдела", resp.content.decode("utf-8"))

    def test_branch_director_gets_branch_dashboard(self):
        self.client.force_login(self.director)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Аналитика подразделения", resp.content.decode("utf-8"))

    def test_group_manager_gets_executive_dashboard(self):
        self.client.force_login(self.gm)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Executive Dashboard", resp.content.decode("utf-8"))

    def test_tenderist_gets_tenderist_dashboard(self):
        self.client.force_login(self.tend)
        resp = self.client.get("/analytics/v2/")
        self.assertEqual(resp.status_code, 200)
        # Специфичный для tenderist заголовок «Обзор».
        self.assertIn("Тендерист", resp.content.decode("utf-8"))

    def test_unauthenticated_redirected_to_login(self):
        resp = self.client.get("/analytics/v2/")
        # login_required → 302 на /login/.
        self.assertEqual(resp.status_code, 302)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class SalesHeadDashboardTests(TestCase):
    def setUp(self):
        self.branch = Branch.objects.create(code="sh", name="Sales Head Branch")
        self.other_branch = Branch.objects.create(code="sh2", name="Other Branch")
        self.rop = User.objects.create_user(
            username="sh_rop", email="rop@x.ru",
            role=User.Role.SALES_HEAD, branch=self.branch,
        )
        self.mgr_a = User.objects.create_user(
            username="sh_a", email="a@x.ru",
            role=User.Role.MANAGER, branch=self.branch, messenger_online=True,
        )
        self.mgr_b = User.objects.create_user(
            username="sh_b", email="b@x.ru",
            role=User.Role.MANAGER, branch=self.branch,
        )
        self.mgr_other = User.objects.create_user(
            username="sh_other", email="o@x.ru",
            role=User.Role.MANAGER, branch=self.other_branch,
        )
        # Выполненные задачи за месяц: mgr_a — 3, mgr_b — 1, mgr_other — 10.
        now = timezone.now()
        for _ in range(3):
            Task.objects.create(assigned_to=self.mgr_a, title="a", status=Task.Status.DONE)
        for _ in range(1):
            Task.objects.create(assigned_to=self.mgr_b, title="b", status=Task.Status.DONE)
        for _ in range(10):
            Task.objects.create(assigned_to=self.mgr_other, title="o", status=Task.Status.DONE)
        # updated_at = auto_now, значит все в пределах «сейчас».

    def test_leaderboard_only_includes_own_branch(self):
        data = get_sales_head_dashboard(self.rop)
        names = {row["username"] for row in data["leaderboard"]}
        self.assertIn("sh_a", names)
        self.assertIn("sh_b", names)
        self.assertNotIn("sh_other", names)  # из чужого подразделения

    def test_leaderboard_ranked_by_done_count(self):
        data = get_sales_head_dashboard(self.rop)
        first = data["leaderboard"][0]
        self.assertEqual(first["username"], "sh_a")  # 3 задачи > 1
        self.assertEqual(first["rank"], 1)

    def test_online_count(self):
        data = get_sales_head_dashboard(self.rop)
        self.assertEqual(data["online"]["online"], 1)  # mgr_a
        self.assertEqual(data["online"]["total"], 2)


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class BranchDirectorDashboardTests(TestCase):
    def test_branches_rank_marks_mine_and_sorts_by_done(self):
        b1 = Branch.objects.create(code="bd1", name="B1")
        b2 = Branch.objects.create(code="bd2", name="B2")
        director = User.objects.create_user(
            username="bd_director", email="d@x.ru",
            role=User.Role.BRANCH_DIRECTOR, branch=b1,
        )
        m1 = User.objects.create_user(username="bd_m1", email="m1@x.ru",
                                       role=User.Role.MANAGER, branch=b1)
        m2 = User.objects.create_user(username="bd_m2", email="m2@x.ru",
                                       role=User.Role.MANAGER, branch=b2)
        # b2 имеет больше выполненных задач.
        Task.objects.create(assigned_to=m1, title="t", status=Task.Status.DONE)
        for _ in range(5):
            Task.objects.create(assigned_to=m2, title="t", status=Task.Status.DONE)

        data = get_branch_director_dashboard(director)
        rank = data["branches_rank"]
        # b2 первым (больше done).
        self.assertEqual(rank[0]["code"], "bd2")
        # Моё подразделение помечено is_mine.
        mine = [r for r in rank if r["is_mine"]][0]
        self.assertEqual(mine["code"], "bd1")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class GroupManagerDashboardTests(TestCase):
    def test_totals_aggregate_across_all_branches(self):
        b1 = Branch.objects.create(code="gm1", name="GM1")
        b2 = Branch.objects.create(code="gm2", name="GM2")
        gm = User.objects.create_user(
            username="gm", email="g@x.ru", role=User.Role.GROUP_MANAGER,
        )
        m1 = User.objects.create_user(username="gm_m1", email="m1@g.ru",
                                       role=User.Role.MANAGER, branch=b1,
                                       messenger_online=True)
        m2 = User.objects.create_user(username="gm_m2", email="m2@g.ru",
                                       role=User.Role.MANAGER, branch=b2)
        Task.objects.create(assigned_to=m1, title="x", status=Task.Status.DONE)
        Task.objects.create(assigned_to=m2, title="y", status=Task.Status.DONE)

        data = get_group_manager_dashboard(gm)
        self.assertEqual(data["totals"]["done_month"], 2)
        self.assertEqual(data["totals"]["online"], 1)
        self.assertEqual(data["totals"]["total_managers"], 2)
        # per_branch содержит оба подразделения.
        codes = {b["code"] for b in data["per_branch"]}
        self.assertEqual(codes, {"gm1", "gm2"})


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class TenderistDashboardTests(TestCase):
    def test_tenderist_counters_populate(self):
        b = Branch.objects.create(code="tb", name="Tendb")
        tend = User.objects.create_user(
            username="tendx", email="t@x.ru", role=User.Role.TENDERIST,
        )
        # 2 компании: одна с истекающим договором.
        today = timezone.localdate()
        Company.objects.create(
            name="C1", branch=b,
            contract_until=today + timedelta(days=15),
        )
        Company.objects.create(name="C2", branch=b)
        data = get_tenderist_dashboard(tend)
        self.assertEqual(data["companies_total"], 2)
        self.assertEqual(data["contracts_with_value"], 1)
        self.assertEqual(data["contracts_expiring_30"], 1)


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
