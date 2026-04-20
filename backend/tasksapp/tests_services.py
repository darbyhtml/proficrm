"""
Тесты для tasksapp/services.py — TaskService.
"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from companies.models import Company
from tasksapp.models import Task, TaskComment, TaskEvent, TaskType
from tasksapp.services import TaskService


class TaskServiceSetupMixin:
    """Общая подготовка для тестов TaskService."""

    def setUp(self):
        self.manager = User.objects.create_user(
            username="svc_mgr", password="pass", role=User.Role.MANAGER
        )
        self.admin = User.objects.create_user(
            username="svc_admin", password="pass", role=User.Role.ADMIN
        )
        self.company = Company.objects.create(name="Тест Сервис", responsible=self.manager)
        self.task_type = TaskType.objects.create(name="Звонок", color="#000", icon="📞")
        self.task = Task.objects.create(
            title="Тестовая задача",
            assigned_to=self.manager,
            created_by=self.admin,
            company=self.company,
            status=Task.Status.NEW,
            type=self.task_type,
        )


class TaskServiceSetStatusTest(TaskServiceSetupMixin, TestCase):

    def test_set_status_changes_status(self):
        result = TaskService.set_status(
            task=self.task, user=self.manager, new_status=Task.Status.IN_PROGRESS
        )
        self.assertTrue(result["changed"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

    def test_set_status_creates_task_event(self):
        TaskService.set_status(
            task=self.task, user=self.manager, new_status=Task.Status.IN_PROGRESS
        )
        event = TaskEvent.objects.filter(task=self.task, kind=TaskEvent.Kind.STATUS_CHANGED).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.actor, self.manager)

    def test_set_status_done_sets_completed_at(self):
        TaskService.set_status(task=self.task, user=self.manager, new_status=Task.Status.DONE)
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.completed_at)

    def test_set_status_invalid_raises(self):
        with self.assertRaises(ValueError):
            TaskService.set_status(task=self.task, user=self.manager, new_status="nonexistent")

    def test_set_status_done_with_save_to_notes_creates_note(self):
        result = TaskService.set_status(
            task=self.task,
            user=self.manager,
            new_status=Task.Status.DONE,
            save_to_notes=True,
        )
        self.assertTrue(result["note_created"])
        self.assertIsNotNone(result["note"])
        from companies.models import CompanyNote

        self.assertTrue(
            CompanyNote.objects.filter(company=self.company, author=self.manager).exists()
        )

    def test_set_status_done_without_save_to_notes_no_note(self):
        result = TaskService.set_status(
            task=self.task, user=self.manager, new_status=Task.Status.DONE
        )
        self.assertFalse(result["note_created"])
        self.assertIsNone(result["note"])

    def test_set_status_no_company_no_note(self):
        task_no_company = Task.objects.create(
            title="Без компании",
            assigned_to=self.manager,
            status=Task.Status.NEW,
        )
        result = TaskService.set_status(
            task=task_no_company,
            user=self.manager,
            new_status=Task.Status.DONE,
            save_to_notes=True,
        )
        # no_company → note not created (save_to_notes ignored)
        self.assertFalse(result["note_created"])


class TaskServiceDeleteTest(TaskServiceSetupMixin, TestCase):

    def test_delete_task_removes_task(self):
        task_id = self.task.id
        TaskService.delete_task(task=self.task, user=self.manager)
        self.assertFalse(Task.objects.filter(id=task_id).exists())

    def test_delete_task_returns_title(self):
        result = TaskService.delete_task(task=self.task, user=self.manager)
        self.assertEqual(result["title"], "Тестовая задача")

    def test_delete_task_with_save_to_notes_creates_note(self):
        result = TaskService.delete_task(task=self.task, user=self.manager, save_to_notes=True)
        self.assertTrue(result["note_created"])
        from companies.models import CompanyNote

        self.assertTrue(
            CompanyNote.objects.filter(company=self.company, author=self.manager).exists()
        )

    def test_delete_task_without_save_to_notes_no_note(self):
        result = TaskService.delete_task(task=self.task, user=self.manager)
        self.assertFalse(result["note_created"])
        self.assertIsNone(result["note"])

    def test_delete_task_company_id_in_result(self):
        result = TaskService.delete_task(task=self.task, user=self.manager)
        self.assertEqual(result["company_id"], self.company.id)


class TaskServiceAddCommentTest(TaskServiceSetupMixin, TestCase):

    def test_add_comment_creates_comment(self):
        comment = TaskService.add_comment(task=self.task, user=self.manager, text="Привет!")
        self.assertIsInstance(comment, TaskComment)
        self.assertEqual(comment.text, "Привет!")
        self.assertEqual(comment.author, self.manager)
        self.assertEqual(comment.task, self.task)

    def test_add_comment_empty_raises(self):
        with self.assertRaises(ValueError):
            TaskService.add_comment(task=self.task, user=self.manager, text="")

    def test_add_comment_whitespace_raises(self):
        with self.assertRaises(ValueError):
            TaskService.add_comment(task=self.task, user=self.manager, text="   ")


class TaskServiceCreateNoteTest(TaskServiceSetupMixin, TestCase):

    def test_create_note_from_task(self):
        from companies.models import CompanyNote

        note = TaskService.create_note_from_task(self.task, self.manager)
        self.assertIsInstance(note, CompanyNote)
        self.assertIn("Звонок", note.text)
        self.assertEqual(note.company, self.company)

    def test_create_note_includes_description(self):
        self.task.description = "Описание задачи"
        self.task.save(update_fields=["description"])
        note = TaskService.create_note_from_task(self.task, self.manager)
        self.assertIn("Описание задачи", note.text)

    def test_create_note_includes_due_at(self):
        self.task.due_at = timezone.now()
        self.task.save(update_fields=["due_at"])
        note = TaskService.create_note_from_task(self.task, self.manager)
        self.assertIn("Дедлайн:", note.text)

    def test_create_note_raises_without_company(self):
        task_no_company = Task.objects.create(
            title="Без компании",
            assigned_to=self.manager,
            status=Task.Status.NEW,
        )
        with self.assertRaises(ValueError):
            TaskService.create_note_from_task(task_no_company, self.manager)
