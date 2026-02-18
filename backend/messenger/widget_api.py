"""
Публичный Widget API для встраивания виджета на внешние сайты.

Endpoints:
- POST /api/widget/bootstrap/ - создание/получение сессии виджета
- POST /api/widget/send/ - отправка сообщения от посетителя
- GET /api/widget/poll/ - получение новых сообщений от операторов

Все endpoints публичные (без аутентификации), защищены через widget_token и widget_session_token.
"""

import logging
import os
import json
import time

from django.conf import settings
from django.utils import timezone as django_timezone
from django.http import FileResponse, Http404, StreamingHttpResponse
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError as DRFValidationError, Throttled

from . import models, serializers, services
from .utils import (
    create_widget_session,
    get_widget_session,
    ensure_messenger_enabled_api,
    is_within_working_hours,
    get_attachment_settings,
    is_content_type_allowed,
    build_message_attachments_payload,
    enforce_widget_origin_allowed,
    get_client_ip,
    mark_ip_activity_for_captcha,
    should_require_captcha,
    create_math_captcha,
    verify_math_captcha,
    mark_captcha_passed,
    is_captcha_passed,
)
from .logging_utils import widget_logger, safe_log_widget_error
from .integrations import notify_conversation_created, notify_message
from .automation import run_automation_for_incoming_message
from .throttles import WidgetBootstrapThrottle, WidgetSendThrottle, WidgetPollThrottle


class WidgetApiMixin:
    """
    Миксин для проверки feature-флага в widget API endpoints.
    """

    def dispatch(self, *args, **kwargs):
        ensure_messenger_enabled_api()
        return super().dispatch(*args, **kwargs)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([WidgetBootstrapThrottle])
