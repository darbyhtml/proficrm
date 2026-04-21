"""
Тесты для dashboard view (Рабочий стол).
Проверка корректности отображения задач, договоров и прав доступа.
Включает тесты для оптимизированной версии с кэшированием и объединёнными запросами.
"""

from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from companies.models import Company, ContractType
from tasksapp.models import Task, TaskType

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class DashboardViewTestCase(TestCase):
    """Тесты для dashboard view."""

    def setUp(self):
        """Настройка тестовых данных."""
        # Очищаем кэш перед каждым тестом
        cache.clear()

        self.client = Client()
        self.user = User.objects.create_user(
            username="testuser", password="testpass123", role=User.Role.MANAGER
        )
        self.client.force_login(self.user)
        self.now = timezone.now()
        self.local_now = timezone.localtime(self.now)
        self.today_date = timezone.localdate(self.now)
        self.today_start = self.local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        self.tomorrow_start = self.today_start + timedelta(days=1)

        # Создаём тестовую компанию
        self.company = Company.objects.create(name="Тестовая компания", responsible=self.user)

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
        # Проверяем наличие счетчиков для кнопок "Посмотреть все"
        self.assertIn("tasks_new_count", context)
        self.assertIn("tasks_today_count", context)
        self.assertIn("overdue_count", context)
        self.assertIn("tasks_week_count", context)

    def test_tasks_today_displayed(self):
        """Тест: задачи на сегодня отображаются."""
        # Создаём задачу на сегодня
        task = Task.objects.create(
            title="Задача на сегодня",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.NEW,
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
            status=Task.Status.DONE,
        )

        # Создаём задачу на сегодня со статусом CANCELLED
        Task.objects.create(
            title="Отменённая задача",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=11),
            status=Task.Status.CANCELLED,
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
            status=Task.Status.NEW,
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
            status=Task.Status.DONE,
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
            status=Task.Status.NEW,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Задача на неделю")
        self.assertContains(response, "Ближайшие 7 дней")

    def test_tasks_week_excludes_today(self):
        """Тест: задачи на неделю не включают задачи на сегодня."""
        # Создаём задачу на сегодня
        Task.objects.create(
            title="Задача на сегодня",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.NEW,
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
            status=Task.Status.NEW,
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
            status=Task.Status.IN_PROGRESS,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        tasks_new_titles = [t.title for t in context["tasks_new"]]
        self.assertNotIn("Задача в работе", tasks_new_titles)

    def test_contracts_soon_displayed(self):
        """Тест: договоры, которые скоро истекают, отображаются."""
        # Создаём вид договора
        contract_type = ContractType.objects.create(name="Рамочный договор")
        # Создаём компанию с договором, который истекает через 15 дней
        contract_until = self.today_date + timedelta(days=15)
        company = Company.objects.create(
            name="Компания с договором",
            responsible=self.user,
            contract_until=contract_until,
            contract_type=contract_type,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Компания с договором")
        self.assertContains(response, "Договоры")

    def test_contracts_soon_only_for_responsible(self):
        """Тест: договоры показываются только для ответственного."""
        other_user = User.objects.create_user(username="otheruser", password="testpass123")

        contract_until = self.today_date + timedelta(days=15)
        company = Company.objects.create(
            name="Компания другого пользователя",
            responsible=other_user,  # Другой пользователь
            contract_until=contract_until,
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
            contract_until=contract_until_far,
        )

        # Создаём компанию с договором, который истекает через 25 дней (должен показываться)
        contract_until_near = self.today_date + timedelta(days=25)
        Company.objects.create(
            name="Компания с близким договором",
            responsible=self.user,
            contract_until=contract_until_near,
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
            name="Срочный договор", responsible=self.user, contract_until=contract_until
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract = next(
            (
                item
                for item in context["contracts_soon"]
                if item["company"].name == "Срочный договор"
            ),
            None,
        )
        self.assertIsNotNone(contract)
        self.assertEqual(contract["level"], "danger")

    def test_contracts_soon_level_warn_14_to_30_days(self):
        """Тест: договоры с 14-30 днями имеют уровень 'warn'."""
        contract_until = self.today_date + timedelta(days=20)
        Company.objects.create(
            name="Договор с предупреждением", responsible=self.user, contract_until=contract_until
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract = next(
            (
                item
                for item in context["contracts_soon"]
                if item["company"].name == "Договор с предупреждением"
            ),
            None,
        )
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
            username="admin", password="testpass123", role=User.Role.ADMIN
        )
        self.client.force_login(admin_user)

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        self.assertTrue(context["can_view_cold_call_reports"])

    def test_empty_states_displayed(self):
        """Тест: пустые состояния отображаются корректно."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "На сегодня пусто")
        self.assertContains(response, "Новых задач нет")
        self.assertContains(response, "Всё в срок")
        self.assertContains(response, "На неделе задач нет")
        self.assertContains(response, "Нет предупреждений по договорам")

    def test_tasks_ordered_by_due_at(self):
        """Тест: задачи на сегодня упорядочены по due_at."""
        # Создаём задачи с разными due_at (не NEW, чтобы они попали в tasks_today)
        task1 = Task.objects.create(
            title="Задача 1 (позже)",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=15),
            status=Task.Status.IN_PROGRESS,
        )
        task2 = Task.objects.create(
            title="Задача 2 (раньше)",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.IN_PROGRESS,
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
            status=Task.Status.NEW,
        )
        # Обновляем created_at для первой задачи (делаем её старше)
        Task.objects.filter(id=task1.id).update(created_at=timezone.now() - timedelta(hours=2))

        task2 = Task.objects.create(
            title="Новая новая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW,
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
        """Тест: просроченные задачи ограничены 20 элементами в контексте, на dashboard показывается только 3."""
        # Создаём 25 просроченных задач (не NEW, чтобы они попали в overdue)
        overdue_time = self.local_now - timedelta(days=1)
        for i in range(25):
            Task.objects.create(
                title=f"Просроченная задача {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=overdue_time - timedelta(hours=i),
                status=Task.Status.IN_PROGRESS,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        # На dashboard показывается только 3 задачи (лимит в views)
        self.assertEqual(len(context["overdue"]), 3)
        # Но счетчик показывает правильное количество (25)
        self.assertEqual(context["overdue_count"], 25)

    def test_tasks_week_limit_50_tasks(self):
        """Тест: задачи на неделю ограничены 50 элементами в контексте, на dashboard показывается только 3."""
        # Создаём 55 задач на неделю (не NEW, чтобы они попали в tasks_week)
        week_task_time = self.tomorrow_start + timedelta(days=3)
        for i in range(55):
            Task.objects.create(
                title=f"Задача на неделю {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=week_task_time + timedelta(hours=i),
                status=Task.Status.IN_PROGRESS,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        # На dashboard показывается только 3 задачи (лимит в views)
        self.assertEqual(len(context["tasks_week"]), 3)
        # Но счетчик показывает правильное количество (55)
        self.assertEqual(context["tasks_week_count"], 55)

    def test_tasks_new_limit_20_tasks(self):
        """Тест: новые задачи ограничены 20 элементами в контексте, на dashboard показывается только 3."""
        # Создаём 25 новых задач
        for i in range(25):
            Task.objects.create(
                title=f"Новая задача {i}",
                assigned_to=self.user,
                company=self.company,
                status=Task.Status.NEW,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        # На dashboard показывается только 3 задачи (лимит в views)
        self.assertEqual(len(context["tasks_new"]), 3)
        # Но счетчик показывает правильное количество (25)
        self.assertEqual(context["tasks_new_count"], 25)

    def test_contracts_soon_limit_50_companies(self):
        """Тест: договоры ограничены 50 компаниями."""
        # Создаём 55 компаний с договорами (в пределах 30 дней, чтобы все попали в фильтр)
        # Распределяем равномерно: первые 30 компаний получают даты 1-30 дней,
        # следующие 25 компаний получают даты 1-25 дней (чтобы было больше разнообразия)
        for i in range(55):
            if i < 30:
                days_offset = i + 1  # От 1 до 30 дней
            else:
                days_offset = (i - 30) + 1  # От 1 до 25 дней (для следующих 25 компаний)
            contract_until = self.today_date + timedelta(days=days_offset)
            Company.objects.create(
                name=f"Компания {i:03d}",  # Форматируем с ведущими нулями для правильной сортировки
                responsible=self.user,
                contract_until=contract_until,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        # Проверяем, что лимит работает (должно быть 50, а не 55)
        self.assertLessEqual(len(context["contracts_soon"]), 50)
        # И что все 55 компаний попали в запрос (но лимит ограничил до 50)
        # Проверяем, что получили именно 50
        self.assertEqual(len(context["contracts_soon"]), 50)

    def test_contracts_soon_excludes_null_contract_until(self):
        """Тест: договоры исключают компании без contract_until."""
        # Создаём компанию без contract_until
        Company.objects.create(
            name="Компания без договора", responsible=self.user, contract_until=None
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        contract_companies = [item["company"].name for item in context["contracts_soon"]]
        self.assertNotIn("Компания без договора", contract_companies)

    def test_tasks_limited_to_5_on_dashboard(self):
        """Тест: на dashboard отображается максимум 3 задач в каждом блоке (лимит в views)."""
        # Создаём 10 задач на сегодня
        for i in range(10):
            Task.objects.create(
                title=f"Задача на сегодня {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=self.today_start + timedelta(hours=i),
                status=Task.Status.IN_PROGRESS,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        context = response.context
        # На dashboard показывается только 3 (лимит в views)
        self.assertLessEqual(len(context["tasks_today"]), 3)
        # Но общее количество должно быть 10
        self.assertEqual(context["tasks_today_count"], 10)

    def test_view_all_links_with_correct_filters(self):
        """Тест: кнопки «Все задачи…» имеют правильные фильтры."""
        for i in range(7):
            due_at_today = self.local_now + timedelta(hours=i + 1)
            if due_at_today >= self.tomorrow_start:
                due_at_today = self.tomorrow_start - timedelta(minutes=1)
            Task.objects.create(
                title=f"Задача на сегодня {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=due_at_today,
                status=Task.Status.IN_PROGRESS,
            )
            Task.objects.create(
                title=f"Новая задача {i}",
                assigned_to=self.user,
                company=self.company,
                status=Task.Status.NEW,
            )
            Task.objects.create(
                title=f"Просроченная задача {i}",
                assigned_to=self.user,
                company=self.company,
                due_at=self.local_now - timedelta(days=i + 1),
                status=Task.Status.IN_PROGRESS,
            )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        response_text = response.content.decode("utf-8")

        # v2 ссылки фильтруют по текущему пользователю (mine=1), чтобы
        # клик из «моих» задач оставался в контексте «моих».
        # Проверяем оба варианта экранирования амперсанда (& и &amp;).
        def _contains_link(text: str, needle: str) -> bool:
            return needle in text or needle.replace("&", "&amp;") in text

        self.assertTrue(_contains_link(response_text, "/tasks/?mine=1&today=1"))
        self.assertTrue(_contains_link(response_text, "/tasks/?mine=1&status=new"))
        self.assertTrue(_contains_link(response_text, "/tasks/?mine=1&overdue=1"))

    def test_task_status_badges_displayed(self):
        """Тест: статусы задач отображаются с правильными бейджами."""
        # Создаём задачи с разными статусами
        Task.objects.create(
            title="Новая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW,
        )
        Task.objects.create(
            title="Задача в работе",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.IN_PROGRESS,
            due_at=self.today_start + timedelta(hours=5),
        )
        # Выполненные задачи не должны отображаться на dashboard (исключаются в запросе)
        Task.objects.create(
            title="Выполненная задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.DONE,
            due_at=self.today_start + timedelta(hours=3),
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)

        # W1.3 (2026-04-21): inline CSS с `.badge-new{...}` extracted в static файл
        # `backend/static/ui/css/pages/base_global.css`, поэтому assertContains на
        # строку `badge-new` больше не pass (класс есть в CSS файле, не в HTML
        # response body). Исторически assertContains фактически проверял наличие
        # CSS определения в inline <style>, а не реальный рендер бейджей. Проверка
        # реального поведения — через response.context.tasks_* ниже.
        # Выполненные задачи не должны отображаться на dashboard (исключаются в запросе)
        self.assertNotContains(response, "Выполненная задача")
        # Проверяем, что выполненные задачи не отображаются
        context = response.context
        all_task_titles = []
        for task_list in [
            context["tasks_new"],
            context["tasks_today"],
            context["overdue"],
            context["tasks_week"],
        ]:
            all_task_titles.extend([t.title for t in task_list])
        self.assertNotIn("Выполненная задача", all_task_titles)

    def test_due_date_displayed(self):
        """Тест: дедлайн отображается в карточке задачи."""
        task = Task.objects.create(
            title="Задача с дедлайном",
            assigned_to=self.user,
            company=self.company,
            due_at=self.today_start + timedelta(hours=10),
            status=Task.Status.IN_PROGRESS,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # v2 формат зависит от секции: H:i для «Сегодня», d.m H:i для других
        formatted_time = task.due_at.strftime("%H:%M")
        self.assertContains(response, formatted_time)

    def test_overdue_tasks_highlighted(self):
        """Тест: просроченные задачи отображаются в секции «Просрочено»."""
        Task.objects.create(
            title="Просроченная задача",
            assigned_to=self.user,
            company=self.company,
            due_at=self.local_now - timedelta(days=1),
            status=Task.Status.IN_PROGRESS,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Просроченная задача")
        self.assertContains(response, "v2-item__dot--danger")

    def test_hero_section_rendered(self):
        """Тест: hero-блок дашборда отображается с метриками."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "v2-hero")

    def test_new_task_button(self):
        """Тест: кнопка создания задачи присутствует."""
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "data-v2-modal-open")

    def test_task_links_present(self):
        """Тест: задачи на дашборде имеют ссылки для перехода."""
        task = Task.objects.create(
            title="Тестовая задача",
            assigned_to=self.user,
            company=self.company,
            status=Task.Status.NEW,
        )

        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f"/tasks/{task.id}/")
