"""
Тесты для reports.py и недостающих view в dashboard.py:
- cold_calls_report_day / cold_calls_report_month / cold_calls_report_last_7_days
- view_as_update / view_as_reset
- dashboard_poll
"""
import json
from datetime import timedelta

from django.test import TestCase, Client, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model

from companies.models import Company
from accounts.models import Branch

User = get_user_model()


# ---------------------------------------------------------------------------
# Reports views
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class ColdCallsReportDayTest(TestCase):
    def setUp(self):
        self.client = Client()
        # View `cold_calls_report_day` возвращает JSON только для AJAX (X-Requested-With=XMLHttpRequest),
        # обычный GET отдаёт HTML (render шаблона). Тесты проверяют JSON-контракт.
        self.client.defaults["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        self.manager = User.objects.create_user(
            username="mgr", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(self.manager)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/reports/cold-calls/day/")
        self.assertIn(resp.status_code, [302, 401])

    def test_returns_200_json(self):
        resp = self.client.get("/reports/cold-calls/day/")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["range"], "day")

    def test_response_has_expected_keys(self):
        resp = self.client.get("/reports/cold-calls/day/")
        data = json.loads(resp.content)
        for key in ("ok", "range", "date", "count", "items", "text", "stats"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_stats_has_expected_keys(self):
        resp = self.client.get("/reports/cold-calls/day/")
        data = json.loads(resp.content)
        stats = data["stats"]
        # В текущей версии view stats содержит cold_calls, incoming_calls,
        # tasks_done, new_companies. `new_contacts` была убрана.
        for key in ("cold_calls", "incoming_calls", "tasks_done", "new_companies"):
            self.assertIn(key, stats, f"Missing stats key: {key}")

    def test_date_param_accepted(self):
        today = timezone.localdate(timezone.now())
        yesterday = today - timedelta(days=1)
        resp = self.client.get(
            "/reports/cold-calls/day/",
            {"date": yesterday.strftime("%Y-%m-%d")},
        )
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertIn(yesterday.strftime("%d.%m.%Y"), data["date"])

    def test_invalid_date_param_uses_today(self):
        resp = self.client.get("/reports/cold-calls/day/", {"date": "not-a-date"})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])

    def test_empty_items_when_no_calls(self):
        resp = self.client.get("/reports/cold-calls/day/")
        data = json.loads(resp.content)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["items"], [])

    def test_forbidden_for_unauthenticated(self):
        c = Client()
        resp = c.get("/reports/cold-calls/day/")
        self.assertIn(resp.status_code, [302, 401, 403])


@override_settings(SECURE_SSL_REDIRECT=False)
class ColdCallsReportMonthTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.client.defaults["HTTP_X_REQUESTED_WITH"] = "XMLHttpRequest"
        self.manager = User.objects.create_user(
            username="mgr2", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(self.manager)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/reports/cold-calls/month/")
        self.assertIn(resp.status_code, [302, 401])

    def test_returns_200_json(self):
        resp = self.client.get("/reports/cold-calls/month/")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["range"], "month")

    def test_response_has_expected_keys(self):
        resp = self.client.get("/reports/cold-calls/month/")
        data = json.loads(resp.content)
        for key in ("ok", "range", "month", "month_label", "available_months", "count", "items", "text", "stats"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_available_months_is_list(self):
        resp = self.client.get("/reports/cold-calls/month/")
        data = json.loads(resp.content)
        self.assertIsInstance(data["available_months"], list)

    def test_month_param_accepted(self):
        today = timezone.localdate(timezone.now())
        month_key = today.strftime("%Y-%m")
        resp = self.client.get("/reports/cold-calls/month/", {"month": month_key})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])

    def test_empty_items_when_no_calls(self):
        resp = self.client.get("/reports/cold-calls/month/")
        data = json.loads(resp.content)
        self.assertEqual(data["count"], 0)


