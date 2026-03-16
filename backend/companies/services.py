"""
Сервисы для бизнес-логики работы с компаниями.

Цель: единообразная логика для UI и API, устранение расхождений.
"""
from __future__ import annotations

import logging
from typing import Any, Callable

from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import User
from audit.service import log_event
from audit.models import ActivityEvent
from companies.models import Company, CompanyPhone, CompanyEmail, CompanyNote, CompanyNoteAttachment, CompanyHistoryEvent
from companies.permissions import can_edit_company, can_transfer_company
from crm.request_id_middleware import get_request_id

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Чистые функции (без сайд-эффектов)
# ---------------------------------------------------------------------------

def get_contract_alert(company: Company) -> tuple[str, int | None]:
    """
    Вычисляет уровень тревоги для договора компании.

    Returns:
        (alert_level, days_left) — alert_level: "" | "warn" | "danger", days_left: int или None
    """
    if not company.contract_until:
        return "", None

    today = timezone.localdate(timezone.now())
    days_left = (company.contract_until - today).days

    if company.contract_type:
        warning_days = company.contract_type.warning_days
        danger_days = company.contract_type.danger_days
        if days_left <= danger_days:
            return "danger", days_left
        if days_left <= warning_days:
            return "warn", days_left
    else:
        # Fallback на жёстко заданные пороги
        if days_left < 14:
            return "danger", days_left
        if days_left <= 30:
            return "warn", days_left

    return "", days_left


def get_worktime_status(company: Company) -> dict:
    """
    Вычисляет статус рабочего времени для компании.

    Returns:
        dict с ключами: has (bool), status (str|None), label (str)
    """
    worktime: dict = {
        "has": bool(company.work_schedule),
        "status": None,
        "label": "",
    }
    if not company.work_schedule:
        return worktime

    try:
        from zoneinfo import ZoneInfo
        from ui.timezone_utils import guess_ru_timezone_from_address
        from core.work_schedule_utils import get_worktime_status_from_schedule

        guessed = guess_ru_timezone_from_address(company.address or "")
        tz_name = (((company.work_timezone or "").strip()) or guessed or "Europe/Moscow").strip()
        tz = ZoneInfo(tz_name)
        now_tz = timezone.now().astimezone(tz)

        status, _mins = get_worktime_status_from_schedule(company.work_schedule, now_tz=now_tz)
        worktime["status"] = status
        worktime["label"] = {
            "ok": "Рабочее время",
            "warn_end": "Остался час",
            "off": "Не рабочее время",
        }.get(status, "")
    except Exception:
        worktime["status"] = "unknown"
        worktime["label"] = ""

    return worktime


def get_org_root(company: Company) -> Company:
    """
    Возвращает "корень" организации для переданной компании.

    Организация = головная компания + все её филиалы.
    Если у компании есть head_company — она филиал, и корнем считается головная.
    Иначе сама компания и есть головная.
    """
    if not company:
        raise ValueError("company is required")
    return company.head_company or company


def get_org_companies(root: Company):
    """
    Возвращает queryset всех компаний организации:
    - головная (root)
    - все компании, у которых head_company_id = root.id

    ВАЖНО: всегда .distinct() по БД, чтобы убрать возможные дубли.
    """
    if not root:
        return Company.objects.none()
    return Company.objects.filter(Q(id=root.id) | Q(head_company_id=root.id)).distinct()


