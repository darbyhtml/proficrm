"""
Тесты для accounts.signals.sync_is_staff_with_role.

Проверяют, что is_staff всегда в соответствии с role:
- role=ADMIN → is_staff=True
- role=MANAGER/SALES_HEAD/BRANCH_DIRECTOR/GROUP_MANAGER → is_staff=False
- is_superuser=True → is_staff=True (переопределяет role)
- Смена роли существующего пользователя автоматически пересинхронизирует is_staff
"""
from django.test import TestCase
from django.contrib.auth import get_user_model

User = get_user_model()


class SyncIsStaffSignalTests(TestCase):
    """Проверка автоматической синхронизации is_staff с ролью."""

    def test_new_admin_gets_is_staff_true(self):
        """При создании пользователя с role=ADMIN is_staff становится True."""
        user = User.objects.create_user(
            username="admin1",
            password="pwd12345",
            role=User.Role.ADMIN,
        )
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_new_manager_has_is_staff_false(self):
        """Обычный менеджер не получает is_staff."""
        user = User.objects.create_user(
            username="manager1",
            password="pwd12345",
            role=User.Role.MANAGER,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_new_sales_head_has_is_staff_false(self):
        """РОП (sales_head) не получает is_staff."""
        user = User.objects.create_user(
            username="rop1",
            password="pwd12345",
            role=User.Role.SALES_HEAD,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_new_branch_director_has_is_staff_false(self):
        """Директор филиала не получает is_staff."""
        user = User.objects.create_user(
            username="dir1",
            password="pwd12345",
            role=User.Role.BRANCH_DIRECTOR,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_new_group_manager_has_is_staff_false(self):
        """Управляющий группой не получает is_staff (у него свои права, но не admin UI)."""
        user = User.objects.create_user(
            username="gm1",
            password="pwd12345",
            role=User.Role.GROUP_MANAGER,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_superuser_always_is_staff(self):
        """is_superuser=True всегда означает is_staff=True (даже если role=MANAGER)."""
        user = User.objects.create_superuser(
            username="super1",
            password="pwd12345",
            email="super@example.com",
        )
        # create_superuser ставит role=MANAGER (default), но is_superuser=True
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_role_change_updates_is_staff(self):
        """Смена роли MANAGER → ADMIN поднимает is_staff автоматически."""
        user = User.objects.create_user(
            username="morph1",
            password="pwd12345",
            role=User.Role.MANAGER,
        )
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

        user.role = User.Role.ADMIN
        user.save()
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

    def test_role_downgrade_updates_is_staff(self):
        """Смена ADMIN → MANAGER снимает is_staff автоматически."""
        user = User.objects.create_user(
            username="morph2",
            password="pwd12345",
            role=User.Role.ADMIN,
        )
        user.refresh_from_db()
        self.assertTrue(user.is_staff)

        user.role = User.Role.MANAGER
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff)

    def test_manual_is_staff_override_corrected(self):
        """
        Если кто-то вручную выставит is_staff=True менеджеру и сохранит,
        сигнал должен откатить это обратно. Это защищает от рассинхронизации.
        """
        user = User.objects.create_user(
            username="manual1",
            password="pwd12345",
            role=User.Role.MANAGER,
        )
        user.is_staff = True  # Искусственный конфликт
        user.save()
        user.refresh_from_db()
        self.assertFalse(user.is_staff, "Сигнал должен был откатить is_staff к False")
