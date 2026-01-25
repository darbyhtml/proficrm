# -*- coding: utf-8 -*-
"""
Тесты импорта из amoCRM: run_id, блокировка параллельного импорта,
progress API (active_run: null при отсутствии активного), один менеджер.
"""
import re
from unittest.mock import patch

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.core.cache import cache

from amocrm.migrate import AmoMigrateResult
from ui.models import AmoApiConfig

User = get_user_model()


def _get_csrf(client):
    r = client.get("/settings/amocrm/migrate/")
    if r.status_code != 200:
        return ""
    m = re.search(r'name="csrfmiddlewaretoken" value="([^"]+)"', r.content.decode())
    return m.group(1) if m else ""


# Минимальный результат миграции для моков
def _make_result(companies_matched=5, companies_next_offset=5, companies_has_more=False):
    r = AmoMigrateResult(
        preview=[],
        tasks_preview=[],
        notes_preview=[],
        contacts_preview=[],
        companies_updates_preview=None,
        contacts_updates_preview=None,
    )
    r.companies_seen = 10
    r.companies_matched = companies_matched
    r.companies_batch = 5
    r.companies_next_offset = companies_next_offset
    r.companies_has_more = companies_has_more
    r.companies_created = 2
    r.companies_updated = 1
    return r


@override_settings(SECURE_SSL_REDIRECT=False)
class AmocrmMigrateViewTestCase(TestCase):
    def setUp(self):
        cache.clear()
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_amocrm",
            password="testpass123",
            role=User.Role.ADMIN,
        )
        self.client.force_login(self.admin)
        cfg = AmoApiConfig.load()
        cfg.domain = "test.amocrm.ru"
        cfg.long_lived_token = "test_token"
        cfg.save()

    def _migrate_patchers(self, result=None):
        if result is None:
            result = _make_result()
        return (
            patch("ui.views.fetch_amo_users", return_value=[{"id": 1, "name": "M1"}, {"id": 2, "name": "M2"}]),
            patch("ui.views.fetch_company_custom_fields", return_value=[]),
            patch("ui.views.migrate_filtered", return_value=result),
        )

    def test_progress_active_run_null_when_no_import(self):
        """GET progress: active_run: null, если активного импорта нет."""
        r = self.client.get("/settings/amocrm/migrate/progress/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("active_run", data)
        self.assertIsNone(data["active_run"])

    def _run_key(self):
        return f"amocrm_import_run:{self.admin.id}"

    def test_progress_returns_active_run_when_lock_set(self):
        """GET progress: active_run с run_id при установленной блокировке (ключ per-user)."""
        import json
        cache.set(self._run_key(), json.dumps({"run_id": "abc-123", "status": "running", "started_at": "2025-01-01T12:00:00"}), timeout=60)
        r = self.client.get("/settings/amocrm/migrate/progress/")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIsNotNone(data.get("active_run"))
        self.assertEqual(data["active_run"]["run_id"], "abc-123")
        self.assertEqual(data["active_run"]["status"], "running")
        cache.delete(self._run_key())

    def test_progress_done_not_active(self):
        """GET progress: статус done/failed не считается активным → active_run: null (и self-clean ключа)."""
        import json
        cache.set(self._run_key(), json.dumps({"run_id": "x", "status": "done"}), timeout=60)
        r = self.client.get("/settings/amocrm/migrate/progress/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json().get("active_run"))
        self.assertIsNone(cache.get(self._run_key()))  # self-clean удалил

    def test_progress_null_when_key_expired_or_deleted(self):
        """Lock/ключ исчёк по TTL или удалён (падение до finally): progress → active_run: null."""
        import json
        cache.set(self._run_key(), json.dumps({"run_id": "z", "status": "running"}), timeout=60)
        cache.delete(self._run_key())  # эмуляция TTL или падения до finally
        r = self.client.get("/settings/amocrm/migrate/progress/")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json().get("active_run"))

    def test_start_import_done_then_progress_null(self):
        """Старт импорта → по завершении progress возвращает active_run: null."""
        with self._migrate_patchers():
            csrf = _get_csrf(self.client)
            post = {
                "csrfmiddlewaretoken": csrf,
                "responsible_user_id": "1",
                "offset": "0",
                "dry_run": "on",
                "limit_companies": "10",
                "migrate_all_companies": "on",
                "import_tasks": "on",
                "import_notes": "on",
                "import_contacts": "",
            }
            r = self.client.post("/settings/amocrm/migrate/", post)
        self.assertEqual(r.status_code, 200)
        prog = self.client.get("/settings/amocrm/migrate/progress/").json()
        self.assertIsNone(prog.get("active_run"))

    def test_new_import_after_done_new_run_id_progress_from_zero(self):
        """Новый импорт после done: новый run_id, прогресс с 0 (отдельный запуск)."""
        with self._migrate_patchers(_make_result(companies_matched=3, companies_next_offset=3, companies_has_more=False)):
            csrf = _get_csrf(self.client)
            post = {
                "csrfmiddlewaretoken": csrf,
                "responsible_user_id": "1",
                "offset": "0",
                "dry_run": "on",
                "limit_companies": "10",
                "migrate_all_companies": "on",
                "import_tasks": "on",
                "import_notes": "on",
                "import_contacts": "",
            }
            r1 = self.client.post("/settings/amocrm/migrate/", post)
        self.assertEqual(r1.status_code, 200)
        self.assertIn("run_id", r1.context or {})
        with self._migrate_patchers(_make_result(companies_matched=2, companies_next_offset=2, companies_has_more=False)):
            csrf2 = _get_csrf(self.client)
            post2 = {"csrfmiddlewaretoken": csrf2, "responsible_user_id": "2", "offset": "0", "dry_run": "on",
                     "limit_companies": "10", "migrate_all_companies": "on", "import_tasks": "on", "import_notes": "on", "import_contacts": ""}
            r2 = self.client.post("/settings/amocrm/migrate/", post2)
        self.assertEqual(r2.status_code, 200)
        self.assertIsNotNone(r2.context.get("result"))
        self.assertEqual(r2.context["result"].companies_matched, 2)
        self.assertEqual(r2.context["result"].companies_next_offset, 2)

    def test_import_already_running_rejected(self):
        """При активном импорте (свой ключ per-user) второй старт — «Импорт уже выполняется»."""
        import json
        cache.set(self._run_key(), json.dumps({"run_id": "lock", "status": "running"}), timeout=3600)
        with self._migrate_patchers() as mocks:
            csrf = _get_csrf(self.client)
            post = {
                "csrfmiddlewaretoken": csrf,
                "responsible_user_id": "1",
                "offset": "0",
                "dry_run": "on",
                "limit_companies": "10",
                "migrate_all_companies": "on",
                "import_tasks": "on",
                "import_notes": "on",
                "import_contacts": "",
            }
            r = self.client.post("/settings/amocrm/migrate/", post)
        self.assertEqual(r.status_code, 200)
        self.assertFalse(mocks[2].called)
        self.assertContains(r, "Импорт уже выполняется")
        cache.delete(self._run_key())

    def test_multiple_managers_rejected(self):
        """При передаче нескольких responsible_user_id — ошибка «Выберите только одного менеджера», migrate не вызывается."""
        from urllib.parse import urlencode
        with self._migrate_patchers() as mocks:
            csrf = _get_csrf(self.client)
            # Два значения для одного ключа: getlist вернёт [1, 2]
            body = urlencode([
                ("csrfmiddlewaretoken", csrf),
                ("responsible_user_id", "1"), ("responsible_user_id", "2"),
                ("offset", "0"), ("dry_run", "on"), ("limit_companies", "10"),
                ("migrate_all_companies", "on"), ("import_tasks", "on"), ("import_notes", "on"), ("import_contacts", ""),
                ("custom_field_id", ""), ("custom_value_label", ""), ("custom_value_enum_id", ""),
            ])
            r = self.client.post(
                "/settings/amocrm/migrate/",
                body,
                content_type="application/x-www-form-urlencoded",
            )
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Выберите только одного менеджера")
        self.assertFalse(mocks[2].called)

    def test_single_manager_accepts_one_id(self):
        """Один менеджер: запрос с одним responsible_user_id проходит, migrate_filtered вызывается."""
        with self._migrate_patchers() as mocks:
            csrf = _get_csrf(self.client)
            post = {
                "csrfmiddlewaretoken": csrf,
                "responsible_user_id": "1",
                "offset": "0",
                "dry_run": "on",
                "limit_companies": "10",
                "migrate_all_companies": "on",
                "import_tasks": "on",
                "import_notes": "on",
                "import_contacts": "",
            }
            r = self.client.post("/settings/amocrm/migrate/", post)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(mocks[2].called)
        self.assertEqual(mocks[2].call_args[1]["responsible_user_id"], 1)
        self.assertIsNotNone(r.context.get("result"))
        self.assertIsNotNone(r.context.get("migrate_responsible_user_id"))
        self.assertEqual(r.context["migrate_responsible_user_id"], 1)
