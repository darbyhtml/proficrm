"""Company notes CRUD + attachments + pin (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `company_note_add` — POST /companies/<uuid>/notes/add/
- `company_note_edit` — POST /companies/<uuid>/notes/<id>/edit/
- `company_note_delete` — POST /companies/<uuid>/notes/<id>/delete/
- `company_note_pin_toggle` — POST /companies/<uuid>/notes/<id>/pin-toggle/
- `company_note_attachment_open` — GET /companies/<uuid>/notes/<id>/attachment/
- `company_note_attachment_download` — GET /companies/<uuid>/notes/<id>/attachment/download/
- `company_note_attachment_by_id_open` — GET /companies/<uuid>/notes/<id>/attachment/<aid>/
- `company_note_attachment_by_id_download` — GET /companies/<uuid>/notes/<id>/attachment/<aid>/download/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    CompanyNote,
    CompanyNoteAttachment,
    CompanyNoteForm,
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    HttpResponseNotFound,
    Max,
    Q,
    User,
    _can_edit_company,
    _safe_next_v3,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    mimetypes,
    policy_required,
    redirect,
    require_can_view_company,
    require_can_view_note_company,
    timezone,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_pin_toggle(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на закрепление заметок по этой компании.")
        return redirect("company_detail", company_id=company.id)

    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    now = timezone.now()

    if note.is_pinned:
        note.is_pinned = False
        note.pinned_at = None
        note.pinned_by = None
        note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])
        messages.success(request, "Заметка откреплена.")
        log_event(
            actor=user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="note",
            entity_id=str(note.id),
            company_id=company.id,
            message="Откреплена заметка",
        )
        return redirect("company_detail", company_id=company.id)

    # Закрепляем: снимаем закрепление с других заметок (одна закреплённая на компанию)
    CompanyNote.objects.filter(company=company, is_pinned=True).exclude(id=note.id).update(
        is_pinned=False, pinned_at=None, pinned_by=None
    )
    note.is_pinned = True
    note.pinned_at = now
    note.pinned_by = user
    note.save(update_fields=["is_pinned", "pinned_at", "pinned_by"])

    messages.success(request, "Заметка закреплена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Закреплена заметка",
    )
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_open(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    """
    Открыть вложение заметки в новом окне (inline). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = (
            mimetypes.guess_type(note.attachment_name or note.attachment.name)[0]
            or "application/octet-stream"
        )
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=False,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_by_id_open(
    request: HttpRequest, company_id, note_id: int, attachment_id: int
) -> HttpResponse:
    """Открыть одно из вложений заметки (CompanyNoteAttachment) по id."""
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    att = get_object_or_404(CompanyNoteAttachment.objects.filter(note=note), id=attachment_id)
    if not att.file:
        raise Http404("Файл не найден")
    ctype = (
        (att.content_type or "").strip()
        or mimetypes.guess_type(att.file_name or att.file.name)[0]
        or "application/octet-stream"
    )
    try:
        return FileResponse(
            open(att.file.path, "rb"),
            as_attachment=False,
            filename=(att.file_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_by_id_download(
    request: HttpRequest, company_id, note_id: int, attachment_id: int
) -> HttpResponse:
    """Скачать одно из вложений заметки (CompanyNoteAttachment) по id."""
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    att = get_object_or_404(CompanyNoteAttachment.objects.filter(note=note), id=attachment_id)
    if not att.file:
        raise Http404("Файл не найден")
    ctype = (
        (att.content_type or "").strip()
        or mimetypes.guess_type(att.file_name or att.file.name)[0]
        or "application/octet-stream"
    )
    try:
        return FileResponse(
            open(att.file.path, "rb"),
            as_attachment=True,
            filename=(att.file_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="page", resource="ui:companies:detail")
@require_can_view_note_company
def company_note_attachment_download(
    request: HttpRequest, company_id, note_id: int
) -> HttpResponse:
    """
    Скачать вложение заметки (attachment). Доступ: всем пользователям (как просмотр компании).
    """
    company = get_object_or_404(Company.objects.all(), id=company_id)
    note = get_object_or_404(
        CompanyNote.objects.select_related("company"), id=note_id, company_id=company.id
    )
    if not note.attachment:
        raise Http404("Файл не найден")
    ctype = (note.attachment_content_type or "").strip()
    if not ctype:
        ctype = (
            mimetypes.guess_type(note.attachment_name or note.attachment.name)[0]
            or "application/octet-stream"
        )
    try:
        return FileResponse(
            open(note.attachment.path, "rb"),
            as_attachment=True,
            filename=(note.attachment_name or "file"),
            content_type=ctype,
        )
    except FileNotFoundError:
        return HttpResponseNotFound("Файл вложения не найден.")


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_add(request: HttpRequest, company_id) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    from companies.services import CompanyService

    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )

    # Заметки по карточке: доступно всем, кто имеет доступ к просмотру карточки (в проекте это все пользователи).
    form = CompanyNoteForm(request.POST, request.FILES)
    extra_files = request.FILES.getlist("attachments") or []
    if form.is_valid():
        note_data: CompanyNote = form.save(commit=False)
        CompanyService.add_note(
            company=company,
            user=user,
            text=note_data.text or "",
            attachment=note_data.attachment or None,
            extra_files=extra_files or None,
        )
    elif (
        extra_files
        and not (request.POST.get("text") or "").strip()
        and not request.FILES.get("attachment")
    ):
        # Только несколько файлов без текста и без одного attachment — создаём заметку вручную
        CompanyService.add_note(
            company=company,
            user=user,
            text="",
            extra_files=extra_files,
        )

    nxt = _safe_next_v3(request, company_id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company_id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_edit(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Редактировать заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(
            CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id
        )
    else:
        # Обычные пользователи могут редактировать свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(
            id=note_id, company_id=company.id
        )
        if company.responsible_id == user.id:
            # Ответственный может редактировать свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут редактировать только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)

    text = (request.POST.get("text") or "").strip()
    remove_attachment = (request.POST.get("remove_attachment") or "").strip() == "1"
    new_file = request.FILES.get("attachment")

    old_file = note.attachment  # storage object
    old_name = getattr(old_file, "name", "") if old_file else ""

    # Если попросили удалить файл — удалим привязку (и сам файл ниже)
    if remove_attachment and note.attachment:
        note.attachment = None
        note.attachment_name = ""
        note.attachment_ext = ""
        note.attachment_size = 0
        note.attachment_content_type = ""

    # Если загрузили новый файл — заменяем
    if new_file:
        note.attachment = new_file
        try:
            note.attachment_name = (
                (getattr(new_file, "name", "") or "").split("/")[-1].split("\\")[-1]
            )
            note.attachment_ext = (
                note.attachment_name.rsplit(".", 1)[-1].lower()
                if "." in note.attachment_name
                else ""
            )[:16]
            note.attachment_size = int(getattr(new_file, "size", 0) or 0)
            note.attachment_content_type = (getattr(new_file, "content_type", "") or "").strip()[
                :120
            ]
        except Exception as e:
            logger.warning(
                f"Ошибка при извлечении метаданных нового вложения заметки: {e}",
                exc_info=True,
                extra={
                    "company_id": str(company.id),
                    "note_id": note.id if hasattr(note, "id") else None,
                },
            )

    # Доп. вложения: удалить отмеченные
    remove_ids = (request.POST.get("remove_attachment_ids") or "").strip()
    if remove_ids:
        for att_id in remove_ids.split(","):
            try:
                aid = int(att_id.strip())
                att = CompanyNoteAttachment.objects.filter(id=aid, note=note).first()
                if att:
                    try:
                        if att.file:
                            att.file.delete(save=False)
                    except Exception:
                        pass
                    att.delete()
            except ValueError:
                pass

    # Новые доп. вложения
    next_order = (note.note_attachments.aggregate(m=Max("order"))["m"] or -1) + 1
    for i, f in enumerate(request.FILES.getlist("attachments") or []):
        try:
            att = CompanyNoteAttachment(note=note, file=f, order=next_order + i)
            att.save()
        except Exception as e:
            logger.warning(
                f"Ошибка при сохранении доп. вложения заметки: {e}",
                exc_info=True,
                extra={"company_id": str(company.id), "note_id": note.id},
            )

    has_attachments = (
        note.attachment or note.note_attachments.exists() or request.FILES.getlist("attachments")
    )
    if not text and not has_attachments:
        messages.error(request, "Заметка не может быть пустой (нужен текст или файл).")
        return redirect("company_detail", company_id=company.id)

    note.text = text
    note.edited_at = timezone.now()
    note.save()

    # Удаляем старый файл из storage, если он был удалён/заменён
    try:
        new_name = getattr(note.attachment, "name", "") if note.attachment else ""
        should_delete_old = bool(
            old_file
            and old_name
            and (remove_attachment or (new_file is not None))
            and old_name != new_name
        )
        if should_delete_old:
            old_file.delete(save=False)
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении старого файла вложения заметки: {e}",
            exc_info=True,
            extra={
                "company_id": str(company.id),
                "note_id": note.id if hasattr(note, "id") else None,
            },
        )

    messages.success(request, "Заметка обновлена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.UPDATE,
        entity_type="note",
        entity_id=str(note.id),
        company_id=company.id,
        message="Изменена заметка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
@require_can_view_company
def company_note_delete(request: HttpRequest, company_id, note_id: int) -> HttpResponse:
    if request.method != "POST":
        return redirect("company_detail", company_id=company_id)

    user: User = request.user
    company = get_object_or_404(Company.objects.all(), id=company_id)

    # Удалять заметки:
    # - админ/суперпользователь/управляющий: любые
    # - остальные: только свои ИЛИ заметки без автора (author=None), если пользователь - ответственный за компанию
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        note = get_object_or_404(
            CompanyNote.objects.select_related("author"), id=note_id, company_id=company.id
        )
    else:
        # Обычные пользователи могут удалять свои заметки или заметки без автора, если они ответственные за компанию
        note_qs = CompanyNote.objects.select_related("author").filter(
            id=note_id, company_id=company.id
        )
        if company.responsible_id == user.id:
            # Ответственный может удалять свои заметки и заметки без автора
            note = get_object_or_404(note_qs.filter(Q(author_id=user.id) | Q(author__isnull=True)))
        else:
            # Остальные могут удалять только свои заметки
            note = get_object_or_404(note_qs, author_id=user.id)
    # Удаляем вложенный файл из storage, затем запись
    try:
        if note.attachment:
            note.attachment.delete(save=False)
    except Exception as e:
        logger.warning(
            f"Ошибка при удалении файла вложения заметки: {e}",
            exc_info=True,
            extra={"company_id": str(company.id), "note_id": note_id},
        )
    note.delete()

    messages.success(request, "Заметка удалена.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="note",
        entity_id=str(note_id),
        company_id=company.id,
        message="Удалена заметка",
    )
    nxt = _safe_next_v3(request, company.id)
    if nxt:
        return redirect(nxt)
    return redirect("company_detail", company_id=company.id)
