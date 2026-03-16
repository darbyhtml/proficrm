"""
Сервисный слой для задач (Task).

Цель: изолировать бизнес-логику от HTTP-слоя,
чтобы она была переиспользуема из UI-views, DRF API, management commands, тестов.
"""
from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

from accounts.models import User
from audit.models import ActivityEvent
from audit.service import log_event
from tasksapp.models import Task, TaskComment, TaskEvent

logger = logging.getLogger(__name__)


class TaskService:
    """Сервис для работы с задачами."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def create_note_from_task(task: Task, user: User):
        """
        Создать заметку компании из задачи.
        Возвращает CompanyNote.
        Выбрасывает ValueError если у задачи нет company.
        """
        from companies.models import CompanyNote

        if not task.company_id:
            raise ValueError("У задачи нет компании — нельзя создать заметку.")

        note_parts = []
        if task.type:
            note_parts.append(f"Задача: {task.type.name}")
        elif task.title:
            note_parts.append(f"Задача: {task.title}")
        else:
            note_parts.append("Задача: Без типа")
        if task.description:
            note_parts.append(f"\n{task.description}")
        if task.due_at:
            note_parts.append(f"\nДедлайн: {task.due_at.strftime('%d.%m.%Y %H:%M')}")

        return CompanyNote.objects.create(
            company=task.company,
            author=user,
            text="\n".join(note_parts),
        )

    # ------------------------------------------------------------------
    # set_status
    # ------------------------------------------------------------------

    @staticmethod
    def set_status(
        *,
        task: Task,
        user: User,
        new_status: str,
        save_to_notes: bool = False,
    ) -> dict[str, Any]:
        """
        Изменить статус задачи.

        Возвращает dict:
          {
            "changed": bool,
            "note_created": bool,
            "note": CompanyNote | None,
          }

        Выбрасывает ValueError для некорректного статуса.
        Создаёт TaskEvent, опциональную заметку и уведомления.
        """
        valid_statuses = {s for s, _ in Task.Status.choices}
        if new_status not in valid_statuses:
            raise ValueError(f"Некорректный статус: {new_status!r}")

        old_status = task.status
        note = None

        # Опциональная заметка при переходе в DONE
        if new_status == Task.Status.DONE and save_to_notes and task.company_id:
            note = TaskService.create_note_from_task(task, user)
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.COMMENT,
                entity_type="note",
                entity_id=note.id,
                company_id=task.company_id,
                message="Добавлена заметка из выполненной задачи",
            )

        task.status = new_status
        if new_status == Task.Status.DONE:
            task.completed_at = timezone.now()
        task.save(update_fields=["status", "completed_at", "updated_at"])

        # История
        old_label = dict(Task.Status.choices).get(old_status, old_status)
        new_label = dict(Task.Status.choices).get(new_status, new_status)
        TaskEvent.objects.create(
            task=task,
            actor=user,
            kind=TaskEvent.Kind.STATUS_CHANGED,
            old_value=old_label,
            new_value=new_label,
        )

        # Лог
        if task.company_id:
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.STATUS,
                entity_type="task",
                entity_id=task.id,
                company_id=task.company_id,
                message=f"Статус: {task.get_status_display()}",
                meta={"status": new_status},
            )

        # Уведомления
        task_url = f"/tasks/?view_task={task.id}"
        TaskService._notify_status_change(
            task=task, user=user, new_status=new_status, task_url=task_url
        )

        return {"changed": True, "note_created": note is not None, "note": note}

    @staticmethod
    def _notify_status_change(*, task: Task, user: User, new_status: str, task_url: str) -> None:
        """Отправить уведомления об изменении статуса задачи."""
        try:
            from notifications.models import Notification
            from notifications.service import notify

            if new_status == Task.Status.DONE:
                recipient_ids: set[int] = {user.id}
                if task.assigned_to_id:
                    recipient_ids.add(task.assigned_to_id)
                if task.created_by_id:
                    recipient_ids.add(task.created_by_id)

                branch_id = None
                if task.company_id and getattr(task, "company", None):
                    branch_id = getattr(task.company, "branch_id", None)
                if not branch_id and getattr(task, "assigned_to", None):
                    branch_id = getattr(task.assigned_to, "branch_id", None)

                if branch_id:
                    for uid in User.objects.filter(
                        is_active=True,
                        role__in=[User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD],
                        branch_id=branch_id,
                    ).values_list("id", flat=True):
                        recipient_ids.add(int(uid))

                for uid in User.objects.filter(
                    is_active=True, role=User.Role.GROUP_MANAGER
                ).values_list("id", flat=True):
                    recipient_ids.add(int(uid))

                for uid in recipient_ids:
                    try:
                        u = User.objects.get(id=uid, is_active=True)
                    except User.DoesNotExist:
                        continue
                    notify(
                        user=u,
                        kind=Notification.Kind.TASK,
                        title="Задача выполнена",
                        body=task.title,
                        url=task_url,
                    )
            else:
                if task.created_by_id and task.created_by_id != user.id:
                    notify(
                        user=task.created_by,
                        kind=Notification.Kind.TASK,
                        title="Статус изменён",
                        body=f"{task.title}: {task.get_status_display()}",
                        url=task_url,
                    )
        except Exception as e:
            logger.warning("Ошибка при отправке уведомления об изменении статуса: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # delete_task
    # ------------------------------------------------------------------

    @staticmethod
    def delete_task(*, task: Task, user: User, save_to_notes: bool = False) -> dict[str, Any]:
        """
        Удалить задачу, опционально создав заметку компании.

        Возвращает dict:
          {
            "note_created": bool,
            "note": CompanyNote | None,
            "title": str,
            "company_id": int | None,
          }
        """
        title = task.title
        company_id = task.company_id
        note = None

        if save_to_notes and company_id:
            note = TaskService.create_note_from_task(task, user)
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.COMMENT,
                entity_type="note",
                entity_id=note.id,
                company_id=company_id,
                message="Добавлена заметка из задачи",
            )

        task.delete()

        log_event(
            actor=user,
            verb=ActivityEvent.Verb.DELETE,
            entity_type="task",
            entity_id=str(task.id),
            company_id=company_id,
            message=f"Удалена задача: {title}",
        )

        return {
            "note_created": note is not None,
            "note": note,
            "title": title,
            "company_id": company_id,
        }

    # ------------------------------------------------------------------
    # add_comment
    # ------------------------------------------------------------------

    @staticmethod
    def add_comment(*, task: Task, user: User, text: str) -> TaskComment:
        """
        Добавить комментарий к задаче.
        Выбрасывает ValueError если текст пустой.
        """
        text = (text or "").strip()
        if not text:
            raise ValueError("Комментарий не может быть пустым.")
        return TaskComment.objects.create(task=task, author=user, text=text)
