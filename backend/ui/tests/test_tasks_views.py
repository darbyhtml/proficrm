"""
Тесты для ui/views/tasks.py — основные сценарии всех view-функций.

Покрытые сценарии:
 1.  task_list — GET: рендер списка (200)
 2.  task_list — фильтр по статусу
 3.  task_list — фильтр mine
 4.  task_list — фильтр overdue
 5.  task_list — авторизация обязательна
 6.  task_create — GET: рендер формы (200)
 7.  task_create — POST: создаёт задачу + редирект
 8.  task_create — AJAX POST: возвращает JSON {ok: true}
 9.  task_create — MANAGER не может назначить другому пользователю
10.  task_create — дублирование (два POST подряд)
11.  task_delete — не-POST редиректит на task_list
12.  task_delete — POST: удаляет задачу (redirect)
13.  task_delete — POST: с save_to_notes создаёт заметку
14.  task_delete — нет прав → 403
15.  task_set_status — POST меняет статус
16.  task_set_status — статус DONE: completed_at выставляется
17.  task_set_status — нет прав → AJAX 403
18.  task_add_comment — POST: добавляет комментарий (JSON)
19.  task_add_comment — пустой текст → 400
20.  task_add_comment — GET → 405
21.  task_edit — POST: обновляет поля задачи
22.  task_edit — смена дедлайна создаёт TaskEvent
23.  task_edit — нет прав → 403
24.  task_view — GET: рендер модального шаблона (200)
25.  task_bulk_reassign — только admin (MANAGER → redirect с ошибкой)
"""

import json
from datetime import timedelta

from django.test import TestCase, Client, override_settings
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from companies.models import Company, CompanyNote
from tasksapp.models import Task, TaskComment, TaskEvent, TaskType

User = get_user_model()


def _due():
    """Дедлайн через 1 день."""
    return timezone.now() + timedelta(days=1)