def resolve_target_companies(
    selected_company: Company | None,
    apply_to_org_branches: bool,
) -> list[Company]:
    """
    Определяет целевые компании для создания задач.

    Логика:
    - если apply_to_org_branches = False → только выбранная компания;
    - если True → вся организация (root + её филиалы).

    Всегда возвращает список без дублей по id, порядок устойчивый.
    """
    if not selected_company:
        return []

    if not apply_to_org_branches:
        return [selected_company]

    root = get_org_root(selected_company)
    qs = get_org_companies(root)

    seen_ids: set = set()
    targets: list[Company] = []
    for c in qs:
        if c.id in seen_ids:
            continue
        seen_ids.add(c.id)
        targets.append(c)
    return targets


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

        # Обновление: всегда синхронизируем филиал под нового ответственного
        company.responsible = new_responsible
        company.branch = new_responsible.branch
        company.save(update_fields=["responsible", "branch", "updated_at"])

        # Инвалидируем кэш счётчиков (смена ответственного/филиала)
        cache.delete("companies_total_count")

        # История передвижения карточки
        CompanyHistoryEvent.objects.create(
            company=company,
            event_type=CompanyHistoryEvent.EventType.ASSIGNED,
            source=CompanyHistoryEvent.Source.LOCAL,
            actor=user,
            actor_name=str(user),
            from_user=old_responsible,
            from_user_name=str(old_responsible) if old_responsible else "",
            to_user=new_responsible,
            to_user_name=str(new_responsible),
            occurred_at=timezone.now(),
        )

        # Уведомление новому ответственному (если это не сам инициатор)
        if new_responsible.id != user.id:
            from notifications.models import Notification
            from notifications.service import notify
            notify(
                user=new_responsible,
                kind=Notification.Kind.COMPANY,
                title="Вам передали компанию",
                body=str(company.name),
                url=f"/companies/{company.id}/",
            )

        # Логирование
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="company",
            entity_id=company.id,
            company_id=company.id,
            message="Изменён ответственный компании",
            meta={
                "from": str(old_responsible) if old_responsible else "",
                "to": str(new_responsible),
                "request_id": get_request_id(),
            },
        )

        logger.info(
            "Company transferred: company_id=%s, old_responsible_id=%s, "
            "new_responsible_id=%s, transferred_by_user_id=%s",
            company.id,
            old_responsible.id if old_responsible else None,
            new_responsible.id,
            user.id,
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
        extra_files: list | None = None,
    ) -> CompanyNote:
        """
        Добавить заметку к компании.

        Args:
            company: Компания
            user: Автор заметки
            text: Текст заметки
            attachment: Основное вложение (опционально)
            extra_files: Дополнительные файлы → CompanyNoteAttachment (опционально)

        Returns:
            Созданная заметка
        """
        note = CompanyNote.objects.create(
            company=company,
            author=user,
            text=text,
            attachment=attachment,
        )

        # Метаданные основного вложения
        if attachment:
            try:
                note.attachment_name = (getattr(attachment, "name", "") or "").split("/")[-1].split("\\")[-1]
                note.attachment_ext = (note.attachment_name.rsplit(".", 1)[-1].lower() if "." in note.attachment_name else "")[:16]
                note.attachment_size = int(getattr(attachment, "size", 0) or 0)
                note.attachment_content_type = (getattr(attachment, "content_type", "") or "").strip()[:120]
                note.save(update_fields=["attachment_name", "attachment_ext", "attachment_size", "attachment_content_type"])
            except Exception as e:
                logger.warning(
                    "Ошибка при извлечении метаданных вложения заметки: %s",
                    e,
                    exc_info=True,
                    extra={"company_id": str(company.id), "note_id": note.id, "request_id": get_request_id()},
                )

        # Дополнительные вложения
        for order, f in enumerate(extra_files or []):
            try:
                CompanyNoteAttachment.objects.create(note=note, file=f, order=order)
            except Exception as e:
                logger.warning(
                    "Ошибка при сохранении доп. вложения заметки: %s",
                    e,
                    exc_info=True,
                    extra={"company_id": str(company.id), "note_id": note.id},
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

        # Уведомление ответственному (если это не он сам)
        if company.responsible_id and company.responsible_id != user.id:
            try:
                from notifications.models import Notification
                from notifications.service import notify
                notify(
                    user=company.responsible,
                    kind=Notification.Kind.COMPANY,
                    title="Новая заметка по компании",
                    body=f"{company.name}: {(text or '').strip()[:180] or 'Вложение'}",
                    url=f"/companies/{company.id}/",
                )
            except Exception as e:
                logger.warning("Ошибка при отправке уведомления о заметке: %s", e, exc_info=True)

        return note
