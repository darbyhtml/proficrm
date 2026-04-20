from __future__ import annotations
from phonebridge.models import MobileAppBuild, MobileAppQrToken
from ui.views._base import (
    ActivityEvent,
    FileResponse,
    Http404,
    HttpRequest,
    HttpResponse,
    cache,
    get_object_or_404,
    log_event,
    login_required,
    policy_required,
    render,
)
import logging

logger = logging.getLogger(__name__)


@login_required
@policy_required(resource_type="page", resource="ui:mobile_app")
def mobile_app_page(request: HttpRequest) -> HttpResponse:
    """
    Страница мобильного приложения: скачивание APK и QR-вход.
    Доступна всем авторизованным пользователям.
    """
    from accounts.security import get_client_ip

    # Получаем последнюю production версию
    latest_build = (
        MobileAppBuild.objects.filter(env="production", is_active=True)
        .order_by("-uploaded_at")
        .first()
    )

    # Получаем список всех версий (последние 10)
    builds = MobileAppBuild.objects.filter(env="production", is_active=True).order_by(
        "-uploaded_at"
    )[:10]

    return render(
        request,
        "ui/mobile_app.html",
        {
            "latest_build": latest_build,
            "builds": builds,
        },
    )


@login_required
@policy_required(resource_type="action", resource="ui:mobile_app:download")
def mobile_app_download(request: HttpRequest, build_id) -> HttpResponse:
    """
    Скачивание APK файла. Только для авторизованных пользователей.
    """
    from accounts.security import get_client_ip

    build = get_object_or_404(MobileAppBuild, id=build_id, env="production", is_active=True)

    if not build.file:
        raise Http404("Файл не найден")

    # Логируем скачивание
    try:
        log_event(
            actor=request.user,
            verb=ActivityEvent.Verb.VIEW,
            entity_type="mobile_app",
            entity_id=str(build.id),
            message=f"Скачана версия {build.version_name} ({build.version_code})",
            meta={
                "version_name": build.version_name,
                "version_code": build.version_code,
                "ip": get_client_ip(request),
            },
        )
    except Exception as e:
        logger.warning(
            f"Ошибка при логировании скачивания мобильного приложения: {e}",
            exc_info=True,
            extra={"user_id": request.user.id if request.user.is_authenticated else None},
        )
        # Не критично, если логирование не удалось, но фиксируем для отладки

    # Отдаем файл с правильным Content-Disposition
    response = FileResponse(
        build.file.open("rb"), content_type="application/vnd.android.package-archive"
    )
    response["Content-Disposition"] = (
        f'attachment; filename="crmprofi-{build.version_name}-{build.version_code}.apk"'
    )
    return response


@login_required
@policy_required(resource_type="action", resource="ui:mobile_app:qr")
def mobile_app_qr_image(request: HttpRequest) -> HttpResponse:
    """
    Генерация QR-кода для входа в мобильное приложение.
    Токен передается через query параметр ?token=...
    Android приложение сканирует просто токен (строку), а не URL.
    """
    import qrcode
    import io

    token = request.GET.get("token", "").strip()
    if not token:
        raise Http404("Токен не указан")

    # Проверяем, что токен существует и принадлежит текущему пользователю
    try:
        qr_token = MobileAppQrToken.objects.get(
            user=request.user, token_hash=MobileAppQrToken.hash_token(token)
        )
    except MobileAppQrToken.DoesNotExist:
        raise Http404("Токен не найден")

    # Android приложение ожидает просто токен (строку), а не URL
    # QR-код содержит только токен, который приложение отправит на /api/phone/qr/exchange/
    qr_data = token

    # Генерируем QR-код
    qr = qrcode.QRCode(version=1, box_size=10, border=4)
    qr.add_data(qr_data)
    qr.make(fit=True)

    img = qr.make_image(fill_color="black", back_color="white")

    # Сохраняем в BytesIO
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)

    response = HttpResponse(buffer.read(), content_type="image/png")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response
