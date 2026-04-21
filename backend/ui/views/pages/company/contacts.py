"""Contact CRUD (W1.2 refactor).

Extracted из `backend/ui/views/company_detail.py` в W1.2. Zero behavior change.

Endpoints:
- `contact_create` — GET/POST /companies/<uuid>/contacts/new/
- `contact_edit` — GET/POST /contacts/<uuid>/edit/
- `contact_delete` — POST /contacts/<uuid>/delete/
"""

from __future__ import annotations

import logging

from ui.views._base import (
    ActivityEvent,
    Company,
    Contact,
    ContactEmailFormSet,
    ContactForm,
    ContactPhoneFormSet,
    HttpRequest,
    HttpResponse,
    JsonResponse,
    User,
    _can_edit_company,
    get_object_or_404,
    log_event,
    login_required,
    messages,
    policy_required,
    redirect,
    render,
)

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_create(request: HttpRequest, company_id) -> HttpResponse:
    user: User = request.user
    company = get_object_or_404(
        Company.objects.select_related("responsible", "branch"), id=company_id
    )
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на добавление контактов в эту компанию.")
        return redirect("company_detail", company_id=company.id)

    contact = Contact(company=company)
    # Определяем модальный режим: по заголовку AJAX или параметру modal=1
    is_modal = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
        or (request.GET.get("modal") == "1")
        or (request.POST.get("modal") == "1")
    )

    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        email_fs = ContactEmailFormSet(request.POST, instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(request.POST, instance=contact, prefix="phones")
        if form.is_valid() and email_fs.is_valid() and phone_fs.is_valid():
            contact = form.save()
            email_fs.instance = contact
            phone_fs.instance = contact
            email_fs.save()
            phone_fs.save()
            messages.success(request, "Контакт добавлен.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.CREATE,
                entity_type="contact",
                entity_id=contact.id,
                company_id=company.id,
                message=f"Добавлен контакт: {contact}",
            )
            if is_modal:
                return JsonResponse({"ok": True, "redirect": f"/companies/{company.id}/"})
            return redirect("company_detail", company_id=company.id)
        if is_modal:
            from django.template.loader import render_to_string

            html = render_to_string(
                "ui/contact_form_modal.html",
                {
                    "company": company,
                    "form": form,
                    "email_fs": email_fs,
                    "phone_fs": phone_fs,
                    "mode": "create",
                },
                request=request,
            )
            return JsonResponse({"ok": False, "html": html}, status=400)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    context = {
        "company": company,
        "form": form,
        "email_fs": email_fs,
        "phone_fs": phone_fs,
        "mode": "create",
        "action_url": f"/companies/{company.id}/contacts/new/",
    }
    if is_modal:
        return render(request, "ui/contact_form_modal.html", context)
    return render(request, "ui/contact_form.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_edit(request: HttpRequest, contact_id) -> HttpResponse:
    user: User = request.user
    contact = get_object_or_404(
        Contact.objects.select_related("company", "company__responsible", "company__branch"),
        id=contact_id,
    )
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("company_list")
    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на редактирование контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    # Определяем модальный режим: по заголовку AJAX или параметру modal=1
    is_modal = (
        request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
        or request.headers.get("X-Requested-With", "").lower() == "xmlhttprequest"
        or (request.GET.get("modal") == "1")
        or (request.POST.get("modal") == "1")
    )

    if request.method == "POST":
        form = ContactForm(request.POST, instance=contact)
        email_fs = ContactEmailFormSet(request.POST, instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(request.POST, instance=contact, prefix="phones")
        if form.is_valid() and email_fs.is_valid() and phone_fs.is_valid():
            form.save()
            email_fs.save()
            phone_fs.save()
            messages.success(request, "Контакт обновлён.")
            log_event(
                actor=user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="contact",
                entity_id=contact.id,
                company_id=company.id,
                message=f"Обновлён контакт: {contact}",
            )
            if is_modal:
                return JsonResponse({"ok": True, "redirect": f"/companies/{company.id}/"})
            return redirect("company_detail", company_id=company.id)
        if is_modal:
            from django.template.loader import render_to_string

            html = render_to_string(
                "ui/contact_form_modal.html",
                {
                    "company": company,
                    "contact": contact,
                    "form": form,
                    "email_fs": email_fs,
                    "phone_fs": phone_fs,
                    "mode": "edit",
                },
                request=request,
            )
            return JsonResponse({"ok": False, "html": html}, status=400)
    else:
        form = ContactForm(instance=contact)
        email_fs = ContactEmailFormSet(instance=contact, prefix="emails")
        phone_fs = ContactPhoneFormSet(instance=contact, prefix="phones")

    context = {
        "company": company,
        "contact": contact,
        "form": form,
        "email_fs": email_fs,
        "phone_fs": phone_fs,
        "mode": "edit",
        "action_url": f"/contacts/{contact.id}/edit/",
    }
    if is_modal:
        return render(request, "ui/contact_form_modal.html", context)
    return render(request, "ui/contact_form.html", context)


@login_required
@policy_required(resource_type="action", resource="ui:companies:update")
def contact_delete(request: HttpRequest, contact_id) -> HttpResponse:
    """
    Удалить контакт компании.
    Доступно только ответственному за карточку.
    """
    if request.method != "POST":
        return redirect("dashboard")

    user: User = request.user
    contact = get_object_or_404(
        Contact.objects.select_related("company", "company__responsible"), id=contact_id
    )
    company = contact.company
    if not company:
        messages.error(request, "Контакт не привязан к компании.")
        return redirect("company_list")

    if not _can_edit_company(user, company):
        messages.error(request, "Нет прав на удаление контактов этой компании.")
        return redirect("company_detail", company_id=company.id)

    contact_name = str(contact)
    contact.delete()

    messages.success(request, f"Контакт '{contact_name}' удалён.")
    log_event(
        actor=user,
        verb=ActivityEvent.Verb.DELETE,
        entity_type="contact",
        entity_id=str(contact_id),
        company_id=company.id,
        message=f"Удалён контакт: {contact_name}",
    )
    return redirect("company_detail", company_id=company.id)
