"""UX-4 — Header quick-add dropdown tests.

Scenario B delivered: 2 items (Task modal via V2Modal + Company navigation).
Contact/Note excluded — they are company-scoped и добавляются в карточке
компании.
"""

from __future__ import annotations

from django.test import Client, TestCase

from core.test_utils import make_disposable_user


class HeaderQuickAddTest(TestCase):
    """Quick-add dropdown renders в global header (base.html)."""

    def setUp(self):
        self.admin = make_disposable_user(
            role="admin",
            prefix="ux4_admin",
            is_staff=True,
            is_superuser=True,
        )

    def _get_home_html(self) -> str:
        c = Client()
        c.force_login(self.admin)
        r = c.get("/")
        self.assertEqual(r.status_code, 200, r.content[:500])
        return r.content.decode()

    def test_dropdown_button_present(self):
        html = self._get_home_html()
        self.assertIn('id="quickAddBtn"', html)
        self.assertIn('data-action="toggle-quick-add"', html)
        self.assertIn('aria-haspopup="true"', html)

    def test_dropdown_menu_present(self):
        html = self._get_home_html()
        self.assertIn('id="quickAddMenu"', html)
        # role=menu container
        self.assertIn('role="menu"', html)

    def test_task_menuitem_present(self):
        """Задача item с data-action="quick-add-task" (opens V2Modal)."""
        html = self._get_home_html()
        self.assertIn('data-action="quick-add-task"', html)
        self.assertIn(">Задача<", html)

    def test_company_menuitem_present(self):
        """Компания item — <a href="/companies/new/">."""
        html = self._get_home_html()
        self.assertIn('href="/companies/new/"', html)
        self.assertIn(">Компания<", html)

    def test_v2_modal_included_globally(self):
        """v2_modal.html теперь available от base.html (not per-page)."""
        html = self._get_home_html()
        self.assertIn("v2ModalBackdrop", html)
        self.assertIn("window.V2Modal", html)

    def test_header_quick_add_js_loaded(self):
        """Delegated listener script включён в base."""
        html = self._get_home_html()
        self.assertIn("header_quick_add.js", html)

    def test_accessibility_attrs(self):
        """aria-expanded=false by default; role=menuitem на items."""
        html = self._get_home_html()
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('role="menuitem"', html)