def widget_bootstrap(request):
    """
    POST /api/widget/bootstrap/

    Создаёт или находит диалог для посетителя и возвращает widget_session_token.
    """
    ensure_messenger_enabled_api()

    widget_token = None
    contact_external_id = None
    
    try:
        # Проверка throttling (выполняется автоматически через декоратор)
        # Если превышен лимит - DRF выбросит Throttled исключение
        
        input_serializer = serializers.WidgetBootstrapSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        widget_token = input_serializer.validated_data["widget_token"]
        contact_external_id = input_serializer.validated_data["contact_external_id"]

        # Найти активный Inbox по widget_token
        try:
            inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
        except models.Inbox.DoesNotExist:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Bootstrap failed: invalid widget_token or inactive inbox",
                widget_token=widget_token,
            )
            return Response(
                {"detail": "Invalid widget_token or inbox is inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        enforce_widget_origin_allowed(request, inbox)

        # Anti-spam: капча по IP при подозрительной активности
        ip = get_client_ip(request)
        mark_ip_activity_for_captcha(ip)
        captcha_required = should_require_captcha(ip)
        captcha_token = ""
        captcha_question = ""
        if captcha_required:
            captcha_token, captcha_question = create_math_captcha()

        # Создать или получить Contact (обновляем поля, если переданы новые значения)
        contact = services.create_or_get_contact(
            external_id=contact_external_id,
            name=input_serializer.validated_data.get("name") or None,  # Передаём None, если пустая строка
            email=input_serializer.validated_data.get("email") or None,
            phone=input_serializer.validated_data.get("phone") or None,
            update_if_exists=True,  # Обновляем существующий контакт новыми данными
        )

        # Найти активный диалог (OPEN/PENDING) или создать новый
        # Если все диалоги закрыты (RESOLVED/CLOSED), создаём новый диалог
        conversation = models.Conversation.objects.filter(
            inbox=inbox,
            contact=contact,
            status__in=[models.Conversation.Status.OPEN, models.Conversation.Status.PENDING],
        ).first()

        if not conversation:
            # Определяем region: из meta/параметра, иначе по GeoIP с клиентского IP
            region = None
            meta = input_serializer.validated_data.get("meta", {})
            region_id = meta.get("region_id") or input_serializer.validated_data.get("region_id")

            if region_id:
                try:
                    from companies.models import Region
                    region = Region.objects.get(id=region_id)
                except (Region.DoesNotExist, ValueError, TypeError):
                    pass

            if not region:
                # GeoIP: определяем регион по IP клиента
                client_ip = (
                    (request.META.get("HTTP_X_FORWARDED_FOR") or "").split(",")[0].strip()
                    or request.META.get("REMOTE_ADDR")
                )
                if client_ip:
                    from .geoip import get_region_from_ip
                    region = get_region_from_ip(client_ip)
                    if region and not contact.region_detected_id:
                        contact.region_detected = region
                        contact.save(update_fields=["region_detected"])

            if not region and contact.region_detected:
                region = contact.region_detected

            # Правило маршрутизации и филиал для диалога
            routing_rule = services.select_routing_rule(inbox, region)
            if inbox.branch_id is not None:
                branch = inbox.branch
            else:
                # Глобальный inbox: филиал из правила или дефолтный
                branch = (routing_rule.branch if routing_rule else None) or services.get_default_branch_for_messenger()
                if not branch:
                    safe_log_widget_error(
                        widget_logger,
                        logging.ERROR,
                        "Bootstrap: global inbox but no routing rule and no MESSENGER_DEFAULT_BRANCH_ID",
                        widget_token=widget_token,
                        inbox_id=inbox.id,
                    )
                    return Response(
                        {"detail": "Service temporarily unavailable. Please try again later."},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE,
                    )

            # Создаём новый диалог (branch задаётся явно для глобального inbox)
            conversation = models.Conversation(
                inbox=inbox,
                contact=contact,
                status=models.Conversation.Status.OPEN,
                branch=branch,
                region=region,
            )
            conversation.save()

            # Auto-assign только в рабочие часы; иначе диалог остаётся без назначения
            within_working_hours = is_within_working_hours(inbox)
            if within_working_hours:
                services.auto_assign_conversation(conversation)

        # Вне рабочих часов (для ответа виджету)
        outside_working_hours = False
        working_hours_message = ""
        if not is_within_working_hours(inbox):
            outside_working_hours = True
            working_hours_message = getattr(
                settings,
                "MESSENGER_OUTSIDE_WORKING_HOURS_MESSAGE",
                "Мы ответим в рабочее время.",
            )

        # Офлайн-режим: настраиваемое сообщение, когда нет операторов или вне рабочих часов
        offline_settings = (inbox.settings or {}).get("offline") or {}
        offline_enabled = offline_settings.get("enabled", False)
        offline_mode = False
        offline_message = ""
        if offline_enabled:
            no_operators = not conversation.assignee_id and not services.has_online_operators_for_branch(
                conversation.branch_id, inbox.id
            )
            if outside_working_hours or no_operators:
                offline_mode = True
                offline_message = (
                    offline_settings.get("message", "").strip()
                    or "Сейчас никого нет. Оставьте заявку — мы ответим в рабочее время."
                )

        # Создать widget_session_token
        session = create_widget_session(
            inbox_id=inbox.id,
            conversation_id=conversation.id,
            contact_id=str(contact.id),
        )

        # Опционально: вернуть последние сообщения (только OUT, без INTERNAL)
        att_settings = get_attachment_settings(inbox)
        initial_messages = []
        messages = conversation.messages.filter(direction=models.Message.Direction.OUT).order_by("-created_at")[:10]
        for msg in reversed(messages):  # В хронологическом порядке
            payload = {
                "id": msg.id,
                "body": msg.body,
                "direction": msg.direction,
                "created_at": msg.created_at.isoformat(),
                "read_at": msg.read_at.isoformat() if msg.read_at else None,
            }
            payload["attachments"] = build_message_attachments_payload(
                msg, request, widget_token, session.token
            )
            initial_messages.append(payload)

        settings_cfg = inbox.settings or {}
        response_data = {
            "widget_session_token": session.token,
            "conversation_id": conversation.id,
            "initial_messages": initial_messages,
            "outside_working_hours": outside_working_hours,
            "working_hours_message": working_hours_message,
            "offline_mode": offline_mode,
            "offline_message": offline_message,
            "title": settings_cfg.get("title") or "",
            "greeting": settings_cfg.get("greeting") or "",
            "color": settings_cfg.get("color") or "",
            # privacy: можно задать в настройках inbox, иначе берём глобальные значения
            "privacy_url": (settings_cfg.get("privacy") or {}).get("url")
            or getattr(settings, "MESSENGER_PRIVACY_URL", ""),
            "privacy_text": (settings_cfg.get("privacy") or {}).get("text")
            or getattr(
                settings,
                "MESSENGER_PRIVACY_TEXT",
                "Отправляя сообщение, вы соглашаетесь с обработкой персональных данных.",
            ),
        }
        features_cfg = (inbox.settings or {}).get("features") or {}
        response_data["sse_enabled"] = bool(features_cfg.get("sse", True))
        response_data["attachments_enabled"] = att_settings["enabled"]
        response_data["max_file_size_bytes"] = att_settings["max_file_size_bytes"]
        response_data["allowed_content_types"] = att_settings["allowed_content_types"]
        response_data["captcha_required"] = captcha_required
        response_data["captcha_token"] = captcha_token
        response_data["captcha_question"] = captcha_question

        # Webhook: новый диалог
        try:
            notify_conversation_created(conversation)
        except Exception:
            widget_logger.warning(
                "Webhook notify_conversation_created failed",
                exc_info=True,
                extra={"widget_token": widget_token, "inbox_id": inbox.id, "conversation_id": conversation.id},
            )

        response_serializer = serializers.WidgetBootstrapResponseSerializer(response_data)

        return Response(response_serializer.data, status=status.HTTP_200_OK)
    
    except Throttled as e:
        # Превышен лимит запросов
        safe_log_widget_error(
            widget_logger,
            logging.WARNING,
            "Bootstrap throttled",
            widget_token=widget_token,
        )
        # DRF автоматически вернёт 429 с деталями
        raise
    
    except DRFValidationError as e:
        # Ошибки валидации сериализатора
        safe_log_widget_error(
            widget_logger,
            logging.WARNING,
            "Bootstrap validation error",
            widget_token=widget_token,
            error=e,
        )
        # DRF автоматически обработает через raise_exception=True
        raise
    
    except Exception as e:
        # Неожиданные ошибки
        safe_log_widget_error(
            widget_logger,
            logging.ERROR,
            "Bootstrap unexpected error",
            widget_token=widget_token,
            error=e,
        )
        return Response(
            {"detail": "Internal server error. Please try again later."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


def _parse_widget_send_input(request):
    """
    Извлекает widget_token, widget_session_token, body и список файлов из запроса.
    Поддерживает application/json и multipart/form-data.
    """
    content_type = (request.content_type or "").lower()
    if "multipart/form-data" in content_type:
        data = request.POST
        body = (data.get("body") or "").strip()
        files = list(request.FILES.getlist("files")) + list(request.FILES.getlist("file"))
        return {
            "widget_token": data.get("widget_token") or "",
            "widget_session_token": data.get("widget_session_token") or "",
            "body": body,
            "files": files,
        }
    # JSON
    data = request.data
    body = (data.get("body") or "").strip()
    return {
        "widget_token": data.get("widget_token") or "",
        "widget_session_token": data.get("widget_session_token") or "",
        "body": body,
        "files": [],
    }


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([WidgetSendThrottle])
def widget_send(request):
    """
    POST /api/widget/send/

    Отправка сообщения от посетителя (входящее сообщение).
    Поддерживает JSON (только текст) и multipart/form-data (body + файлы).
    Текст может быть пустым, если есть хотя бы одно вложение.
    """
    ensure_messenger_enabled_api()

    widget_token = None
    widget_session_token = None

    try:
        parsed = _parse_widget_send_input(request)
        widget_token = parsed["widget_token"]
        widget_session_token = parsed["widget_session_token"]
        body = parsed["body"]
        uploaded_files = parsed["files"]
        captcha_token = (request.POST.get("captcha_token") or request.data.get("captcha_token") or "").strip()
        captcha_answer = (request.POST.get("captcha_answer") or request.data.get("captcha_answer") or "").strip()

        # Валидация токенов и body (body обязателен только если нет файлов)
        if not widget_token or not widget_session_token:
            return Response(
                {"detail": "widget_token and widget_session_token are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not body and not uploaded_files:
            return Response(
                {"detail": "Message body cannot be empty when no files are attached."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if body and len(body) > 2000:
            return Response(
                {"detail": "Message body is too long (max 2000 characters)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Honeypot для JSON (для multipart можно передать hp в POST)
        if not uploaded_files:
            input_serializer = serializers.WidgetSendSerializer(data=request.data)
            if not input_serializer.is_valid():
                return Response(input_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
            hp = (input_serializer.validated_data.get("hp") or "").strip()
            if hp:
                return Response({"detail": "Invalid request."}, status=status.HTTP_400_BAD_REQUEST)
            # Проверка ссылок в body
            if body:
                import re
                url_pattern = r"(https?://|www\.)[^\s]+"
                urls = re.findall(url_pattern, body, re.IGNORECASE)
                if len(urls) > 3:
                    return Response(
                        {"body": "Message contains too many links."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        # Дополнительная проверка на одинаковые сообщения (через cache)
        from django.core.cache import cache
        message_hash_key = f"messenger:spam:send:{widget_session_token[:16]}:{hash(body or str(len(uploaded_files)))}"
        duplicate_count = cache.get(message_hash_key, 0)
        if duplicate_count >= 3:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Send blocked: duplicate message spam",
                widget_token=widget_token,
                session_token=widget_session_token,
            )
            return Response(
                {"detail": "Duplicate messages are not allowed."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cache.set(message_hash_key, duplicate_count + 1, timeout=300)

        # Валидировать widget_token → inbox
        try:
            inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
        except models.Inbox.DoesNotExist:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Send failed: invalid widget_token or inactive inbox",
                widget_token=widget_token,
                session_token=widget_session_token,
            )
            return Response(
                {"detail": "Invalid widget_token or inbox is inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        enforce_widget_origin_allowed(request, inbox)

        # Anti-spam: если IP под капчей — требуем прохождение капчи (один раз на session)
        ip = get_client_ip(request)
        mark_ip_activity_for_captcha(ip)
        if should_require_captcha(ip) and not is_captcha_passed(widget_session_token):
            if not verify_math_captcha(captcha_token, captcha_answer):
                return Response(
                    {"detail": "Captcha required.", "captcha_required": True},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            mark_captcha_passed(widget_session_token)

        # Вложения: лимиты и типы
        att_settings = get_attachment_settings(inbox)
        max_files_per_message = 5
        if uploaded_files:
            if not att_settings["enabled"]:
                return Response(
                    {"detail": "Attachments are not allowed for this widget."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if len(uploaded_files) > max_files_per_message:
                return Response(
                    {"detail": f"Maximum {max_files_per_message} files per message."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            max_bytes = att_settings["max_file_size_bytes"]
            allowed_types = att_settings["allowed_content_types"]
            for f in uploaded_files:
                size = getattr(f, "size", 0) or 0
                if size > max_bytes:
                    return Response(
                        {"detail": f"File too large. Maximum size is {max_bytes // (1024*1024)} MB."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                ct = (getattr(f, "content_type", "") or "").strip()
                if not is_content_type_allowed(ct, allowed_types):
                    return Response(
                        {"detail": f"File type not allowed: {ct or 'unknown'}."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        # Получить сессию виджета
        session = get_widget_session(widget_session_token)
        if not session:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Send failed: invalid or expired session token",
                widget_token=widget_token,
                session_token=widget_session_token,
            )
            return Response(
                {"detail": "Invalid or expired widget_session_token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        if session.inbox_id != inbox.id:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Send failed: session token mismatch",
                widget_token=widget_token,
                session_token=widget_session_token,
                inbox_id=inbox.id,
                session_inbox_id=session.inbox_id,
            )
            return Response(
                {"detail": "Widget session token does not match widget_token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        try:
            conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
            contact = models.Contact.objects.get(id=session.contact_id)
        except (models.Conversation.DoesNotExist, models.Contact.DoesNotExist) as e:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Send failed: conversation or contact not found",
                widget_token=widget_token,
                session_token=widget_session_token,
                conversation_id=session.conversation_id,
                contact_id=session.contact_id,
                error=e,
            )
            return Response(
                {"detail": "Conversation or contact not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Создать входящее сообщение (body может быть пустым при наличии вложений)
        message = models.Message(
            conversation=conversation,
            direction=models.Message.Direction.IN,
            body=body or "",
            sender_contact=contact,
            sender_user=None,
        )
        message.full_clean()
        message.save()

        for f in uploaded_files:
            models.MessageAttachment.objects.create(
                message=message,
                file=f,
                original_name=getattr(f, "name", "") or "",
                content_type=(getattr(f, "content_type", "") or "")[:120],
                size=getattr(f, "size", 0) or 0,
            )

        models.Conversation.objects.filter(pk=conversation.id).update(last_message_at=django_timezone.now())

        # Webhook: новое входящее сообщение
        try:
            notify_message(message)
        except Exception:
            widget_logger.warning(
                "Webhook notify_message failed",
                exc_info=True,
                extra={
                    "widget_token": widget_token,
                    "session_token": widget_session_token,
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                },
            )

        # Автоматизация: автоответ на первый входящий месседж (если включено в Inbox)
        try:
            run_automation_for_incoming_message(message)
        except Exception:
            widget_logger.warning(
                "Automation run_automation_for_incoming_message failed",
                exc_info=True,
                extra={
                    "widget_token": widget_token,
                    "session_token": widget_session_token,
                    "conversation_id": conversation.id,
                    "message_id": message.id,
                },
            )

        response_serializer = serializers.WidgetSendResponseSerializer({
            "id": message.id,
            "created_at": message.created_at,
        })
        return Response(response_serializer.data, status=status.HTTP_201_CREATED)
    
    except Throttled as e:
        # Превышен лимит запросов
        safe_log_widget_error(
            widget_logger,
            logging.WARNING,
            "Send throttled",
            widget_token=widget_token,
            session_token=widget_session_token,
        )
        raise
    
    except DRFValidationError as e:
        # Ошибки валидации сериализатора
        safe_log_widget_error(
            widget_logger,
            logging.WARNING,
            "Send validation error",
            widget_token=widget_token,
            session_token=widget_session_token,
            error=e,
        )
        raise
    
    except Exception as e:
        # Неожиданные ошибки
        safe_log_widget_error(
            widget_logger,
            logging.ERROR,
            "Send unexpected error",
            widget_token=widget_token,
            session_token=widget_session_token,
            error=e,
        )
        return Response(
            {"detail": "Internal server error. Please try again later."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([WidgetPollThrottle])
def widget_poll(request):
    """
    GET /api/widget/poll/

    Получение новых сообщений от операторов (OUT и INTERNAL).
    """
    ensure_messenger_enabled_api()

    widget_token = request.query_params.get("widget_token")
    widget_session_token = request.query_params.get("widget_session_token")
    since_id = request.query_params.get("since_id")

    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        # Валидировать widget_token → inbox
        try:
            inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
        except models.Inbox.DoesNotExist:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Poll failed: invalid widget_token or inactive inbox",
                widget_token=widget_token,
                session_token=widget_session_token,
            )
            return Response(
                {"detail": "Invalid widget_token or inbox is inactive."},
                status=status.HTTP_404_NOT_FOUND,
            )

        enforce_widget_origin_allowed(request, inbox)

        # Получить сессию виджета
        session = get_widget_session(widget_session_token)
        if not session:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Poll failed: invalid or expired session token",
                widget_token=widget_token,
                session_token=widget_session_token,
            )
            return Response(
                {"detail": "Invalid or expired widget_session_token."},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        # Проверить совпадение inbox_id
        if session.inbox_id != inbox.id:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Poll failed: session token mismatch",
                widget_token=widget_token,
                session_token=widget_session_token,
                inbox_id=inbox.id,
                session_inbox_id=session.inbox_id,
            )
            return Response(
                {"detail": "Widget session token does not match widget_token."},
                status=status.HTTP_403_FORBIDDEN,
            )

        # Получить conversation
        try:
            conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
        except models.Conversation.DoesNotExist as e:
            safe_log_widget_error(
                widget_logger,
                logging.WARNING,
                "Poll failed: conversation not found",
                widget_token=widget_token,
                session_token=widget_session_token,
                conversation_id=session.conversation_id,
                error=e,
            )
            return Response(
                {"detail": "Conversation not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Получить новые сообщения (только OUT, без INTERNAL)
        messages_qs = conversation.messages.filter(
            direction=models.Message.Direction.OUT
        ).order_by("created_at", "id")

        if since_id:
            try:
                since_id_int = int(since_id)
                messages_qs = messages_qs.filter(id__gt=since_id_int)
            except ValueError:
                return Response(
                    {"detail": "Invalid since_id format."},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        messages = messages_qs[:50]  # Лимит 50 сообщений за запрос

        result = []
        for msg in messages:
            payload = {
                "id": msg.id,
                "body": msg.body,
                "direction": msg.direction,
                "created_at": msg.created_at.isoformat(),
                "read_at": msg.read_at.isoformat() if msg.read_at else None,
            }
            payload["attachments"] = build_message_attachments_payload(
                msg, request, widget_token, widget_session_token
            )
            result.append(payload)

        from .typing import get_typing_status
        typing_status = get_typing_status(conversation.id)

        # Оценка: запросить у контакта, если диалог закрыт/решён и ещё не оценен
        rating_requested = False
        rating_type = "stars"
        rating_max_score = 5
        rating_cfg = (inbox.settings or {}).get("rating") or {}
        if rating_cfg.get("enabled") and conversation.status in (
            models.Conversation.Status.RESOLVED,
            models.Conversation.Status.CLOSED,
        ) and conversation.rating_score is None:
            rating_requested = True
            rating_type = rating_cfg.get("type", "stars")
            rating_max_score = int(rating_cfg.get("max_score", 5)) if rating_type == "stars" else 10

        return Response({
            "messages": result,
            "operator_typing": typing_status["operator_typing"],
            "rating_requested": rating_requested,
            "rating_type": rating_type,
            "rating_max_score": rating_max_score,
        }, status=status.HTTP_200_OK)
    
    except Throttled as e:
        # Превышен лимит запросов
        safe_log_widget_error(
            widget_logger,
            logging.WARNING,
            "Poll throttled",
            widget_token=widget_token,
            session_token=widget_session_token,
        )
        raise
    
    except Exception as e:
        # Неожиданные ошибки
        safe_log_widget_error(
            widget_logger,
            logging.ERROR,
            "Poll unexpected error",
            widget_token=widget_token,
            session_token=widget_session_token,
            error=e,
        )
        return Response(
            {"detail": "Internal server error. Please try again later."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET"])
@permission_classes([AllowAny])
def widget_stream(request):
    """
    GET /api/widget/stream/?widget_token=...&widget_session_token=...&since_id=...

    SSE-стрим обновлений для виджета (замена частого poll).
    Работает короткими соединениями (≈25 секунд), затем клиент переподключается.
    """
    ensure_messenger_enabled_api()

    widget_token = request.query_params.get("widget_token")
    widget_session_token = request.query_params.get("widget_session_token")
    since_id = request.query_params.get("since_id")

    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Валидации как в poll
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        raise Http404("Invalid widget_token or inbox is inactive.")

    enforce_widget_origin_allowed(request, inbox)

    features_cfg = (inbox.settings or {}).get("features") or {}
    if not bool(features_cfg.get("sse", True)):
        raise Http404("SSE disabled for this inbox.")

    session = get_widget_session(widget_session_token)
    if not session or session.inbox_id != inbox.id:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )

    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
    except models.Conversation.DoesNotExist:
        raise Http404("Conversation not found.")

    try:
        last_id = int(since_id) if since_id is not None else 0
    except ValueError:
        return Response({"detail": "Invalid since_id format."}, status=status.HTTP_400_BAD_REQUEST)

    from .typing import get_typing_status

    def _rating_payload() -> tuple[bool, str, int]:
        rating_requested = False
        rating_type = "stars"
        rating_max_score = 5
        rating_cfg = (inbox.settings or {}).get("rating") or {}
        if rating_cfg.get("enabled") and conversation.status in (
            models.Conversation.Status.RESOLVED,
            models.Conversation.Status.CLOSED,
        ) and conversation.rating_score is None:
            rating_requested = True
            rating_type = rating_cfg.get("type", "stars")
            rating_max_score = int(rating_cfg.get("max_score", 5)) if rating_type == "stars" else 10
        return rating_requested, rating_type, rating_max_score

    def event_stream():
        nonlocal last_id
        started = time.time()
        last_keepalive = 0.0
        last_typing = None
        last_rating = None

        # Первое событие (handshake) — чтобы браузер сразу «увидел» поток
        yield "event: ready\ndata: {}\n\n"

        while True:
            now = time.time()
            if now - started > 25:
                break

            # Новые OUT сообщения
            msgs_qs = conversation.messages.filter(
                direction=models.Message.Direction.OUT,
                id__gt=last_id,
            ).order_by("created_at", "id")[:50]

            messages_payload = []
            max_id = last_id
            for msg in msgs_qs:
                if msg.id and msg.id > max_id:
                    max_id = msg.id
                payload = {
                    "id": msg.id,
                    "body": msg.body,
                    "direction": msg.direction,
                    "created_at": msg.created_at.isoformat(),
                    "read_at": msg.read_at.isoformat() if msg.read_at else None,
                    "attachments": build_message_attachments_payload(
                        msg, request, widget_token, widget_session_token
                    ),
                }
                messages_payload.append(payload)

            typing_status = get_typing_status(conversation.id)
            operator_typing = typing_status.get("operator_typing") is True

            rating_requested, rating_type, rating_max_score = _rating_payload()
            rating_tuple = (rating_requested, rating_type, rating_max_score)

            changed = False
            if messages_payload:
                changed = True
            if last_typing is None or last_typing != operator_typing:
                changed = True
            if last_rating is None or last_rating != rating_tuple:
                changed = True

            if changed:
                last_typing = operator_typing
                last_rating = rating_tuple
                if messages_payload:
                    last_id = max_id

                data = {
                    "messages": messages_payload,
                    "operator_typing": operator_typing,
                    "rating_requested": rating_requested,
                    "rating_type": rating_type,
                    "rating_max_score": rating_max_score,
                    "since_id": last_id,
                }
                yield "event: update\ndata: " + json.dumps(data, ensure_ascii=False) + "\n\n"
            else:
                # keep-alive раз в ~5 сек, чтобы прокси не резали соединение
                if now - last_keepalive > 5:
                    last_keepalive = now
                    yield ": keep-alive\n\n"

            time.sleep(1)

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"  # nginx: не буферизовать SSE
    return resp


@api_view(["GET"])
@permission_classes([AllowAny])
def widget_attachment_download(request, attachment_id):
    """
    GET /api/widget/attachment/<id>/?widget_token=...&widget_session_token=...

    Скачивание/просмотр вложения виджета. Доступ только если вложение принадлежит
    сообщению из диалога текущей сессии виджета.
    """
    ensure_messenger_enabled_api()
    widget_token = request.query_params.get("widget_token")
    widget_session_token = request.query_params.get("widget_session_token")
    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        raise Http404("Invalid widget_token or inbox is inactive.")

    enforce_widget_origin_allowed(request, inbox)
    session = get_widget_session(widget_session_token)
    if not session or session.inbox_id != inbox.id:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        attachment = models.MessageAttachment.objects.select_related("message").get(pk=attachment_id)
    except models.MessageAttachment.DoesNotExist:
        raise Http404("Attachment not found.")
    if attachment.message.conversation_id != session.conversation_id:
        raise Http404("Attachment not found.")
    if not attachment.file:
        raise Http404("File not available.")
    filename = attachment.original_name or os.path.basename(attachment.file.name)
    try:
        f = attachment.file.open("rb")
    except Exception:
        raise Http404("File not available.")
    response = FileResponse(f, filename=filename, content_type=attachment.content_type or "application/octet-stream")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([WidgetPollThrottle])
def widget_typing(request):
    """
    POST /api/widget/typing/

    Сигнал «контакт печатает» (виджет). Тело: widget_token, widget_session_token.
    Состояние хранится в Redis с TTL 8 с; в poll возвращается operator_typing, здесь — контакт.
    """
    ensure_messenger_enabled_api()
    widget_token = request.data.get("widget_token") or request.query_params.get("widget_token")
    widget_session_token = request.data.get("widget_session_token") or request.query_params.get("widget_session_token")
    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response({"detail": "Invalid widget_token or inbox is inactive."}, status=status.HTTP_404_NOT_FOUND)

    enforce_widget_origin_allowed(request, inbox)
    session = get_widget_session(widget_session_token)
    if not session or session.inbox_id != inbox.id:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
    except models.Conversation.DoesNotExist:
        return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)
    from .typing import set_contact_typing
    set_contact_typing(conversation.id)
    return Response({"status": "ok"}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([WidgetPollThrottle])
def widget_mark_read(request):
    """
    POST /api/widget/mark_read/

    Отметить исходящие сообщения как прочитанные (для статуса «прочитано» в виджете).
    Тело: widget_token, widget_session_token, last_message_id (int) — отметить все OUT сообщения
    с id <= last_message_id в диалоге; либо message_ids (list[int]) — отметить указанные сообщения.
    """
    ensure_messenger_enabled_api()
    widget_token = request.data.get("widget_token") or request.query_params.get("widget_token")
    widget_session_token = request.data.get("widget_session_token") or request.query_params.get("widget_session_token")
    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response({"detail": "Invalid widget_token or inbox is inactive."}, status=status.HTTP_404_NOT_FOUND)

    enforce_widget_origin_allowed(request, inbox)
    session = get_widget_session(widget_session_token)
    if not session or session.inbox_id != inbox.id:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
    except models.Conversation.DoesNotExist:
        return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

    from django.utils import timezone
    now = timezone.now()
    last_message_id = request.data.get("last_message_id")
    message_ids = request.data.get("message_ids")

    if last_message_id is not None:
        try:
            last_id = int(last_message_id)
            conversation.messages.filter(
                direction=models.Message.Direction.OUT,
                id__lte=last_id,
            ).update(read_at=now)
        except (ValueError, TypeError):
            return Response({"detail": "Invalid last_message_id."}, status=status.HTTP_400_BAD_REQUEST)
    elif message_ids is not None:
        if not isinstance(message_ids, list):
            return Response({"detail": "message_ids must be a list."}, status=status.HTTP_400_BAD_REQUEST)
        ids_ok = [int(x) for x in message_ids if isinstance(x, (int, str)) and str(x).isdigit()]
        if ids_ok:
            conversation.messages.filter(
                direction=models.Message.Direction.OUT,
                id__in=ids_ok,
            ).update(read_at=now)
    else:
        return Response(
            {"detail": "Provide last_message_id or message_ids."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return Response({"status": "ok"}, status=status.HTTP_200_OK)


@api_view(["POST"])
@permission_classes([AllowAny])
@throttle_classes([WidgetPollThrottle])
def widget_rate(request):
    """
    POST /api/widget/rate/

    Отправить оценку диалога (после закрытия). Тело: widget_token, widget_session_token,
    score (int 1–5 для звёзд или 0–10 для NPS), comment (опционально).
    """
    ensure_messenger_enabled_api()
    widget_token = request.data.get("widget_token") or request.query_params.get("widget_token")
    widget_session_token = request.data.get("widget_session_token") or request.query_params.get("widget_session_token")
    if not widget_token or not widget_session_token:
        return Response(
            {"detail": "widget_token and widget_session_token are required."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        inbox = models.Inbox.objects.get(widget_token=widget_token, is_active=True)
    except models.Inbox.DoesNotExist:
        return Response({"detail": "Invalid widget_token or inbox is inactive."}, status=status.HTTP_404_NOT_FOUND)

    enforce_widget_origin_allowed(request, inbox)
    session = get_widget_session(widget_session_token)
    if not session or session.inbox_id != inbox.id:
        return Response(
            {"detail": "Invalid or expired widget_session_token."},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    try:
        conversation = models.Conversation.objects.get(id=session.conversation_id, inbox=inbox)
    except models.Conversation.DoesNotExist:
        return Response({"detail": "Conversation not found."}, status=status.HTTP_404_NOT_FOUND)

    if conversation.status not in (
        models.Conversation.Status.RESOLVED,
        models.Conversation.Status.CLOSED,
    ):
        return Response(
            {"detail": "Rating is only available for closed or resolved conversations."},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if conversation.rating_score is not None:
        return Response({"detail": "Conversation already rated."}, status=status.HTTP_400_BAD_REQUEST)

    rating_cfg = (inbox.settings or {}).get("rating") or {}
    rating_type = rating_cfg.get("type", "stars")
    max_score = 10 if rating_type == "nps" else int(rating_cfg.get("max_score", 5))
    min_score = 0 if rating_type == "nps" else 1

    try:
        score = int(request.data.get("score", request.data.get("rating_score")))
    except (TypeError, ValueError):
        return Response({"detail": "Invalid score."}, status=status.HTTP_400_BAD_REQUEST)
    if not (min_score <= score <= max_score):
        return Response(
            {"detail": f"Score must be between {min_score} and {max_score}."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    comment = (request.data.get("comment") or "").strip()[:2000]
    from django.utils import timezone
    now = timezone.now()
    conversation.rating_score = score
    conversation.rating_comment = comment
    conversation.rated_at = now
    conversation.save(update_fields=["rating_score", "rating_comment", "rated_at"])
    return Response({"status": "ok"}, status=status.HTTP_200_OK)