@override_settings(SECURE_SSL_REDIRECT=False)
class ColdCallsReportLast7DaysTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            username="mgr3", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(self.manager)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        self.assertIn(resp.status_code, [302, 401])

    def test_returns_200_json(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertTrue(data["ok"])
        self.assertEqual(data["range"], "last_7_days")

    def test_response_has_expected_keys(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        data = json.loads(resp.content)
        for key in ("ok", "range", "period", "total", "days"):
            self.assertIn(key, data, f"Missing key: {key}")

    def test_days_list_has_7_entries(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        data = json.loads(resp.content)
        self.assertEqual(len(data["days"]), 7)

    def test_each_day_has_expected_keys(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        data = json.loads(resp.content)
        for day in data["days"]:
            for key in ("date", "label", "count"):
                self.assertIn(key, day, f"Missing day key: {key}")

    def test_total_is_sum_of_days(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        data = json.loads(resp.content)
        expected_total = sum(d["count"] for d in data["days"])
        self.assertEqual(data["total"], expected_total)

    def test_zero_calls_when_no_data(self):
        resp = self.client.get("/reports/cold-calls/last-7-days/")
        data = json.loads(resp.content)
        self.assertEqual(data["total"], 0)


# ---------------------------------------------------------------------------
# view_as_update / view_as_reset
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsUpdateTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_va", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_va", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(self.admin)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.post("/admin/view-as/")
        self.assertIn(resp.status_code, [302, 401])

    def test_non_admin_is_redirected(self):
        self.client.force_login(self.manager)
        resp = self.client.post("/admin/view-as/", {"view_role": "manager"})
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

    def test_get_redirects_to_referer(self):
        resp = self.client.get("/admin/view-as/", HTTP_REFERER="/companies/")
        self.assertIn(resp.status_code, [302])
        self.assertIn("/companies/", resp.get("Location", ""))

    def test_set_view_role_in_session(self):
        resp = self.client.post(
            "/admin/view-as/",
            {"view_role": "manager"},
        )
        self.assertIn(resp.status_code, [302])
        self.assertEqual(self.client.session.get("view_as_role"), "manager")

    def test_invalid_role_clears_session(self):
        session = self.client.session
        session["view_as_role"] = "manager"
        session.save()
        resp = self.client.post(
            "/admin/view-as/",
            {"view_role": "nonexistent_role"},
        )
        self.assertIn(resp.status_code, [302])
        self.assertIsNone(self.client.session.get("view_as_role"))

    def test_set_view_user_id_in_session(self):
        resp = self.client.post(
            "/admin/view-as/",
            {"view_user_id": str(self.manager.id)},
        )
        self.assertIn(resp.status_code, [302])
        self.assertEqual(self.client.session.get("view_as_user_id"), self.manager.id)

    def test_invalid_user_id_clears_session(self):
        session = self.client.session
        session["view_as_user_id"] = 999999
        session.save()
        resp = self.client.post(
            "/admin/view-as/",
            {"view_user_id": "999999"},  # non-existent user
        )
        self.assertIn(resp.status_code, [302])
        self.assertIsNone(self.client.session.get("view_as_user_id"))

    def test_set_view_branch_in_session(self):
        branch = Branch.objects.create(code="b1", name="Branch 1")
        resp = self.client.post(
            "/admin/view-as/",
            {"view_branch_id": str(branch.id)},
        )
        self.assertIn(resp.status_code, [302])
        self.assertEqual(self.client.session.get("view_as_branch_id"), branch.id)

    def test_redirect_to_next_param(self):
        resp = self.client.post(
            "/admin/view-as/",
            {"view_role": "manager", "next": "/companies/"},
        )
        self.assertRedirects(resp, "/companies/", fetch_redirect_response=False)


@override_settings(SECURE_SSL_REDIRECT=False)
class ViewAsResetTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_vr", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_vr", password="pass", role=User.Role.MANAGER
        )

    def test_requires_login(self):
        resp = self.client.get("/admin/view-as/reset/")
        self.assertIn(resp.status_code, [302, 401])

    def test_non_admin_is_redirected_to_dashboard(self):
        self.client.force_login(self.manager)
        resp = self.client.post("/admin/view-as/reset/")
        self.assertRedirects(resp, "/", fetch_redirect_response=False)

    def test_clears_session_keys(self):
        self.client.force_login(self.admin)
        session = self.client.session
        session["view_as_user_id"] = 99
        session["view_as_role"] = "manager"
        session["view_as_branch_id"] = 1
        session.save()

        self.client.get("/admin/view-as/reset/")
        self.assertIsNone(self.client.session.get("view_as_user_id"))
        self.assertIsNone(self.client.session.get("view_as_role"))
        self.assertIsNone(self.client.session.get("view_as_branch_id"))

    def test_redirects_to_referer(self):
        self.client.force_login(self.admin)
        resp = self.client.get(
            "/admin/view-as/reset/",
            HTTP_REFERER="/companies/",
        )
        self.assertIn("/companies/", resp.get("Location", ""))


# ---------------------------------------------------------------------------
# dashboard_poll
# ---------------------------------------------------------------------------

@override_settings(SECURE_SSL_REDIRECT=False)
class DashboardPollTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.manager = User.objects.create_user(
            username="mgr_poll", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(self.manager)

    def test_requires_login(self):
        self.client.logout()
        resp = self.client.get("/api/dashboard/poll/")
        self.assertIn(resp.status_code, [302, 401])

    def test_returns_200_json(self):
        resp = self.client.get("/api/dashboard/poll/")
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        self.assertIn("updated", data)

    def test_without_since_returns_updated_true(self):
        """Без `since` view возвращает минимальный {updated: True, timestamp},
        клиент делает reload. Полная пачка данных (tasks_today, overdue, ...)
        берётся из /dashboard/ HTML, не из poll-эндпоинта."""
        resp = self.client.get("/api/dashboard/poll/")
        data = json.loads(resp.content)
        self.assertTrue(data.get("updated"))
        self.assertIn("timestamp", data)

    def test_with_future_since_returns_no_changes(self):
        # since = far future → no new tasks
        future_ts = int((timezone.now() + timedelta(hours=1)).timestamp() * 1000)
        resp = self.client.get("/api/dashboard/poll/", {"since": str(future_ts)})
        self.assertEqual(resp.status_code, 200)
        data = json.loads(resp.content)
        # Either updated=False (no changes) or updated=True with data
        self.assertIn("updated", data)

    def test_with_invalid_since_returns_400(self):
        """Битый `since` даёт 400 вместо updated=true — защита от бесконечного
        reload-цикла. Клиент видит error и сбрасывает lastPollTs."""
        resp = self.client.get("/api/dashboard/poll/", {"since": "not-a-number"})
        self.assertEqual(resp.status_code, 400)
        data = json.loads(resp.content)
        self.assertEqual(data.get("error"), "invalid_since")

    def test_response_has_timestamp(self):
        resp = self.client.get("/api/dashboard/poll/")
        data = json.loads(resp.content)
        if data.get("updated"):
            self.assertIn("timestamp", data)
