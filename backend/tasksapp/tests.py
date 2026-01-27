from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from companies.models import Company
from companies.services import resolve_target_companies
from tasksapp.models import Task, TaskType

User = get_user_model()


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
        resp = self.client.post("/api/tasks/", payload, format="json")
        # Обрабатываем редирект вручную (APIClient не следует редиректам автоматически)
        max_redirects = 5
        redirect_count = 0
        while resp.status_code in [status.HTTP_301_MOVED_PERMANENTLY, status.HTTP_302_FOUND, status.HTTP_307_TEMPORARY_REDIRECT, status.HTTP_308_PERMANENT_REDIRECT] and redirect_count < max_redirects:
            redirect_url = resp.get("Location") or resp.get("location")
            if not redirect_url:
                break
            # Если полный URL, извлекаем только путь
            if redirect_url.startswith("http"):
                from urllib.parse import urlparse
                redirect_url = urlparse(redirect_url).path
            resp = self.client.post(redirect_url, payload, format="json")
            redirect_count += 1
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
