"""
Views: HTML-превью, вложения, экспорт ошибок, повторная отправка.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from accounts.models import User
from mailer.mail_content import apply_signature
from mailer.models import Campaign, CampaignQueue, CampaignRecipient
from mailer.utils import html_to_text
from mailer.views._helpers import _can_manage_campaign
from policy.engine import enforce

logger = logging.getLogger(__name__)


@login_required
def campaign_html_preview(request: HttpRequest, campaign_id) -> HttpResponse:
    """
    Возвращает полное HTML письма для отображения в <iframe>.
    Открывается во вкладке или во встроенном фрейме в UI.
    """
    enforce(
        user=request.user,
        resource_type="page",
        resource="ui:mail:campaigns:detail",
        context={"path": request.path},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        raise Http404()

    preview_html = camp.body_html or ""
    if preview_html:
        from mailer.utils import html_to_text

        auto_plain = html_to_text(preview_html)
        preview_html, _ = apply_signature(
            user=user, body_html=preview_html, body_text=auto_plain or camp.body_text or ""
        )

    return render(
        request,
        "ui/mail/html_preview.html",
        {
            "campaign": camp,
            "preview_html": preview_html,
        },
    )


@login_required
def campaign_attachment_download(request: HttpRequest, campaign_id) -> HttpResponse:
    """Скачивание вложения кампании с оригинальным именем файла."""
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:attachment:download",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        raise Http404()
    if not camp.attachment:
        raise Http404()
    fname = (camp.attachment_original_name or "").strip() or (
        camp.attachment.name.split("/")[-1] if camp.attachment.name else "attachment"
    )
    try:
        f = camp.attachment.open("rb")
    except Exception:
        f = camp.attachment.open()
    return FileResponse(f, as_attachment=True, filename=fname)


@login_required
def campaign_attachment_delete(request: HttpRequest, campaign_id) -> HttpResponse:
    """AJAX удаление вложения кампании."""
    if request.method != "POST":
        from django.http import JsonResponse

        return JsonResponse({"success": False, "error": "Метод не разрешен"}, status=405)

    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:edit",
        context={"path": request.path, "method": request.method},
    )
    user: User = request.user
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(user, camp):
        from django.http import JsonResponse

        return JsonResponse({"success": False, "error": "Доступ запрещён"}, status=403)

    if not camp.attachment:
        from django.http import JsonResponse

        return JsonResponse({"success": False, "error": "Вложение не найдено"}, status=404)

    try:
        camp.attachment.delete(save=False)
        camp.attachment = None
        camp.attachment_original_name = ""
        camp.save(update_fields=["attachment", "attachment_original_name", "updated_at"])
        from django.http import JsonResponse

        return JsonResponse({"success": True})
    except Exception as e:
        logger.error(f"Ошибка при удалении вложения кампании {camp.id}: {e}")
        from django.http import JsonResponse

        return JsonResponse({"success": False, "error": "Ошибка при удалении файла"}, status=500)


@login_required
def campaign_export_failed(request: HttpRequest, campaign_id) -> HttpResponse:
    """Скачать список FAILED получателей в CSV."""
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:export_failed",
        context={"path": request.path, "method": request.method},
    )
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(request.user, camp):
        messages.error(request, "Нет прав.")
        return redirect("campaign_detail", campaign_id=camp.id)
    import csv
    import re

    from django.http import StreamingHttpResponse

    def rows():
        yield ["email", "ошибка", "дата"]
        for r in (
            camp.recipients.filter(status=CampaignRecipient.Status.FAILED)
            .order_by("email")
            .values_list("email", "last_error", "updated_at")
        ):
            yield [r[0], r[1] or "", r[2].strftime("%d.%m.%Y %H:%M") if r[2] else ""]

    class Echo:
        def write(self, value):
            return value

    writer = csv.writer(Echo())
    response = StreamingHttpResponse(
        (writer.writerow(row) for row in rows()),
        content_type="text/csv; charset=utf-8-sig",
    )
    safe_name = re.sub(r"[^\w\s\-]", "", camp.name[:50]).strip()
    response["Content-Disposition"] = f'attachment; filename="failed_{safe_name}.csv"'
    return response


@login_required
def campaign_retry_failed(request: HttpRequest, campaign_id) -> HttpResponse:
    """Повторная отправка: переводит всех FAILED получателей обратно в PENDING."""
    enforce(
        user=request.user,
        resource_type="action",
        resource="ui:mail:campaigns:retry_failed",
        context={"path": request.path, "method": request.method},
    )
    if request.method != "POST":
        return redirect("campaign_detail", campaign_id=campaign_id)
    camp = get_object_or_404(Campaign, id=campaign_id)
    if not _can_manage_campaign(request.user, camp):
        messages.error(request, "Нет прав на управление этой кампанией.")
        return redirect("campaign_detail", campaign_id=camp.id)
    updated = camp.recipients.filter(status=CampaignRecipient.Status.FAILED).update(
        status=CampaignRecipient.Status.PENDING,
        last_error="",
    )
    if updated:
        camp.status = Campaign.Status.READY
        camp.save(update_fields=["status", "updated_at"])
        queue, created = CampaignQueue.objects.get_or_create(
            campaign=camp,
            defaults={"status": CampaignQueue.Status.PENDING, "priority": 0},
        )
        if not created and queue.status not in (
            CampaignQueue.Status.PENDING,
            CampaignQueue.Status.PROCESSING,
        ):
            queue.status = CampaignQueue.Status.PENDING
            queue.deferred_until = None
            queue.defer_reason = ""
            queue.save(update_fields=["status", "deferred_until", "defer_reason"])
        messages.success(request, f"Повторная отправка запланирована для {updated} получателей.")
    else:
        messages.info(request, "Нет получателей с ошибками для повторной отправки.")
    return redirect("campaign_detail", campaign_id=camp.id)
