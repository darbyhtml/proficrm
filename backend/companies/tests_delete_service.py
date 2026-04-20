"""
Тесты для phase 3 сервиса:
- company_delete.execute_company_deletion
- company_delete.CompanyDeletionError

Покрывают: happy path, детач дочерних компаний, удаление связанных задач,
сохранность audit event, обработка IntegrityError.
"""
from __future__ import annotations

from unittest.mock import patch

from django.db import IntegrityError
from django.test import TestCase

from accounts.models import Branch, User
from audit.models import ActivityEvent
from companies.models import Company, CompanySearchIndex
from companies.services import execute_company_deletion, CompanyDeletionError
from tasksapp.models import Task


def _mk_user(username: str, role=User.Role.MANAGER) -> User:
    return User.objects.create_user(username=username, password="x", role=role)


def _mk_branch(code: str) -> Branch:
    return Branch.objects.create(code=code, name=code.upper())


def _mk_company(*, name: str = "ACME", branch=None, head_company=None, responsible=None) -> Company:
    return Company.objects.create(
        name=name,
        branch=branch,
        head_company=head_company,
        responsible=responsible,
    )


class ExecuteCompanyDeletionHappyPathTests(TestCase):
    """Базовый сценарий: обычная компания без дочек и задач."""

    def setUp(self):
        self.branch = _mk_branch("ekb")
        self.actor = _mk_user("actor", role=User.Role.BRANCH_DIRECTOR)
        self.company = _mk_company(branch=self.branch, responsible=self.actor)

    def test_company_deleted(self):
        pk = self.company.id
        result = execute_company_deletion(
            company=self.company,
            actor=self.actor,
            reason="test cleanup",
        )
        self.assertFalse(Company.objects.filter(id=pk).exists())
        self.assertEqual(result["company_pk"], pk)
        self.assertEqual(result["detached_count"], 0)
        self.assertEqual(result["tasks_deleted_count"], 0)

    def test_audit_event_created(self):
        pk = self.company.id
        execute_company_deletion(
            company=self.company,
            actor=self.actor,
            reason="for test",
        )
        event = ActivityEvent.objects.filter(
            entity_type="company",
            entity_id=str(pk),
            verb=ActivityEvent.Verb.DELETE,
        ).first()
        self.assertIsNotNone(event)
        self.assertEqual(event.message, "Компания удалена")
        self.assertEqual(event.meta.get("reason"), "for test")

    def test_approve_source_has_different_message(self):
        execute_company_deletion(
            company=self.company,
            actor=self.actor,
            source="approve_request",
            extra_meta={"request_id": 42},
        )
        event = ActivityEvent.objects.filter(verb=ActivityEvent.Verb.DELETE).first()
        self.assertEqual(event.message, "Компания удалена (по запросу)")
        self.assertEqual(event.meta.get("request_id"), 42)


class ExecuteCompanyDeletionWithRelatedTests(TestCase):
    """Задачи и дочерние компании удаляются/отцепляются правильно."""

    def setUp(self):
        self.branch = _mk_branch("tmn")
        self.actor = _mk_user("actor", role=User.Role.BRANCH_DIRECTOR)
        self.head = _mk_company(name="HEAD", branch=self.branch, responsible=self.actor)
        self.child1 = _mk_company(name="CHILD-1", branch=self.branch, head_company=self.head)
        self.child2 = _mk_company(name="CHILD-2", branch=self.branch, head_company=self.head)

    def test_children_detached(self):
        head_pk = self.head.id
        result = execute_company_deletion(company=self.head, actor=self.actor)
        self.assertEqual(result["detached_count"], 2)
        self.child1.refresh_from_db()
        self.child2.refresh_from_db()
        self.assertIsNone(self.child1.head_company_id)
        self.assertIsNone(self.child2.head_company_id)
        self.assertFalse(Company.objects.filter(id=head_pk).exists())

    def test_tasks_explicitly_deleted(self):
        # Task.company on_delete=SET_NULL, поэтому без нашей явной очистки
        # задачи остались бы висеть c company_id=NULL.
        t1 = Task.objects.create(
            company=self.head,
            title="Task 1",
            created_by=self.actor,
        )
        t2 = Task.objects.create(
            company=self.head,
            title="Task 2",
            created_by=self.actor,
        )
        result = execute_company_deletion(company=self.head, actor=self.actor)
        self.assertEqual(result["tasks_deleted_count"], 2)
        self.assertFalse(Task.objects.filter(id__in=[t1.id, t2.id]).exists())


class ExecuteCompanyDeletionErrorTests(TestCase):
    """IntegrityError на CompanySearchIndex → CompanyDeletionError."""

    def setUp(self):
        self.branch = _mk_branch("krd")
        self.actor = _mk_user("actor", role=User.Role.BRANCH_DIRECTOR)
        self.company = _mk_company(branch=self.branch, responsible=self.actor)

    def test_integrity_error_raises_custom_exception(self):
        pk = self.company.id
        with patch("companies.services.company_delete.CompanySearchIndex") as mock_idx:
            mock_idx.objects.filter.return_value.delete.side_effect = IntegrityError("boom")
            with self.assertRaises(CompanyDeletionError) as ctx:
                execute_company_deletion(company=self.company, actor=self.actor)
        self.assertIn("индексом", str(ctx.exception))
        # Компания должна остаться — транзакция откатилась
        self.assertTrue(Company.objects.filter(id=pk).exists())
