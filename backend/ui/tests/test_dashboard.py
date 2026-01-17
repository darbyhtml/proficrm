"""
Тесты для dashboard view (Рабочий стол).
Проверка корректности отображения задач, договоров и прав доступа.
"""

from django.test import TestCase, Client, override_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta, date
from tasksapp.models import Task, TaskType
from companies.models import Company

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class DashboardViewTestCase(TestCase):
    """Тесты для dashboard view."""

    def setUp(self):
        """Настройка тестовых данных."""
        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            role=User.Role.MANAGER
        )
        self.client.force_login(self.user)
        self.now = timezone.now()
        self.local_now = timezone.localtime(self.now)
        self.today_date = timezone.localdate(self.now)
        self.today_start = self.local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.tomorrow_start = self.today_start + timedelta(days=1)

        # Создаём тестовую компанию
        self.company = Company.objects.create(
            name="Тестовая компания",
            responsible=self.user
        )

    def test_dashboard_requires_login(self):
        """Тест: dashboard требует авторизации."""
        self.client.logout()
        response = self.client.get("/")
        self.assertIn(response.status_code, [302, 401])  # Редирект на login

    def test_dashboard_renders_successfully(self):
        """Тест: dashboard успешно рендерится."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Рабочий стол")

    def test_dashboard_context_keys_present(self):
        """Тест: все контекстные ключи присутствуют."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertIn("now", context)
        self.assertIn("local_now", context)
        self.assertIn("tasks_new", context)
        self.assertIn("tasks_today", context)
        self.assertIn("overdue", context)
        self.assertIn("tasks_week", context)
        self.assertIn("contracts_soon", context)
        self.assertIn("can_view_cold_call_reports", context)

    def test_tasks_today_displayed(self):
        """Тест: задачи на сегодня отображаются."""
        # Создаём задачу на сегодня
        task = Task.objects.create(
            title="Задача на сегодня",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Задача на сегодня")
        self.assertContains(response, "На сегодня")

    def test_tasks_today_excludes_done_and_cancelled(self):
        """Тест: задачи на сегодня исключают выполненные и отменённые."""
        # Создаём задачу на сегодня со статусом DONE
        Task.objects.create(
            title="Выполненная задача",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.DONE
        )

        # Создаём задачу на сегодня со статусом CANCELLED
        Task.objects.create(
            title="Отменённая задача",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=11),
            status=Task.Status.CANCELLED
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Выполненная задача")
        self.assertNotContains(response, "Отменённая задача")

    def test_overdue_tasks_displayed(self):
        """Тест: просроченные задачи отображаются."""
        # Создаём просроченную задачу
        overdue_time = self.now - timedelta(days=1)
        task = Task.objects.create(
            title="Просроченная задача",
            assigned_to=self.user,
            company=self.company,
            due_at=overdue_time,
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Просроченная задача")
        self.assertContains(response, "Просрочено")

    def test_overdue_tasks_excludes_done_and_cancelled(self):
        """Тест: просроченные задачи исключают выполненные и отменённые."""
        overdue_time = self.now - timedelta(days=1)

        # Создаём просроченную задачу со статусом DONE
        Task.objects.create(
            title="Выполненная просроченная",
            assigned_to=self.user,
            company=self.company,
            due_at=overdue_time,
            status=Task.Status.DONE
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Выполненная просроченная")

    def test_tasks_week_displayed(self):
        """Тест: задачи на неделю отображаются."""
        # Создаём задачу на завтра (входит в неделю)
        week_task_time = self.tomorrow_start + timedelta(days=3)
        task = Task.objects.create(
            title="Задача на неделю",
            assigned_to=self.user,
            company=self.company,
            due_at=week_task_time,
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Задача на неделю")
        self.assertContains(response, "Задачи на неделю")

    def test_tasks_week_excludes_today(self):
        """Тест: задачи на неделю не включают задачи на сегодня."""
        # Создаём задачу на сегодня
        Task.objects.create(
            title="Задача на сегодня",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # Проверяем, что задача на сегодня не в разделе "Задачи на неделю"
        # (она должна быть в разделе "На сегодня")
        context = response.context
        tasks_week_titles = [t.title for t in context["tasks_week"]]
        self.assertNotIn("Задача на сегодня", tasks_week_titles)

    def test_tasks_new_displayed(self):
        """Тест: новые задачи отображаются."""
        task = Task.objects.create(
            title="Новая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новая задача")
        self.assertContains(response, "Новые задачи")

    def test_tasks_new_only_shows_new_status(self):
        """Тест: новые задачи показывают только статус NEW."""
        # Создаём задачу со статусом IN_PROGRESS
        Task.objects.create(
            title="Задача в работе",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.IN_PROGRESS
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        tasks_new_titles = [t.title for t in context["tasks_new"]]
        self.assertNotIn("Задача в работе", tasks_new_titles)

    def test_contracts_soon_displayed(self):
        """Тест: договоры, которые скоро истекают, отображаются."""
        # Создаём компанию с договором, который истекает через 15 дней
        contract_until = self.today_date + timedelta(days=15)
        company = Company.objects.create(
            name="Компания с договором",
            responsible=self.user,
            contract_until=contract_until,
            contract_type=Company.ContractType.FRAME
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Компания с договором")
        self.assertContains(response, "Договоры")

    def test_contracts_soon_only_for_responsible(self):
        """Тест: договоры показываются только для ответственного."""
        other_user = User.objects.create_user(
            username="otheruser",
            password="testpass123"
        )

        contract_until = self.today_date + timedelta(days=15)
        company = Company.objects.create(
            name="Компания другого пользователя",
            responsible=other_user,  # Другой пользователь
            contract_until=contract_until
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract_companies = [item["company"].name for item in context["contracts_soon"]]
        self.assertNotIn("Компания другого пользователя", contract_companies)

    def test_contracts_soon_within_30_days(self):
        """Тест: договоры показываются только если истекают в течение 30 дней."""
        # Создаём компанию с договором, который истекает через 35 дней (не должен показываться)
        contract_until_far = self.today_date + timedelta(days=35)
        Company.objects.create(
            name="Компания с далёким договором",
            responsible=self.user,
            contract_until=contract_until_far
        )

        # Создаём компанию с договором, который истекает через 25 дней (должен показываться)
        contract_until_near = self.today_date + timedelta(days=25)
        Company.objects.create(
            name="Компания с близким договором",
            responsible=self.user,
            contract_until=contract_until_near
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract_companies = [item["company"].name for item in context["contracts_soon"]]
        self.assertNotIn("Компания с далёким договором", contract_companies)
        self.assertIn("Компания с близким договором", contract_companies)

    def test_contracts_soon_level_danger_less_than_14_days(self):
        """Тест: договоры с менее чем 14 днями имеют уровень 'danger'."""
        contract_until = self.today_date + timedelta(days=10)
        Company.objects.create(
            name="Срочный договор",
            responsible=self.user,
            contract_until=contract_until
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract = next((item for item in context["contracts_soon"] if item["company"].name == "Срочный договор"), None)
        self.assertIsNotNone(contract)
        self.assertEqual(contract["level"], "danger")

    def test_contracts_soon_level_warn_14_to_30_days(self):
        """Тест: договоры с 14-30 днями имеют уровень 'warn'."""
        contract_until = self.today_date + timedelta(days=20)
        Company.objects.create(
            name="Договор с предупреждением",
            responsible=self.user,
            contract_until=contract_until
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract = next((item for item in context["contracts_soon"] if item["company"].name == "Договор с предупреждением"), None)
        self.assertIsNotNone(contract)
        self.assertEqual(contract["level"], "warn")

    def test_can_view_cold_call_reports_for_manager(self):
        """Тест: менеджер может видеть отчёты по холодным звонкам."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertTrue(context["can_view_cold_call_reports"])

    def test_can_view_cold_call_reports_for_admin(self):
        """Тест: администратор может видеть отчёты по холодным звонкам."""
        admin_user = User.objects.create_user(
            username="admin",
            password="testpass123",
            role=User.Role.ADMIN
        )
        self.client.force_login(admin_user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertTrue(context["can_view_cold_call_reports"])

    def test_empty_states_displayed(self):
        """Тест: пустые состояния отображаются корректно."""
        # Не создаём никаких задач и договоров
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На сегодня задач нет")
        self.assertContains(response, "Новых задач нет")
        self.assertContains(response, "Нет просроченных задач")
        self.assertContains(response, "На ближайшую неделю задач нет")
        self.assertContains(response, "Ближайших окончаний договоров нет")

    def test_tasks_ordered_by_due_at(self):
        """Тест: задачи на сегодня упорядочены по due_at."""
        # Создаём задачи с разными due_at
        task1 = Task.objects.create(
            title="Задача 1 (позже)",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=15),
            status=Task.Status.NEW
        )
        task2 = Task.objects.create(
            title="Задача 2 (раньше)",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        tasks_today = list(context["tasks_today"])
        self.assertEqual(len(tasks_today), 2)
        # Первая задача должна быть с более ранним due_at
        self.assertEqual(tasks_today[0].title, "Задача 2 (раньше)")
        self.assertEqual(tasks_today[1].title, "Задача 1 (позже)")

    def test_tasks_new_ordered_by_created_at_desc(self):
        """Тест: новые задачи упорядочены по created_at (новые первыми)."""
        # Создаём задачи с разными created_at
        task1 = Task.objects.create(
            title="Старая новая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW
        )
        # Обновляем created_at для первой задачи (делаем её старше)
        Task.objects.filter(id=task1.id).update(created_at=timezone.now() - timedelta(hours=2))

        task2 = Task.objects.create(
            title="Новая новая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        tasks_new = list(context["tasks_new"])
        self.assertEqual(len(tasks_new), 2)
        # Первая задача должна быть более новой
        self.assertEqual(tasks_new[0].title, "Новая новая задача")
        self.assertEqual(tasks_new[1].title, "Старая новая задача")

    def test_overdue_limit_20_tasks(self):
        """Тест: просроченные задачи ограничены 20 элементами."""
        # Создаём 25 просроченных задач
        overdue_time = self.now - timedelta(days=1)
        for i in range(25):
            Task.objects.create(
                title=f"Просроченная задача {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=overdue_time - timedelta(hours=i),
                status=Task.Status.NEW
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(len(context["overdue"]), 20)

    def test_tasks_week_limit_50_tasks(self):
        """Тест: задачи на неделю ограничены 50 элементами."""
        # Создаём 55 задач на неделю
        week_task_time = self.tomorrow_start + timedelta(days=3)
        for i in range(55):
            Task.objects.create(
                title=f"Задача на неделю {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=week_task_time + timedelta(hours=i),
                status=Task.Status.NEW
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(len(context["tasks_week"]), 50)

    def test_tasks_new_limit_20_tasks(self):
        """Тест: новые задачи ограничены 20 элементами."""
        # Создаём 25 новых задач
        for i in range(25):
            Task.objects.create(
                title=f"Новая задача {i}",
                assigned_to=self.user,
                company=self.company,
                status=Task.Status.NEW
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(len(context["tasks_new"]), 20)

    def test_contracts_soon_limit_50_companies(self):
        """Тест: договоры ограничены 50 компаниями."""
        # Создаём 55 компаний с договорами
        for i in range(55):
            contract_until = self.today_date + timedelta(days=15 + i)
            Company.objects.create(
                name=f"Компания {i}",
                responsible=self.user,
                contract_until=contract_until
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertEqual(len(context["contracts_soon"]), 50)

    def test_contracts_soon_excludes_null_contract_until(self):
        """Тест: договоры исключают компании без contract_until."""
        # Создаём компанию без contract_until
        Company.objects.create(
            name="Компания без договора",
            responsible=self.user,
            contract_until=None
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract_companies = [item["company"].name for item in context["contracts_soon"]]
        self.assertNotIn("Компания без договора", contract_companies)
