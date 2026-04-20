"""
Тесты для generate_recurring_tasks (tasksapp/tasks.py).

Покрытие:
  1. _parse_rrule_occurrences — парсинг RRULE, диапазон, невалидная строка
  2. generate_recurring_tasks — создание экземпляров, защита от дублей,
     обновление recurrence_next_generate_after, пропуск без RRULE
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from accounts.models import User
from tasksapp.models import Task, TaskType
from tasksapp.tasks import _parse_rrule_occurrences, generate_recurring_tasks, HORIZON_DAYS


def _make_user(username="tpluser"):
    return User.objects.create_user(username=username, password="pass", role=User.Role.MANAGER)


def _make_template(user, rrule, due_at=None, title="Template"):
    return Task.objects.create(
        title=title,
        status=Task.Status.NEW,
        created_by=user,
        assigned_to=user,
        recurrence_rrule=rrule,
        due_at=due_at or timezone.now(),
    )


# ---------------------------------------------------------------------------
# 1. _parse_rrule_occurrences
# ---------------------------------------------------------------------------


class ParseRRuleTest(TestCase):
    def _bounds(self, days=7):
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        after = start - timedelta(seconds=1)  # включаем start в выборку
        until = start + timedelta(days=days)
        return start, after, until

    def test_daily_generates_correct_count(self):
        start, after, until = self._bounds(days=4)
        result = _parse_rrule_occurrences("FREQ=DAILY", dtstart=start, after=after, until=until)
        self.assertEqual(len(result), 5)  # start, +1, +2, +3, +4

    def test_weekly_generates_correct_count(self):
        start, after, until = self._bounds(days=13)
        result = _parse_rrule_occurrences("FREQ=WEEKLY", dtstart=start, after=after, until=until)
        self.assertEqual(len(result), 2)  # start, start+7

    def test_count_limits_occurrences(self):
        start, after, until = self._bounds(days=100)
        result = _parse_rrule_occurrences(
            "FREQ=DAILY;COUNT=3", dtstart=start, after=after, until=until
        )
        self.assertEqual(len(result), 3)

    def test_after_excludes_already_generated(self):
        """after исключителен: вхождение в after не возвращается."""
        start = timezone.now().replace(hour=10, minute=0, second=0, microsecond=0)
        after = start  # first occurrence == after → excluded
        until = start + timedelta(days=3)
        result = _parse_rrule_occurrences("FREQ=DAILY", dtstart=start, after=after, until=until)
        self.assertEqual(len(result), 3)  # +1, +2, +3 (start excluded)

    def test_invalid_rrule_returns_empty(self):
        start = timezone.now()
        result = _parse_rrule_occurrences(
            "NOT_VALID_RRULE",
            dtstart=start,
            after=start - timedelta(seconds=1),
            until=start + timedelta(days=7),
        )
        self.assertEqual(result, [])

    def test_empty_string_returns_empty(self):
        start = timezone.now()
        result = _parse_rrule_occurrences(
            "", dtstart=start, after=start - timedelta(seconds=1), until=start + timedelta(days=7)
        )
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# 2. generate_recurring_tasks
# ---------------------------------------------------------------------------


class GenerateRecurringTasksTest(TestCase):
    def setUp(self):
        self.user = _make_user()

    def test_creates_instances_for_daily_template(self):
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=3", due_at=due)
        result = generate_recurring_tasks()
        instances = Task.objects.filter(parent_recurring_task=template)
        self.assertEqual(instances.count(), 3)
        self.assertEqual(result["created"], 3)

    def test_instances_have_empty_rrule(self):
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=2", due_at=due)
        generate_recurring_tasks()
        for inst in Task.objects.filter(parent_recurring_task=template):
            self.assertEqual(inst.recurrence_rrule, "")

    def test_instances_inherit_title_and_assigned_to(self):
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=1", due_at=due, title="Шаблон")
        generate_recurring_tasks()
        inst = Task.objects.get(parent_recurring_task=template)
        self.assertEqual(inst.title, "Шаблон")
        self.assertEqual(inst.assigned_to, self.user)

    def test_no_duplicate_on_second_run(self):
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=2", due_at=due)
        generate_recurring_tasks()
        generate_recurring_tasks()
        count = Task.objects.filter(parent_recurring_task=template).count()
        self.assertEqual(count, 2)  # не задвоилось

    def test_updates_next_generate_after(self):
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=2", due_at=due)
        generate_recurring_tasks()
        template.refresh_from_db()
        self.assertIsNotNone(template.recurrence_next_generate_after)

    def test_task_without_rrule_not_processed(self):
        Task.objects.create(
            title="Обычная задача",
            status=Task.Status.NEW,
            created_by=self.user,
            assigned_to=self.user,
            recurrence_rrule="",
        )
        result = generate_recurring_tasks()
        self.assertEqual(result["created"], 0)

    def test_generated_instance_not_treated_as_template(self):
        """Сгенерированные экземпляры (parent_recurring_task != NULL) не порождают новые экземпляры."""
        due = timezone.now() - timedelta(hours=1)
        template = _make_template(self.user, "FREQ=DAILY;COUNT=1", due_at=due)
        generate_recurring_tasks()
        instance = Task.objects.get(parent_recurring_task=template)
        # Ставим rrule на экземпляр — не должно обрабатываться
        instance.recurrence_rrule = "FREQ=DAILY;COUNT=5"
        instance.save()
        result = generate_recurring_tasks()
        # Только оригинальный шаблон в templates, экземпляр пропускается
        self.assertEqual(Task.objects.filter(parent_recurring_task=instance).count(), 0)
