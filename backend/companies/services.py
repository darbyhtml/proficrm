"""
Сервисы для бизнес-логики работы с компаниями.

Цель: единообразная логика для UI и API, устранение расхождений.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.models import User
from audit.service import log_event
from audit.models import ActivityEvent
from companies.models import Company, CompanyPhone, CompanyEmail, CompanyNote
from companies.permissions import can_edit_company, can_transfer_company
from crm.request_id_middleware import get_request_id

logger = logging.getLogger(__name__)


class CompanyService:
    """Сервис для работы с компаниями."""
    
    @staticmethod
    def update_phone(
        *,
        company: Company,
        user: User,
        phone: str,
        normalize_func: Callable[[str], str] | None = None,
    ) -> dict[str, Any]:
        """
        Обновить основной телефон компании.
        
        Args:
            company: Компания для обновления
            user: Пользователь, выполняющий операцию
            phone: Новый номер телефона
            normalize_func: Функция нормализации телефона (опционально)
        
        Returns:
            dict с результатом операции
        
        Raises:
            PermissionDenied: если нет прав на редактирование
            ValidationError: если телефон некорректен или дублируется
        """
        if not can_edit_company(user, company):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Нет прав на редактирование этой компании")
        
        # Нормализация телефона
        if normalize_func:
            normalized = normalize_func(phone) if phone else ""
        else:
            normalized = phone.strip() if phone else ""
        
        # Проверка дублей с дополнительными телефонами
        if normalized:
            exists = CompanyPhone.objects.filter(company=company, value=normalized).exists()
            if exists:
                raise ValidationError("Такой телефон уже есть в дополнительных номерах")
        
        # Обновление
        old_phone = company.phone
        company.phone = normalized
        company.save(update_fields=["phone", "updated_at"])
        
        # Логирование
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message="Обновлен основной телефон",
            meta={
                "old_phone": old_phone,
                "new_phone": normalized,
                "request_id": get_request_id(),
            },
        )
        
        return {
            "success": True,
            "phone": normalized,
            "company_id": str(company.id),
        }
    
    @staticmethod
    def update_email(
        *,
        company: Company,
        user: User,
        email: str,
    ) -> dict[str, Any]:
        """
        Обновить основной email компании.
        
        Args:
            company: Компания для обновления
            user: Пользователь, выполняющий операцию
            email: Новый email адрес
        
        Returns:
            dict с результатом операции
        
        Raises:
            PermissionDenied: если нет прав на редактирование
            ValidationError: если email некорректен или дублируется
        """
        if not can_edit_company(user, company):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Нет прав на редактирование этой компании")
        
        email = email.strip().lower() if email else ""
        
        if email:
            from django.core.validators import validate_email
            try:
                validate_email(email)
            except ValidationError:
                raise ValidationError("Некорректный email")
            
            # Проверка дублей с дополнительными email
            if CompanyEmail.objects.filter(company=company, value__iexact=email).exists():
                raise ValidationError("Такой email уже есть в дополнительных адресах")
        
        # Обновление
        old_email = company.email
        company.email = email
        company.save(update_fields=["email", "updated_at"])
        
        # Логирование
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message="Обновлен основной email",
            meta={
                "old_email": old_email,
                "new_email": email,
                "request_id": get_request_id(),
            },
        )
        
        return {
            "success": True,
            "email": email,
            "company_id": str(company.id),
        }
    
    @staticmethod
    @transaction.atomic
    def transfer(
        *,
        company: Company,
        user: User,
        new_responsible: User,
    ) -> dict[str, Any]:
        """
        Передать компанию другому ответственному.
        
        Args:
            company: Компания для передачи
            user: Пользователь, выполняющий операцию
            new_responsible: Новый ответственный
        
        Returns:
            dict с результатом операции
        
        Raises:
            PermissionDenied: если нет прав на передачу
            ValidationError: если новый ответственный некорректен
        """
        if not can_transfer_company(user, company):
            from django.core.exceptions import PermissionDenied
            raise PermissionDenied("Нет прав на передачу компании")
        
        if new_responsible.role not in (User.Role.MANAGER, User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD):
            raise ValidationError("Нового ответственного можно выбрать только из: менеджер / директор филиала / РОП")
        
        old_responsible = company.responsible
        old_branch = company.branch
        
        # Обновление
        company.responsible = new_responsible
        # При передаче обновляем филиал компании под филиал нового ответственного
        if new_responsible.branch:
            company.branch = new_responsible.branch
        company.save(update_fields=["responsible", "branch", "updated_at"])
        
        # Логирование
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message=f"Компания передана от {old_responsible} к {new_responsible}",
            meta={
                "old_responsible_id": str(old_responsible.id) if old_responsible else None,
                "new_responsible_id": str(new_responsible.id),
                "old_branch_id": str(old_branch.id) if old_branch else None,
                "new_branch_id": str(new_responsible.branch.id) if new_responsible.branch else None,
                "request_id": get_request_id(),
            },
        )
        
        return {
            "success": True,
            "company_id": str(company.id),
            "old_responsible_id": str(old_responsible.id) if old_responsible else None,
            "new_responsible_id": str(new_responsible.id),
        }
    
    @staticmethod
    def add_note(
        *,
        company: Company,
        user: User,
        text: str,
        attachment: Any | None = None,
    ) -> CompanyNote:
        """
        Добавить заметку к компании.
        
        Args:
            company: Компания
            user: Автор заметки
            text: Текст заметки
            attachment: Вложение (опционально)
        
        Returns:
            Созданная заметка
        
        Raises:
            PermissionDenied: если нет прав на просмотр компании
        """
        # Проверка прав на просмотр (заметки может добавлять любой, кто видит компанию)
        # В реальности это проверяется через policy_required на уровне view
        
        note = CompanyNote.objects.create(
            company=company,
            author=user,
            text=text,
            attachment=attachment,
        )
        
        # Обработка метаданных вложения
        if attachment:
            try:
                note.attachment_name = (getattr(attachment, "name", "") or "").split("/")[-1].split("\\")[-1]
                note.attachment_ext = (note.attachment_name.rsplit(".", 1)[-1].lower() if "." in note.attachment_name else "")[:16]
                note.attachment_size = int(getattr(attachment, "size", 0) or 0)
                note.attachment_content_type = (getattr(attachment, "content_type", "") or "").strip()[:120]
                note.save(update_fields=["attachment_name", "attachment_ext", "attachment_size", "attachment_content_type"])
            except Exception as e:
                logger.warning(
                    f"Ошибка при извлечении метаданных вложения заметки: {e}",
                    exc_info=True,
                    extra={"company_id": str(company.id), "note_id": note.id, "request_id": get_request_id()},
                )
        
        # Логирование
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.COMMENT,
            entity_type="note",
            entity_id=note.id,
            company_id=company.id,
            message="Добавлена заметка",
            meta={"request_id": get_request_id()},
        )
        
        return note