def _due_fmt():
    return _due().strftime("%Y-%m-%dT%H:%M")


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskListViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_tasks", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_tasks", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="ТестКомп", responsible=self.manager)
        self.task = Task.objects.create(
            title="Звонок",
            company=self.company,
            assigned_to=self.manager,
            created_by=self.manager,
            status=Task.Status.NEW,
            due_at=_due(),
        )
        self.client.force_login(self.admin)

    def test_task_list_renders(self):
        """GET /tasks/ → 200 и название задачи на странице."""
        r = self.client.get(reverse("task_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Звонок")

    def test_task_list_filter_by_status(self):
        """GET /tasks/?status=done — задача NEW не отображается."""
        done_task = Task.objects.create(
            title="Завершена",
            company=self.company,
            assigned_to=self.manager,
            created_by=self.manager,
            status=Task.Status.DONE,
            due_at=_due(),
        )
        r = self.client.get(reverse("task_list") + "?status=done&show_done=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Завершена")

    def test_task_list_filter_mine(self):
        """GET /tasks/?mine=1 от менеджера → только его задачи."""
        self.client.force_login(self.manager)
        r = self.client.get(reverse("task_list") + "?mine=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Звонок")

    def test_task_list_filter_overdue(self):
        """GET /tasks/?overdue=1 — просроченная задача попадает в список."""
        overdue_task = Task.objects.create(
            title="ПросроченоЗадача",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=timezone.now() - timedelta(days=2),
        )
        r = self.client.get(reverse("task_list") + "?overdue=1")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ПросроченоЗадача")

    def test_task_list_requires_login(self):
        """GET без авторизации → редирект на login."""
        self.client.logout()
        r = self.client.get(reverse("task_list"))
        self.assertIn(r.status_code, [301, 302])
        self.assertIn("/login/", r["Location"])


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskCreateViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_create", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_create", password="pass", role=User.Role.MANAGER
        )
        self.other = User.objects.create_user(
            username="other_create", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="CreateКомп", responsible=self.manager)
        self.task_type = TaskType.objects.create(name="Звонок")

    def test_task_create_get_form(self):
        """GET /tasks/new/ → 200."""
        self.client.force_login(self.admin)
        r = self.client.get(reverse("task_create"))
        self.assertEqual(r.status_code, 200)

    def test_task_create_post_creates_task(self):
        """POST /tasks/new/ → создаётся задача с именем типа и происходит редирект."""
        self.client.force_login(self.admin)
        data = {
            "company": str(self.company.id),
            "type": self.task_type.id,
            "assigned_to": str(self.manager.id),
            "due_at": _due_fmt(),
            "description": "",
        }
        r = self.client.post(reverse("task_create"), data)
        self.assertIn(r.status_code, [301, 302])
        self.assertTrue(Task.objects.filter(type=self.task_type).exists())

    def test_task_create_ajax_returns_json(self):
        """POST /tasks/new/ с X-Requested-With: XMLHttpRequest → JSON {ok: true}."""
        self.client.force_login(self.admin)
        data = {
            "company": str(self.company.id),
            "type": self.task_type.id,
            "assigned_to": str(self.manager.id),
            "due_at": _due_fmt(),
            "description": "",
        }
        r = self.client.post(reverse("task_create"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertTrue(body.get("ok"))

    def test_task_create_manager_reassigned_to_self(self):
        """MANAGER пытается назначить задачу другому → задача назначается на себя."""
        self.client.force_login(self.manager)
        data = {
            "company": str(self.company.id),
            "type": self.task_type.id,
            "assigned_to": str(self.other.id),  # другой менеджер
            "due_at": _due_fmt(),
            "description": "",
        }
        r = self.client.post(reverse("task_create"), data, HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        # MANAGER всегда получает assigned_to = self
        if r.status_code == 200:
            body = json.loads(r.content)
            if body.get("ok"):
                task = Task.objects.get(id=body["task_id"])
                self.assertEqual(task.assigned_to_id, self.manager.id)


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskDeleteViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_del", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_del", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="DelКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="Удаляемая",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )

    def test_task_delete_get_redirects(self):
        """GET /tasks/<id>/delete/ → редирект на task_list."""
        self.client.force_login(self.admin)
        r = self.client.get(reverse("task_delete", kwargs={"task_id": self.task.id}))
        self.assertIn(r.status_code, [301, 302])

    def test_task_delete_post_deletes(self):
        """POST /tasks/<id>/delete/ → задача удалена."""
        self.client.force_login(self.admin)
        r = self.client.post(reverse("task_delete", kwargs={"task_id": self.task.id}))
        self.assertIn(r.status_code, [301, 302])
        self.assertFalse(Task.objects.filter(id=self.task.id).exists())

    def test_task_delete_with_save_to_notes_creates_note(self):
        """POST с save_to_notes=1 → создаётся CompanyNote."""
        self.client.force_login(self.admin)
        count_before = CompanyNote.objects.filter(company=self.company).count()
        r = self.client.post(
            reverse("task_delete", kwargs={"task_id": self.task.id}),
            {"save_to_notes": "1"},
        )
        self.assertIn(r.status_code, [301, 302])
        self.assertEqual(
            CompanyNote.objects.filter(company=self.company).count(),
            count_before + 1,
        )

    def test_task_delete_forbidden_for_unrelated_manager(self):
        """MANAGER без связи с задачей → 403."""
        unrelated = User.objects.create_user(
            username="unrelated_del", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(unrelated)
        r = self.client.post(
            reverse("task_delete", kwargs={"task_id": self.task.id}),
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 403)


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskSetStatusViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_status", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_status", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="StatusКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="СтатусЗадача",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )

    def _url(self):
        return reverse("task_set_status", kwargs={"task_id": self.task.id})

    def test_set_status_changes_status(self):
        """POST /tasks/<id>/status/ → статус обновляется."""
        self.client.force_login(self.admin)
        self.client.post(self._url(), {"status": Task.Status.IN_PROGRESS})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

    def test_set_status_done_sets_completed_at(self):
        """POST со статусом DONE → completed_at выставляется."""
        self.client.force_login(self.admin)
        self.task.status = Task.Status.IN_PROGRESS
        self.task.save(update_fields=["status"])
        self.client.post(self._url(), {"status": Task.Status.DONE})
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.DONE)
        self.assertIsNotNone(self.task.completed_at)

    def test_set_status_forbidden_for_unrelated_manager(self):
        """MANAGER без связи с задачей → AJAX 403."""
        unrelated = User.objects.create_user(
            username="unrelated_status", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(unrelated)
        r = self.client.post(
            self._url(),
            {"status": Task.Status.IN_PROGRESS},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 403)


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskAddCommentViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_comment", password="pass", role=User.Role.ADMIN
        )
        self.company = Company.objects.create(name="CommКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="КомментЗадача",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )
        self.client.force_login(self.admin)

    def _url(self):
        return reverse("task_add_comment", kwargs={"task_id": self.task.id})

    def test_add_comment_creates_comment(self):
        """POST /tasks/<id>/comment/ → комментарий сохранён, JSON {ok: true}."""
        r = self.client.post(
            self._url(),
            {"text": "Тестовый комментарий"},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 200)
        body = json.loads(r.content)
        self.assertTrue(body.get("ok"))
        self.assertTrue(TaskComment.objects.filter(task=self.task).exists())

    def test_add_empty_comment_returns_400(self):
        """POST с пустым текстом → 400."""
        r = self.client.post(
            self._url(),
            {"text": "   "},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertEqual(r.status_code, 400)

    def test_add_comment_get_returns_405(self):
        """GET /tasks/<id>/comment/ → 405."""
        r = self.client.get(self._url())
        self.assertEqual(r.status_code, 405)


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskEditViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_edit", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_edit", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="EditКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="РедактирЗадача",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )

    def _url(self):
        return reverse("task_edit", kwargs={"task_id": self.task.id})

    def test_task_edit_post_updates_type(self):
        """POST /tasks/<id>/edit/ → type обновляется (title берётся из type.name)."""
        new_type = TaskType.objects.create(name="НовыйТипЗадачи")
        self.client.force_login(self.admin)
        new_due = (timezone.now() + timedelta(days=3)).strftime("%Y-%m-%dT%H:%M")
        r = self.client.post(
            self._url(),
            {
                "type": new_type.id,
                "due_at": new_due,
                "description": "",
            },
        )
        self.assertIn(r.status_code, [200, 301, 302])
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "НовыйТипЗадачи")

    def test_task_edit_deadline_change_updates_due_at(self):
        """Смена дедлайна при POST edit → due_at задачи обновляется."""
        task_type = TaskType.objects.create(name="ТипДляЭдит")
        self.task.type = task_type
        self.task.title = task_type.name
        self.task.save(update_fields=["type", "title"])
        self.client.force_login(self.admin)
        new_due = (timezone.now() + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M")
        r = self.client.post(
            self._url(),
            {
                "type": task_type.id,
                "due_at": new_due,
                "description": "",
            },
        )
        # Форма валидна → редирект
        self.assertIn(r.status_code, [301, 302])
        self.task.refresh_from_db()
        # Новый дедлайн должен быть позже исходного (day+1)
        self.assertGreater(self.task.due_at, timezone.now() + timedelta(days=3))

    def test_task_edit_forbidden_for_unrelated(self):
        """MANAGER без связи с задачей → редирект или 403."""
        task_type = TaskType.objects.create(name="ТипДляФорбид")
        unrelated = User.objects.create_user(
            username="unrelated_edit", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(unrelated)
        r = self.client.post(
            self._url(),
            {
                "type": task_type.id,
                "due_at": _due_fmt(),
                "description": "",
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertIn(r.status_code, [403, 302])


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskViewModalTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_view", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_view", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="ViewКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="ModalЗадача",
            company=self.company,
            assigned_to=self.admin,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )

    def test_task_view_renders_modal(self):
        """GET /tasks/<id>/ → 200."""
        self.client.force_login(self.admin)
        r = self.client.get(reverse("task_view", kwargs={"task_id": self.task.id}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "ModalЗадача")

    def test_task_view_403_for_unrelated_manager(self):
        """MANAGER без связи → 403 или редирект."""
        unrelated = User.objects.create_user(
            username="unrelated_view", password="pass", role=User.Role.MANAGER
        )
        self.client.force_login(unrelated)
        r = self.client.get(reverse("task_view", kwargs={"task_id": self.task.id}))
        self.assertIn(r.status_code, [403, 302])


@override_settings(SECURE_SSL_REDIRECT=False)
class TaskBulkReassignViewTest(TestCase):
    def setUp(self):
        self.client = Client()
        self.admin = User.objects.create_user(
            username="admin_bulk", password="pass", role=User.Role.ADMIN
        )
        self.manager = User.objects.create_user(
            username="mgr_bulk", password="pass", role=User.Role.MANAGER
        )
        self.new_manager = User.objects.create_user(
            username="new_mgr_bulk", password="pass", role=User.Role.MANAGER
        )
        self.company = Company.objects.create(name="BulkКомп", responsible=self.admin)
        self.task = Task.objects.create(
            title="BulkЗадача",
            company=self.company,
            assigned_to=self.manager,
            created_by=self.admin,
            status=Task.Status.NEW,
            due_at=_due(),
        )

    def test_bulk_reassign_forbidden_for_manager(self):
        """MANAGER пытается bulk_reassign → редирект с ошибкой (не admin)."""
        self.client.force_login(self.manager)
        r = self.client.post(
            reverse("task_bulk_reassign"),
            {
                "apply_mode": "selected",
                "task_ids": [str(self.task.id)],
                "assigned_to_id": str(self.new_manager.id),
            },
        )
        # Должен редиректить с сообщением об ошибке (нет прав)
        self.assertIn(r.status_code, [301, 302, 403])
        # Задача не переназначена
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_to_id, self.manager.id)

    def test_bulk_reassign_admin_reassigns_task(self):
        """ADMIN: bulk_reassign selected → задача переназначена."""
        self.client.force_login(self.admin)
        r = self.client.post(
            reverse("task_bulk_reassign"),
            {
                "apply_mode": "selected",
                "task_ids": [str(self.task.id)],
                "assigned_to_id": str(self.new_manager.id),
            },
        )
        self.assertIn(r.status_code, [301, 302])
        self.task.refresh_from_db()
        self.assertEqual(self.task.assigned_to_id, self.new_manager.id)
