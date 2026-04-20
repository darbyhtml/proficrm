"""F9 UI (2026-04-18): кастомная страница /admin/mobile-apps/ для
загрузки APK-билдов CRMProfiDialer.

Альтернатива /django-admin/phonebridge/mobileappbuild/ — та же функция,
но в кастомной v3-админке CRM без тех-интерфейса Django.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from accounts.permissions import require_admin
from audit.models import ActivityEvent
from audit.service import log_event
from phonebridge.models import MobileAppBuild

logger = logging.getLogger(__name__)


@login_required
def settings_mobile_apps(request: HttpRequest) -> HttpResponse:
    """Список всех загруженных APK + форма загрузки новой версии."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    builds = list(
        MobileAppBuild.objects.select_related("uploaded_by").order_by(
            "-version_code", "-uploaded_at"
        )[:50]
    )
    return render(
        request,
        "ui/settings/mobile_apps.html",
        {"builds": builds},
    )


@login_required
@require_POST
def settings_mobile_apps_upload(request: HttpRequest) -> HttpResponse:
    """Загрузка нового APK. Поля: version_name, version_code, file."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    version_name = (request.POST.get("version_name") or "").strip()
    version_code_raw = (request.POST.get("version_code") or "").strip()
    apk = request.FILES.get("file")

    if not version_name or not version_code_raw or not apk:
        messages.error(request, "Заполните version_name, version_code и приложите .apk-файл.")
        return redirect("settings_mobile_apps")

    try:
        version_code = int(version_code_raw)
    except ValueError:
        messages.error(
            request, f"version_code должен быть целым числом, получено: {version_code_raw}"
        )
        return redirect("settings_mobile_apps")

    if version_code < 1:
        messages.error(request, "version_code должен быть положительным.")
        return redirect("settings_mobile_apps")

    if not apk.name.lower().endswith(".apk"):
        messages.error(request, "Загружать можно только файлы .apk.")
        return redirect("settings_mobile_apps")

    if MobileAppBuild.objects.filter(version_code=version_code, env="production").exists():
        messages.error(
            request,
            f"APK с version_code={version_code} уже существует. "
            "Инкрементируйте version_code или удалите старую запись.",
        )
        return redirect("settings_mobile_apps")

    try:
        build = MobileAppBuild.objects.create(
            version_name=version_name,
            version_code=version_code,
            file=apk,
            env="production",
            is_active=True,
            uploaded_by=request.user,
        )
    except Exception as exc:
        logger.exception("Failed to upload APK")
        messages.error(request, f"Ошибка загрузки: {exc}")
        return redirect("settings_mobile_apps")

    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.CREATE,
            entity_type="mobile_app_build",
            entity_id=str(build.id),
            message=f"APK {version_name} (code {version_code}) загружен",
            meta={"sha256": build.sha256, "size": build.get_file_size()},
        )
    except Exception:
        logger.exception("Failed to write audit event for APK upload")

    messages.success(
        request,
        f"APK {version_name} (code {version_code}) загружен. SHA256: {build.sha256[:16]}…",
    )
    return redirect("settings_mobile_apps")


@login_required
@require_POST
def settings_mobile_apps_toggle(request: HttpRequest, build_id: str) -> HttpResponse:
    """Включить/выключить активность конкретного билда."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    try:
        build = MobileAppBuild.objects.get(id=build_id)
    except MobileAppBuild.DoesNotExist:
        messages.error(request, "Билд не найден.")
        return redirect("settings_mobile_apps")

    build.is_active = not build.is_active
    build.save(update_fields=["is_active"])

    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.UPDATE,
            entity_type="mobile_app_build",
            entity_id=str(build.id),
            message=f"APK {build.version_name} is_active={build.is_active}",
        )
    except Exception:
        pass

    state = "активен" if build.is_active else "отключён"
    messages.success(request, f"Билд {build.version_name}: {state}.")
    return redirect("settings_mobile_apps")
