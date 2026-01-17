"""
Тесты для передачи компаний между пользователями.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

from accounts.models import Branch, User
from companies.models import Company
from companies.permissions import (
    can_transfer_company,
    get_transfer_targets,
    can_transfer_companies,
)

User = get_user_model()


class CompanyTransferPermissionsTestCase(TestCase):
    """Тесты для проверки прав на передачу компаний."""

    def setUp(self):
        """Создаём тестовые данные."""
        # Филиалы
        self.branch1 = Branch.objects.create(code="ekb", name="Екатеринбург")
        self.branch2 = Branch.objects.create(code="tyumen", name="Тюмень")
        
        # Пользователи
        self.manager1 = User.objects.create_user(
            username="manager1",
            password="test123",
            role=User.Role.MANAGER,
            branch=self.branch1,
            first_name="Менеджер",
            last_name="Один",
        )
        self.manager2 = User.objects.create_user(
            username="manager2",
            password="test123",
            role=User.Role.MANAGER,
            branch=self.branch1,
            first_name="Менеджер",
            last_name="Два",
        )
        self.manager3 = User.objects.create_user(
            username="manager3",
            password="test123",
            role=User.Role.MANAGER,
            branch=self.branch2,
            first_name="Менеджер",
            last_name="Три",
        )
        self.rop1 = User.objects.create_user(
            username="rop1",
            password="test123",
            role=User.Role.SALES_HEAD,
            branch=self.branch1,
            first_name="РОП",
            last_name="Один",
        )
        self.director1 = User.objects.create_user(
            username="director1",
            password="test123",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.branch1,
            first_name="Директор",
            last_name="Один",
        )
        self.group_manager = User.objects.create_user(
            username="group_manager",
            password="test123",
            role=User.Role.GROUP_MANAGER,
            first_name="Управляющий",
            last_name="Группы",
        )
        self.admin = User.objects.create_user(
            username="admin",
            password="test123",
            role=User.Role.ADMIN,
            first_name="Админ",
            last_name="Системы",
        )
        
        # Компании
        self.company1 = Company.objects.create(
            name="Компания 1",
            responsible=self.manager1,
            branch=self.branch1,
        )
        self.company2 = Company.objects.create(
            name="Компания 2",
            responsible=self.manager2,
            branch=self.branch1,
        )
        self.company3 = Company.objects.create(
            name="Компания 3",
            responsible=self.manager3,
            branch=self.branch2,
        )
        self.company_no_resp = Company.objects.create(
            name="Компания без ответственного",
            responsible=None,
            branch=self.branch1,
        )

    def test_manager_can_transfer_own_company(self):
        """Менеджер может передавать только свои компании."""
        self.assertTrue(can_transfer_company(self.manager1, self.company1))
        self.assertFalse(can_transfer_company(self.manager1, self.company2))
        self.assertFalse(can_transfer_company(self.manager1, self.company3))
        self.assertFalse(can_transfer_company(self.manager1, self.company_no_resp))

    def test_rop_can_transfer_companies_in_branch(self):
        """РОП может передавать компании менеджеров своего филиала."""
        # Компании менеджеров своего филиала
        self.assertTrue(can_transfer_company(self.rop1, self.company1))
        self.assertTrue(can_transfer_company(self.rop1, self.company2))
        # Компания менеджера другого филиала
        self.assertFalse(can_transfer_company(self.rop1, self.company3))
        # Компания без ответственного
        self.assertFalse(can_transfer_company(self.rop1, self.company_no_resp))

    def test_director_can_transfer_companies_in_branch(self):
        """Директор филиала может передавать компании менеджеров своего филиала."""
        # Компании менеджеров своего филиала
        self.assertTrue(can_transfer_company(self.director1, self.company1))
        self.assertTrue(can_transfer_company(self.director1, self.company2))
        # Компания менеджера другого филиала
        self.assertFalse(can_transfer_company(self.director1, self.company3))

    def test_group_manager_can_transfer_any_company(self):
        """Управляющий группой компаний может передавать любые компании."""
        self.assertTrue(can_transfer_company(self.group_manager, self.company1))
        self.assertTrue(can_transfer_company(self.group_manager, self.company2))
        self.assertTrue(can_transfer_company(self.group_manager, self.company3))
        self.assertTrue(can_transfer_company(self.group_manager, self.company_no_resp))

    def test_admin_can_transfer_any_company(self):
        """Администратор может передавать любые компании."""
        self.assertTrue(can_transfer_company(self.admin, self.company1))
        self.assertTrue(can_transfer_company(self.admin, self.company2))
        self.assertTrue(can_transfer_company(self.admin, self.company3))
        self.assertTrue(can_transfer_company(self.admin, self.company_no_resp))

    def test_get_transfer_targets_excludes_group_manager_and_admin(self):
        """Список получателей не содержит GROUP_MANAGER и ADMIN."""
        targets = get_transfer_targets(self.manager1)
        target_ids = list(targets.values_list("id", flat=True))
        
        self.assertIn(self.manager1.id, target_ids)
        self.assertIn(self.manager2.id, target_ids)
        self.assertIn(self.rop1.id, target_ids)
        self.assertIn(self.director1.id, target_ids)
        self.assertNotIn(self.group_manager.id, target_ids)
        self.assertNotIn(self.admin.id, target_ids)

    def test_get_transfer_targets_grouped_by_branch(self):
        """Список получателей отсортирован по филиалам."""
        targets = list(get_transfer_targets(self.manager1))
        
        # Проверяем, что сначала идут пользователи из branch1, потом из branch2
        branch1_users = [u for u in targets if u.branch_id == self.branch1.id]
        branch2_users = [u for u in targets if u.branch_id == self.branch2.id]
        
        # Все пользователи branch1 должны идти перед пользователями branch2
        if branch1_users and branch2_users:
            last_branch1_idx = max(i for i, u in enumerate(targets) if u.branch_id == self.branch1.id)
            first_branch2_idx = min(i for i, u in enumerate(targets) if u.branch_id == self.branch2.id)
            self.assertLess(last_branch1_idx, first_branch2_idx)

    def test_can_transfer_companies_bulk_all_allowed(self):
        """Массовая передача: все компании разрешены."""
        result = can_transfer_companies(
            self.manager1,
            [self.company1.id]
        )
        self.assertEqual(len(result["allowed"]), 1)
        self.assertEqual(len(result["forbidden"]), 0)
        self.assertIn(self.company1.id, result["allowed"])

    def test_can_transfer_companies_bulk_some_forbidden(self):
        """Массовая передача: некоторые компании запрещены."""
        result = can_transfer_companies(
            self.manager1,
            [self.company1.id, self.company2.id, self.company3.id]
        )
        # manager1 может передать только company1 (свою)
        self.assertEqual(len(result["allowed"]), 1)
        self.assertEqual(len(result["forbidden"]), 2)
        self.assertIn(self.company1.id, result["allowed"])
        self.assertIn(self.company2.id, [f["id"] for f in result["forbidden"]])
        self.assertIn(self.company3.id, [f["id"] for f in result["forbidden"]])

    def test_can_transfer_companies_bulk_rop_all_in_branch(self):
        """Массовая передача РОП: все компании своего филиала разрешены."""
        result = can_transfer_companies(
            self.rop1,
            [self.company1.id, self.company2.id]
        )
        self.assertEqual(len(result["allowed"]), 2)
        self.assertEqual(len(result["forbidden"]), 0)

    def test_can_transfer_companies_bulk_rop_mixed(self):
        """Массовая передача РОП: смешанный выбор (свой филиал + другой филиал)."""
        result = can_transfer_companies(
            self.rop1,
            [self.company1.id, self.company2.id, self.company3.id]
        )
        # rop1 может передать только компании своего филиала
        self.assertEqual(len(result["allowed"]), 2)
        self.assertEqual(len(result["forbidden"]), 1)
        self.assertIn(self.company1.id, result["allowed"])
        self.assertIn(self.company2.id, result["allowed"])
        self.assertIn(self.company3.id, [f["id"] for f in result["forbidden"]])

    def test_can_transfer_companies_bulk_admin_all_allowed(self):
        """Массовая передача админа: все компании разрешены."""
        result = can_transfer_companies(
            self.admin,
            [self.company1.id, self.company2.id, self.company3.id, self.company_no_resp.id]
        )
        self.assertEqual(len(result["allowed"]), 4)
        self.assertEqual(len(result["forbidden"]), 0)

    def test_can_transfer_companies_forbidden_reason(self):
        """Проверка причин запрета в массовой передаче."""
        result = can_transfer_companies(
            self.manager1,
            [self.company2.id]
        )
        self.assertEqual(len(result["forbidden"]), 1)
        forbidden = result["forbidden"][0]
        self.assertIn("не являетесь ответственным", forbidden["reason"])
