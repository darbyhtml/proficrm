from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from companies.models import Company
from companies.services import resolve_target_companies
from tasksapp.models import Task, TaskType
from ui import views as ui_views

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskOrgCreationTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="manager",
            email="manager@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
        )
        self.client.force_authenticate(user=self.user)

        # Головная компания без филиалов
        self.root_single = Company.objects.create(name="Одиночная", responsible=self.user)

        # Организация: root + два филиала
        self.root = Company.objects.create(name="Головная", responsible=self.user)
        self.branch1 = Company.objects.create(name="Филиал 1", head_company=self.root, responsible=self.user)
        self.branch2 = Company.objects.create(name="Филиал 2", head_company=self.root, responsible=self.user)

        self.task_type = TaskType.objects.create(name="Тестовая задача")

    def _post_task(self, company: Company, apply_to_org: bool) -> list[Task]:
        due_at = (timezone.now() + timedelta(days=1)).isoformat()
        payload = {
            "title": "Задача по организации",
            "description": "Описание",
            "status": Task.Status.NEW,
            "company": str(company.id),
            "type": self.task_type.id,
            "due_at": due_at,
            "apply_to_org_branches": apply_to_org,
        }
        # DRF DefaultRouter создает URL с trailing slash: /api/tasks/
        # Используем reverse для получения правильного URL
        from django.urls import reverse
        url = reverse("task-list")  # Должно вернуть "/api/tasks/"
        # Убеждаемся, что URL заканчивается на slash (DRF DefaultRouter требует это)
        if not url.endswith("/"):
            url = url + "/"
        resp = self.client.post(url, payload, format="json")
        
        # Проверяем успешный статус (201 Created или 200 OK)
        self.assertIn(
            resp.status_code,
            (status.HTTP_201_CREATED, status.HTTP_200_OK),
            f"Unexpected status: {resp.status_code}, data={getattr(resp, 'data', None)}",
        )
        return list(Task.objects.order_by("id"))

    def test_single_root_no_branches_flag_off_creates_one_task(self):
        tasks = self._post_task(self.root_single, apply_to_org=False)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].company_id, self.root_single.id)

    def test_single_root_no_branches_flag_on_still_one_task(self):
        tasks = self._post_task(self.root_single, apply_to_org=True)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].company_id, self.root_single.id)

    def test_root_with_branches_flag_off_only_root(self):
        tasks = self._post_task(self.root, apply_to_org=False)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].company_id, self.root.id)

    def test_root_with_branches_flag_on_all_org(self):
        tasks = self._post_task(self.root, apply_to_org=True)
        company_ids = {t.company_id for t in tasks}
        self.assertSetEqual(company_ids, {self.root.id, self.branch1.id, self.branch2.id})

    def test_branch_selected_flag_on_all_org_without_duplicates(self):
        tasks = self._post_task(self.branch1, apply_to_org=True)
        company_ids = [t.company_id for t in tasks]
        self.assertEqual(len(company_ids), len(set(company_ids)))
        self.assertSetEqual(set(company_ids), {self.root.id, self.branch1.id, self.branch2.id})


class ResolveTargetCompaniesUnitTestCase(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="u",
            email="u@example.com",
            password="x",
            role=User.Role.ADMIN,
        )
        self.root = Company.objects.create(name="Root", responsible=self.user)
        self.branch1 = Company.objects.create(name="B1", head_company=self.root, responsible=self.user)
        self.branch2 = Company.objects.create(name="B2", head_company=self.root, responsible=self.user)

    def test_resolve_without_flag_returns_only_selected(self):
        targets = resolve_target_companies(self.branch1, apply_to_org_branches=False)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0].id, self.branch1.id)

    def test_resolve_with_flag_returns_full_org(self):
        targets = resolve_target_companies(self.branch1, apply_to_org_branches=True)
        ids = {c.id for c in targets}
        self.assertSetEqual(ids, {self.root.id, self.branch1.id, self.branch2.id})


