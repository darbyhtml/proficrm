"""
Тесты для accounts.templatetags.accounts_extras (has_role, role_label).
"""
from django.contrib.auth.models import AnonymousUser
from django.test import TestCase
from django.template import Context, Template

from accounts.models import User


class HasRoleFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = User.objects.create_user(
            username="a1", password="pwd12345", role=User.Role.ADMIN
        )
        cls.manager = User.objects.create_user(
            username="m1", password="pwd12345", role=User.Role.MANAGER
        )
        cls.rop = User.objects.create_user(
            username="r1", password="pwd12345", role=User.Role.SALES_HEAD
        )

    def _render(self, user, roles: str) -> str:
        tpl = Template(
            '{% load accounts_extras %}'
            '{% if user|has_role:"' + roles + '" %}YES{% else %}NO{% endif %}'
        )
        return tpl.render(Context({"user": user}))

    def test_admin_matches_admin(self):
        self.assertEqual(self._render(self.admin, "admin"), "YES")

    def test_manager_does_not_match_admin(self):
        self.assertEqual(self._render(self.manager, "admin"), "NO")

    def test_multi_role_match(self):
        self.assertEqual(self._render(self.rop, "sales_head,branch_director"), "YES")

    def test_multi_role_no_match(self):
        self.assertEqual(self._render(self.manager, "sales_head,branch_director"), "NO")

    def test_anonymous_never_matches(self):
        self.assertEqual(self._render(AnonymousUser(), "admin"), "NO")

    def test_none_never_matches(self):
        self.assertEqual(self._render(None, "admin"), "NO")

    def test_superuser_matches_admin_check(self):
        su = User.objects.create_superuser(
            username="su1", password="pwd12345", email="s@e.com"
        )
        self.assertEqual(self._render(su, "admin"), "YES")


class RoleLabelFilterTests(TestCase):
    def _render(self, value) -> str:
        tpl = Template('{% load accounts_extras %}{{ v|role_label }}')
        return tpl.render(Context({"v": value}))

    def test_admin_label(self):
        self.assertEqual(self._render("admin"), "Администратор")

    def test_manager_label(self):
        self.assertEqual(self._render("manager"), "Менеджер")

    def test_empty(self):
        self.assertEqual(self._render(""), "")

    def test_unknown_returns_raw(self):
        self.assertEqual(self._render("wtf_unknown"), "wtf_unknown")
