"""UX-3 — Company list bulk UI render tests.

Entire bulk-transfer feature (template checkboxes + JS fetch preview/confirm
modal + backend endpoints) was already complete since 2026-04. `can_bulk_transfer`
variable computed in view но не передавался в render context → template
`{% if can_bulk_transfer %}` gate всегда falsy → UI полностью не рендерился.

Tests verify fix: admin + eligible roles сейчас видят checkboxes + bulk form.
"""

from __future__ import annotations

from django.test import Client, TestCase

from companies.models import Company
from core.test_utils import make_disposable_user


class CompanyListBulkRenderTest(TestCase):
    """Admin role видит bulk UI на company list."""

    def setUp(self):
        self.admin = make_disposable_user(
            role="admin",
            prefix="ux3_admin",
            is_staff=True,
            is_superuser=True,
        )
        # Seed пара компаний чтобы transfer_targets non-empty и page.object_list truthy
        self.co1 = Company.objects.create(name="UX3 Co 1")
        self.co2 = Company.objects.create(name="UX3 Co 2")
        # Transfer target: manager eligible for bulk transfer
        self.manager = make_disposable_user(role="manager", prefix="ux3_mgr")

    def _get_list_html(self) -> str:
        c = Client()
        c.force_login(self.admin)
        r = c.get("/companies/")
        self.assertEqual(r.status_code, 200, r.content[:500])
        return r.content.decode()

    def test_bulk_form_rendered_for_admin(self):
        """Admin видит bulk form `#v2CompanyBulk`."""
        html = self._get_list_html()
        self.assertIn('id="v2CompanyBulk"', html)
        self.assertIn("Передать выбранные", html)

    def test_select_all_checkbox_rendered(self):
        """Header select-all checkbox `#v2BulkAll` present."""
        html = self._get_list_html()
        self.assertIn('id="v2BulkAll"', html)

    def test_row_checkboxes_rendered(self):
        """Каждая company row имеет checkbox с name=company_ids."""
        html = self._get_list_html()
        # Row checkbox class + form binding
        self.assertIn("v2-bulk-row", html)
        self.assertIn('name="company_ids"', html)
        # Company IDs present в value="..."
        self.assertIn(str(self.co1.id), html)

    def test_bulk_counter_rendered(self):
        """Counter «Выбрано: N» rendered."""
        html = self._get_list_html()
        self.assertIn('id="v2BulkCount"', html)
        self.assertIn("Выбрано:", html)

    def test_bulk_modal_rendered(self):
        """Preview modal markup present."""
        html = self._get_list_html()
        self.assertIn('id="v2BulkModal"', html)

    def test_manager_role_does_not_see_bulk(self):
        """Обычный manager не видит bulk UI (can_bulk_transfer=False для MANAGER)."""
        c = Client()
        c.force_login(self.manager)
        r = c.get("/companies/")
        self.assertEqual(r.status_code, 200)
        html = r.content.decode()
        # Bulk form absent для ordinary manager
        self.assertNotIn('id="v2CompanyBulk"', html)
        self.assertNotIn('id="v2BulkAll"', html)