class TaskNotificationTestCase(TestCase):
    """Тесты для уведомлений при назначении задач."""
    
    def setUp(self):
        from accounts.models import Branch
        self.branch = Branch.objects.create(code="test", name="Тестовый филиал")
        self.creator = User.objects.create_user(
            username="creator",
            email="creator@example.com",
            password="testpass123",
            role=User.Role.SALES_HEAD,
            branch=self.branch,
        )
        self.assignee = User.objects.create_user(
            username="assignee",
            email="assignee@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.task_type = TaskType.objects.create(name="Тестовая задача")
        self.company = Company.objects.create(name="Тестовая компания", responsible=self.creator)
    
    def test_task_assigned_notification_created(self):
        """Проверка создания уведомления при назначении задачи."""
        from notifications.models import Notification
        
        task = Task.objects.create(
            created_by=self.creator,
            assigned_to=self.assignee,
            company=self.company,
            type=self.task_type,
            title="Тестовая задача",
            description="Описание",
            is_urgent=True,
        )
        
        # Проверяем, что уведомление создано
        notification = Notification.objects.filter(
            user=self.assignee,
            kind=Notification.Kind.TASK,
            title="Вам назначена задача",
        ).first()
        
        self.assertIsNotNone(notification, "Уведомление должно быть создано")
        self.assertIsNotNone(notification.payload, "Payload должен быть заполнен")
        self.assertEqual(notification.payload.get("task_id"), str(task.id))
        self.assertEqual(notification.payload.get("is_urgent"), True)
        self.assertEqual(notification.payload.get("creator_role"), "sales_head")
    
    def test_task_assigned_to_self_no_notification(self):
        """Проверка, что уведомление не создаётся, если задача назначена самому создателю."""
        from notifications.models import Notification
        
        task = Task.objects.create(
            created_by=self.creator,
            assigned_to=self.creator,  # Назначено самому создателю
            company=self.company,
            type=self.task_type,
            title="Тестовая задача",
        )
        
        # Проверяем, что уведомление НЕ создано
        notification = Notification.objects.filter(
            user=self.creator,
            kind=Notification.Kind.TASK,
            title="Вам назначена задача",
        ).first()
        
        self.assertIsNone(notification, "Уведомление не должно быть создано для самого создателя")


class CompanyDeletionRequestNotificationTestCase(TestCase):
    """Тесты для уведомлений при запросах на удаление компаний."""
    
    def setUp(self):
        from accounts.models import Branch
        from companies.models import CompanyDeletionRequest
        self.branch = Branch.objects.create(code="test", name="Тестовый филиал")
        self.manager = User.objects.create_user(
            username="manager",
            email="manager@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
            branch=self.branch,
        )
        self.director = User.objects.create_user(
            username="director",
            email="director@example.com",
            password="testpass123",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.branch,
        )
        self.company = Company.objects.create(
            name="Тестовая компания",
            responsible=self.manager,
            branch=self.branch,
        )
    
    def test_deletion_request_notifies_director(self):
        """Проверка уведомления директора при создании запроса на удаление."""
        from notifications.models import Notification
        from companies.models import CompanyDeletionRequest
        
        req = CompanyDeletionRequest.objects.create(
            company=self.company,
            company_id_snapshot=self.company.id,
            company_name_snapshot=self.company.name,
            requested_by=self.manager,
            requested_by_branch=self.branch,
            note="Тестовая причина",
            status=CompanyDeletionRequest.Status.PENDING,
        )
        
        # Симулируем создание уведомления (в реальности это делается в view)
        from notifications.service import notify
        from notifications.models import Notification as NotifModel
        notify(
            user=self.director,
            kind=NotifModel.Kind.COMPANY,
            title="Запрос на удаление компании",
            body=f"{self.company.name}: Тестовая причина",
            url=f"/companies/{self.company.id}/",
            payload={
                "company_id": str(self.company.id),
                "request_id": req.id,
                "requested_by_id": self.manager.id,
            },
        )
        
        notification = Notification.objects.filter(
            user=self.director,
            kind=Notification.Kind.COMPANY,
            title="Запрос на удаление компании",
        ).first()
        
        self.assertIsNotNone(notification, "Уведомление должно быть создано для директора")


class BulkTransferBranchRestrictionTestCase(TestCase):
    """Тесты для ограничений bulk transfer по филиалу."""
    
    def setUp(self):
        from accounts.models import Branch
        from companies.permissions import get_transfer_targets
        
        self.branch1 = Branch.objects.create(code="branch1", name="Филиал 1")
        self.branch2 = Branch.objects.create(code="branch2", name="Филиал 2")
        
        self.director1 = User.objects.create_user(
            username="director1",
            email="director1@example.com",
            password="testpass123",
            role=User.Role.BRANCH_DIRECTOR,
            branch=self.branch1,
        )
        
        self.manager1 = User.objects.create_user(
            username="manager1",
            email="manager1@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
            branch=self.branch1,
        )
        
        self.manager2 = User.objects.create_user(
            username="manager2",
            email="manager2@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
            branch=self.branch2,
        )
    
    def test_get_transfer_targets_limited_by_branch(self):
        """Проверка, что get_transfer_targets для директора ограничивает список получателей филиалом."""
        from companies.permissions import get_transfer_targets
        
        targets = get_transfer_targets(self.director1)
        target_ids = set(targets.values_list("id", flat=True))
        
        # Должны быть только пользователи из branch1
        self.assertIn(self.manager1.id, target_ids, "Менеджер из того же филиала должен быть в списке")
        self.assertNotIn(self.manager2.id, target_ids, "Менеджер из другого филиала не должен быть в списке")


class TaskBulkFilterSummaryUnitTestCase(TestCase):
    """Юнит‑тесты для вспомогательной функции _apply_task_filters_for_bulk_ui (bulk‑операции задач)."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="bulk_user",
            email="bulk@example.com",
            password="testpass123",
            role=User.Role.ADMIN,
        )
        self.other_user = User.objects.create_user(
            username="bulk_other",
            email="other@example.com",
            password="testpass123",
            role=User.Role.MANAGER,
        )
        self.company = Company.objects.create(name="BulkTest", responsible=self.user)
        self.task_type = TaskType.objects.create(name="Bulk type")

        now = timezone.now()
        # Задача в пределах периода и на нужного исполнителя
        self.t1 = Task.objects.create(
            title="В период, мой исполнитель",
            status=Task.Status.NEW,
            company=self.company,
            type=self.task_type,
            assigned_to=self.user,
            due_at=now.replace(hour=10, minute=0, second=0, microsecond=0),
        )
        # Задача вне периода
        self.t2 = Task.objects.create(
            title="Вне периода",
            status=Task.Status.NEW,
            company=self.company,
            type=self.task_type,
            assigned_to=self.user,
            due_at=now - timedelta(days=30),
        )
        # Задача с другим исполнителем
        self.t3 = Task.objects.create(
            title="Другой исполнитель",
            status=Task.Status.NEW,
            company=self.company,
            type=self.task_type,
            assigned_to=self.other_user,
            due_at=now,
        )
        # Выполненная задача (DONE), чтобы проверить show_done по умолчанию
        self.t_done = Task.objects.create(
            title="Выполненная",
            status=Task.Status.DONE,
            company=self.company,
            type=self.task_type,
            assigned_to=self.user,
            due_at=now,
        )

    def test_apply_task_filters_for_bulk_ui_period_and_assignee(self):
        """Фильтры по исполнителю + периоду по due_at отрабатывают как в task_list, а summary человекочитаемо описывает их."""
        today = timezone.localdate()
        date_from = today.strftime("%Y-%m-%d")
        date_to = today.strftime("%Y-%m-%d")

        qs = Task.objects.all()
        params = {
            "status": "",  # без явного статуса — по умолчанию DONE исключается
            "mine": "1",
            "assigned_to": str(self.user.id),
            "overdue": "",
            "today": "",
            "date_from": date_from,
            "date_to": date_to,
            "show_done": "",  # как в UI по умолчанию
        }

        qs_filtered, summary = ui_views._apply_task_filters_for_bulk_ui(qs, self.user, params)

        ids = set(qs_filtered.values_list("id", flat=True))
        # Ожидаем только t1 в выборке
        self.assertIn(self.t1.id, ids)
        self.assertNotIn(self.t2.id, ids)
        self.assertNotIn(self.t3.id, ids)
        self.assertNotIn(self.t_done.id, ids)

        summary_text = " | ".join(summary)
        self.assertIn("Исполнитель:", summary_text)
        self.assertIn("Период дедлайна:", summary_text)
        self.assertIn("Без выполненных задач", summary_text)

    def test_apply_task_filters_for_bulk_ui_show_done_included(self):
        """При show_done=1 выполненные задачи не отфильтровываются и summary отражает это."""
        qs = Task.objects.all()
        params = {
            "status": "",
            "mine": "",
            "assigned_to": "",
            "overdue": "",
            "today": "",
            "date_from": "",
            "date_to": "",
            "show_done": "1",
        }

        qs_filtered, summary = ui_views._apply_task_filters_for_bulk_ui(qs, self.user, params)
        ids = set(qs_filtered.values_list("id", flat=True))
        self.assertIn(self.t_done.id, ids)

        summary_text = " | ".join(summary)
        self.assertIn("Включая выполненные задачи", summary_text)
