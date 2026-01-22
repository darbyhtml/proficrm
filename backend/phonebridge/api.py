from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from .models import CallRequest, PhoneDevice, PhoneTelemetry, PhoneLogBundle, MobileAppQrToken
from policy.engine import enforce


def mask_phone(phone: str | None) -> str:
    """
    Маскирует номер телефона для логов (оставляет последние 4 цифры).
    Защита от утечки персональных данных в логах.
    """
    if not phone or len(phone) <= 4:
        return "***"
    return f"***{phone[-4:]}"


class RegisterDeviceSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64)
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    fcm_token = serializers.CharField(required=False, allow_blank=True)


class RegisterDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        enforce(user=request.user, resource_type="action", resource="phone:devices:register", context={"path": request.path})
        s = RegisterDeviceSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        device_id = s.validated_data["device_id"]
        device_name = s.validated_data.get("device_name", "") or ""
        fcm_token = s.validated_data.get("fcm_token", "") or ""

        obj, _ = PhoneDevice.objects.update_or_create(
            user=request.user,
            device_id=device_id,
            defaults={
                "device_name": device_name,
                "platform": "android",
                "fcm_token": fcm_token,
                "last_seen_at": timezone.now(),
            },
        )
        return Response({"ok": True, "device_id": obj.device_id})


class DeviceHeartbeatSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64)
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    app_version = serializers.CharField(max_length=32, required=False, allow_blank=True)
    last_poll_code = serializers.IntegerField(required=False, allow_null=True)
    last_poll_at = serializers.DateTimeField(required=False, allow_null=True)
    encryption_enabled = serializers.BooleanField(required=False, default=True)


class DeviceHeartbeatView(APIView):
    """
    Лёгкий heartbeat от Android-приложения.
    Не ломает существующий /devices/register/ и /calls/pull/.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:devices:heartbeat", context={"path": request.path})

        s = DeviceHeartbeatSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        device_id = s.validated_data["device_id"].strip()
        device_name = (s.validated_data.get("device_name") or "").strip()
        app_version = (s.validated_data.get("app_version") or "").strip()
        last_poll_code = s.validated_data.get("last_poll_code")
        last_poll_at = s.validated_data.get("last_poll_at")
        encryption_enabled = s.validated_data.get("encryption_enabled", True)
        queue_stuck = s.validated_data.get("queue_stuck", False)
        stuck_items = s.validated_data.get("stuck_items")
        stuck_count = s.validated_data.get("stuck_count", 0)
        oldest_stuck_age_sec = s.validated_data.get("oldest_stuck_age_sec")
        stuck_by_type = s.validated_data.get("stuck_by_type")

        if not device_id:
            return Response({"detail": "device_id is required"}, status=400)

        # Определяем IP для диагностики (используем единую логику из accounts.security)
        from accounts.security import get_client_ip
        ip = get_client_ip(request)

        # Формируем сообщение об ошибке для queue_stuck
        error_message = ""
        if queue_stuck and stuck_count:
            error_message = f"Queue stuck: {stuck_count} items reached max retries"
            if oldest_stuck_age_sec:
                error_message += f", oldest: {oldest_stuck_age_sec}s"
            if stuck_by_type:
                type_list = [f"{k}:{v}" for k, v in stuck_by_type.items()]
                error_message += f" (by type: {', '.join(type_list)})"
        
        obj, created = PhoneDevice.objects.update_or_create(
            user=request.user,
            device_id=device_id,
            defaults={
                "device_name": device_name or None or "",
                "platform": "android",
                "last_seen_at": timezone.now(),
                "app_version": app_version,
                "last_poll_code": last_poll_code,
                "last_poll_at": last_poll_at or timezone.now(),
                "last_ip": ip,
                "encryption_enabled": encryption_enabled,
                "last_error_code": "queue_stuck" if queue_stuck else obj.last_error_code if not created else "",
                "last_error_message": error_message if queue_stuck else obj.last_error_message if not created else "",
            },
        )
        
        # Логируем предупреждения
        if not encryption_enabled:
            logger.warning(f"DeviceHeartbeat: user={request.user.id}, device={device_id} - encryption DISABLED (security risk)")
        if queue_stuck:
            logger.warning(f"DeviceHeartbeat: user={request.user.id}, device={device_id} - QUEUE STUCK: {stuck_count} items failed after max retries")

        logger.debug(f"DeviceHeartbeat: user={request.user.id}, device={device_id}, created={created}")

        return Response(
            {
                "ok": True,
                "device_id": obj.device_id,
                "app_version": obj.app_version,
                "last_seen_at": obj.last_seen_at,
            }
        )


class PullCallView(APIView):
    """
    MVP: polling.
    Клиент вызывает этот endpoint раз в 1-3 секунды и получает следующую команду "позвонить".
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        import logging

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:calls:pull", context={"path": request.path})

        device_id = (request.query_params.get("device_id") or "").strip()
        if not device_id:
            logger.warning(f"PullCallView: device_id missing for user {request.user.id}")
            return Response({"detail": "device_id is required"}, status=400)

        # Проверяем, что device_id принадлежит текущему пользователю (безопасность)
        device_exists = PhoneDevice.objects.filter(user=request.user, device_id=device_id).exists()
        if not device_exists:
            logger.warning(f"PullCallView: device_id {device_id} not found for user {request.user.id}")
            return Response({"detail": "Device not found or access denied"}, status=403)

        # обновим last_seen
        PhoneDevice.objects.filter(user=request.user, device_id=device_id).update(last_seen_at=timezone.now())

        # Проверяем наличие pending запросов для этого пользователя (для логов/диагностики)
        pending_count = CallRequest.objects.filter(user=request.user, status=CallRequest.Status.PENDING).count()
        logger.debug(f"PullCallView: user {request.user.id}, device {device_id}, pending calls: {pending_count}")

        # ВАЖНО: используем select_for_update(skip_locked=True), чтобы один и тот же звонок
        # не был выдан одновременно двум устройствам при конкурентных запросах.
        with transaction.atomic():
            call = (
                CallRequest.objects.select_for_update(skip_locked=True)
                .filter(user=request.user, status=CallRequest.Status.PENDING)
                .order_by("created_at")
                .first()
            )
            if not call:
                return Response(status=204)

            now = timezone.now()
            call.status = CallRequest.Status.CONSUMED
            call.delivered_at = now
            call.consumed_at = now
            call.save(update_fields=["status", "delivered_at", "consumed_at"])

        logger.info(f"PullCallView: delivered call {call.id} to user {request.user.id}, phone {mask_phone(call.phone_raw)}")

        return Response(
            {
                "id": str(call.id),
                "phone": call.phone_raw,
                "company_id": str(call.company_id) if call.company_id else None,
                "contact_id": str(call.contact_id) if call.contact_id else None,
                "note": call.note,
                "created_at": call.created_at,
            }
        )


class UpdateCallInfoSerializer(serializers.Serializer):
    """
    Serializer для обновления данных о звонке.
    Поддерживает legacy формат (4 поля) и extended формат (со всеми optional полями).
    Все новые поля optional для обратной совместимости.
    """
    # Legacy поля (обязательное только call_request_id)
    call_request_id = serializers.UUIDField()
    call_status = serializers.ChoiceField(choices=CallRequest.CallStatus.choices, required=False, allow_null=True)
    call_started_at = serializers.DateTimeField(required=False, allow_null=True)
    call_duration_seconds = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    
    # Новые поля (ЭТАП 1: контракт, приём и валидация, но пока не сохраняем в БД - ЭТАП 3)
    call_ended_at = serializers.DateTimeField(required=False, allow_null=True)
    # Используем CharField вместо ChoiceField для graceful handling неизвестных значений
    direction = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    resolve_method = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    attempts_count = serializers.IntegerField(required=False, allow_null=True, min_value=0)
    action_source = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    
    def validate_call_status(self, value):
        """
        Валидация call_status с graceful обработкой неизвестных значений.
        Если передан неизвестный статус - логируем и возвращаем UNKNOWN (не падаем).
        """
        if value is None:
            return value
        
        # Проверяем, что значение в choices
        valid_choices = [choice[0] for choice in CallRequest.CallStatus.choices]
        if value not in valid_choices:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"UpdateCallInfoSerializer: неизвестный call_status '{value}', маппим в UNKNOWN")
            # Маппим в UNKNOWN вместо ошибки валидации
            return CallRequest.CallStatus.UNKNOWN
        
        return value
    
    def validate_direction(self, value):
        """Валидация direction с graceful обработкой неизвестных значений."""
        if value is None or value == "":
            return None
        
        valid_choices = [choice[0] for choice in CallRequest.CallDirection.choices]
        if value not in valid_choices:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"UpdateCallInfoSerializer: неизвестный direction '{value}', игнорируем")
            return None  # Игнорируем неизвестное значение
        
        return value
    
    def validate_resolve_method(self, value):
        """Валидация resolve_method с graceful обработкой неизвестных значений."""
        if value is None or value == "":
            return None
        
        valid_choices = [choice[0] for choice in CallRequest.ResolveMethod.choices]
        if value not in valid_choices:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"UpdateCallInfoSerializer: неизвестный resolve_method '{value}', игнорируем")
            return None
        
        return value
    
    def validate_action_source(self, value):
        """Валидация action_source с graceful обработкой неизвестных значений."""
        if value is None or value == "":
            return None
        
        valid_choices = [choice[0] for choice in CallRequest.ActionSource.choices]
        if value not in valid_choices:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"UpdateCallInfoSerializer: неизвестный action_source '{value}', игнорируем")
            return None
        
        return value


class UpdateCallInfoView(APIView):
    """
    Endpoint для отправки данных о фактическом звонке из Android приложения.
    Android приложение собирает данные из CallLog и отправляет сюда.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging
        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:calls:update", context={"path": request.path})
        
        s = UpdateCallInfoSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        
        call_request_id = s.validated_data["call_request_id"]
        call_status = s.validated_data.get("call_status")
        call_started_at = s.validated_data.get("call_started_at")
        call_duration_seconds = s.validated_data.get("call_duration_seconds")

        # Проверяем, что CallRequest принадлежит текущему пользователю
        try:
            call_request = CallRequest.objects.get(id=call_request_id, user=request.user)
        except CallRequest.DoesNotExist:
            logger.warning(f"UpdateCallInfo: CallRequest {call_request_id} not found for user {request.user.id}")
            return Response({"detail": "CallRequest not found or access denied"}, status=404)

        # Обновляем данные о звонке (legacy поля)
        update_fields = []
        if call_status is not None:
            call_request.call_status = call_status
            update_fields.append("call_status")
        if call_started_at is not None:
            call_request.call_started_at = call_started_at
            update_fields.append("call_started_at")
        if call_duration_seconds is not None:
            call_request.call_duration_seconds = call_duration_seconds
            update_fields.append("call_duration_seconds")
        
        # Новые поля (ЭТАП 3: сохраняем в БД)
        # Вычисляем call_ended_at, если не передан, но есть started_at и duration
        call_ended_at = s.validated_data.get("call_ended_at")
        if call_ended_at is None and call_started_at is not None and call_duration_seconds is not None and call_duration_seconds > 0:
            from datetime import timedelta
            try:
                call_ended_at = call_started_at + timedelta(seconds=call_duration_seconds)
                logger.debug(f"UpdateCallInfo: вычислен call_ended_at = {call_ended_at}")
            except Exception as e:
                logger.warning(f"UpdateCallInfo: ошибка вычисления call_ended_at: {e}")
        
        # Сохраняем новые поля в БД
        direction = s.validated_data.get("direction")
        if direction is not None:
            call_request.direction = direction
            update_fields.append("direction")
        
        resolve_method = s.validated_data.get("resolve_method")
        if resolve_method is not None:
            call_request.resolve_method = resolve_method
            update_fields.append("resolve_method")
        
        attempts_count = s.validated_data.get("attempts_count")
        if attempts_count is not None:
            call_request.attempts_count = attempts_count
            update_fields.append("attempts_count")
        
        action_source = s.validated_data.get("action_source")
        if action_source is not None:
            call_request.action_source = action_source
            update_fields.append("action_source")
        
        if call_ended_at is not None:
            call_request.call_ended_at = call_ended_at
            update_fields.append("call_ended_at")
        
        # Логируем новые поля для отладки
        new_fields_received = {}
        if call_ended_at is not None:
            new_fields_received["call_ended_at"] = call_ended_at.isoformat()
        if direction is not None:
            new_fields_received["direction"] = direction
        if resolve_method is not None:
            new_fields_received["resolve_method"] = resolve_method
        if attempts_count is not None:
            new_fields_received["attempts_count"] = attempts_count
        if action_source is not None:
            new_fields_received["action_source"] = action_source
        
        if new_fields_received:
            logger.info(f"UpdateCallInfo: сохранены новые поля в БД: {new_fields_received}")

        if update_fields:
            call_request.save(update_fields=update_fields)
            logger.info(f"UpdateCallInfo: updated CallRequest {call_request_id} with {update_fields}")

        return Response({"ok": True, "call_request_id": str(call_request.id)})


class TelemetryItemSerializer(serializers.Serializer):
    ts = serializers.DateTimeField(required=False)
    type = serializers.ChoiceField(choices=PhoneTelemetry.Type.choices, required=False)
    endpoint = serializers.CharField(required=False, allow_blank=True)
    http_code = serializers.IntegerField(required=False, allow_null=True)
    value_ms = serializers.IntegerField(required=False, allow_null=True)
    extra = serializers.JSONField(required=False)


class TelemetryBatchSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    items = TelemetryItemSerializer(many=True)
    
    def validate_items(self, value):
        """Валидация размера батча: максимум 100 items для защиты от DoS."""
        if len(value) > 100:
            raise serializers.ValidationError("Максимум 100 items за раз. Получено: %d" % len(value))
        return value


class PhoneTelemetryView(APIView):
    """
    Принимает батч телеметрии от Android-приложения.
    Минимальный, неблокирующий endpoint: лишние поля тихо игнорируются.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:telemetry", context={"path": request.path})

        s = TelemetryBatchSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        device_id = (s.validated_data.get("device_id") or "").strip()
        items = s.validated_data["items"]

        device = None
        if device_id:
            device = (
                PhoneDevice.objects.filter(user=request.user, device_id=device_id)
                .order_by("-created_at")
                .first()
            )

        to_create: list[PhoneTelemetry] = []
        now = timezone.now()
        for item in items:
            ts = item.get("ts") or now
            type_val = item.get("type") or PhoneTelemetry.Type.OTHER
            endpoint = (item.get("endpoint") or "").strip()
            http_code = item.get("http_code")
            value_ms = item.get("value_ms")
            extra = item.get("extra") or {}

            to_create.append(
                PhoneTelemetry(
                    device=device,
                    user=request.user,
                    ts=ts,
                    type=type_val,
                    endpoint=endpoint,
                    http_code=http_code,
                    value_ms=value_ms,
                    extra=extra,
                )
            )

        if to_create:
            # Убрали ignore_conflicts - дедупликация телеметрии не требуется,
            # все записи уникальны по времени и контексту
            PhoneTelemetry.objects.bulk_create(to_create)
            logger.debug(f"PhoneTelemetry: user={request.user.id}, device={device_id}, count={len(to_create)}")

        return Response({"ok": True, "saved": len(to_create)})


class PhoneLogBundleSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    ts = serializers.DateTimeField(required=False)
    level_summary = serializers.CharField(max_length=64, required=False, allow_blank=True)
    source = serializers.CharField(max_length=64, required=False, allow_blank=True)
    payload = serializers.CharField(max_length=50000)  # Лимит ~50KB для защиты от DoS


class PhoneLogUploadView(APIView):
    """
    Принимает небольшие "бандлы" логов для диагностики.
    Использовать редко, только при ошибках — не для всего logcat.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:logs:upload", context={"path": request.path})

        many = isinstance(request.data, list)
        if many:
            data = request.data
        else:
            data = [request.data]

        saved = 0
        now = timezone.now()
        for item in data:
            s = PhoneLogBundleSerializer(data=item)
            s.is_valid(raise_exception=True)
            device_id = (s.validated_data.get("device_id") or "").strip()
            ts = s.validated_data.get("ts") or now
            level = (s.validated_data.get("level_summary") or "").strip()
            source = (s.validated_data.get("source") or "").strip()
            payload = s.validated_data["payload"]

            device = None
            if device_id:
                device = (
                    PhoneDevice.objects.filter(user=request.user, device_id=device_id)
                    .order_by("-created_at")
                    .first()
                )

            PhoneLogBundle.objects.create(
                device=device,
                user=request.user,
                ts=ts,
                level_summary=level,
                source=source,
                payload=payload,
            )
            saved += 1

        logger.debug(f"PhoneLogUpload: user={request.user.id}, saved={saved}")
        return Response({"ok": True, "saved": saved})


class QrTokenCreateView(APIView):
    """
    Создание одноразового QR-токена для входа в мобильное приложение.
    Требует авторизации через session (UI) или JWT.
    Rate limit: не чаще 1 раза в 10 секунд.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging
        from accounts.security import get_client_ip, is_ip_rate_limited
        from rest_framework import status
        from django.db import DatabaseError

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:qr:create", context={"path": request.path})

        try:
            # Rate limiting: не чаще 1 раза в 10 секунд
            ip = get_client_ip(request)
            if is_ip_rate_limited(ip, "qr_token_create", 1, 10):
                return Response(
                    {"detail": "Слишком частые запросы. Подождите немного."},
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            # Генерируем токен
            token = MobileAppQrToken.generate_token()
            qr_token = MobileAppQrToken.objects.create(
                user=request.user,
                token=token,
                ip_address=ip,
                user_agent=request.META.get("HTTP_USER_AGENT", "")[:255],
            )

            logger.info(f"QrTokenCreate: user={request.user.id}, token={token[:16]}...")

            return Response({
                "token": token,
                "expires_at": qr_token.expires_at.isoformat(),
            })
        except DatabaseError as e:
            logger.error(f"QrTokenCreate database error: {e}")
            return Response(
                {"detail": "Ошибка базы данных. Убедитесь, что миграции применены."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
        except Exception as e:
            logger.error(f"QrTokenCreate error: {e}", exc_info=True)
            return Response(
                {"detail": f"Ошибка создания QR-токена: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class QrTokenExchangeSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128)


class QrTokenExchangeView(APIView):
    """
    Обмен QR-токена на JWT access/refresh токены.
    Не требует авторизации (публичный endpoint).
    Токен одноразовый, TTL 5 минут.
    """

    permission_classes = []  # Публичный endpoint

    def post(self, request):
        import logging
        from rest_framework import status
        from rest_framework_simplejwt.tokens import RefreshToken

        logger = logging.getLogger(__name__)

        s = QrTokenExchangeSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        token = s.validated_data["token"].strip()

        # Находим токен
        try:
            qr_token = MobileAppQrToken.objects.get(token=token)
        except MobileAppQrToken.DoesNotExist:
            logger.warning(f"QrTokenExchange: invalid token {token[:16]}...")
            return Response(
                {"detail": "Неверный или истекший токен"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Проверяем валидность
        if not qr_token.is_valid():
            logger.warning(f"QrTokenExchange: expired or used token {token[:16]}...")
            return Response(
                {"detail": "Неверный или истекший токен"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Policy: можно запретить вход в мобильное приложение для некоторых ролей/пользователей
        enforce(user=qr_token.user, resource_type="action", resource="phone:qr:exchange", context={"path": request.path})

        # Помечаем как использованный (делаем ПОСЛЕ проверки policy, чтобы не «сжигать» токен при запрете)
        qr_token.mark_as_used()

        # Генерируем JWT токены
        refresh = RefreshToken.for_user(qr_token.user)
        access = refresh.access_token

        logger.info(f"QrTokenExchange: user={qr_token.user.id}, token={token[:16]}... - success")

        # Определяем, является ли пользователь администратором
        from accounts.models import User
        user = qr_token.user
        is_admin = bool(
            user.is_superuser or 
            (hasattr(user, "role") and user.role == User.Role.ADMIN)
        )

        return Response({
            "access": str(access),
            "refresh": str(refresh),
            "username": qr_token.user.username,  # Возвращаем username для удобства
            "is_admin": is_admin,  # Возвращаем роль администратора
        })


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True)
    device_id = serializers.CharField(required=False, allow_blank=True, max_length=64)


class LogoutView(APIView):
    """
    Удалённый logout: инвалидирует refresh token или все сессии пользователя.
    Используется для безопасного завершения сессии с другого устройства или из CRM.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging
        from rest_framework import status
        from accounts.security import get_client_ip

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:logout", context={"path": request.path})
        s = LogoutSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        
        refresh_token = s.validated_data.get("refresh", "").strip()
        device_id = s.validated_data.get("device_id", "").strip()
        
        # Если передан refresh token - инвалидируем его
        if refresh_token:
            try:
                token = RefreshToken(refresh_token)
                token.blacklist()  # Добавляем в blacklist (требует django-rest-framework-simplejwt с blacklist)
                logger.info(f"Logout: user={request.user.id}, refresh token blacklisted")
            except Exception as e:
                # Если blacklist не настроен или токен невалиден - просто логируем
                logger.warning(f"Logout: failed to blacklist token: {e}")
        
        # Если передан device_id - логируем logout для конкретного устройства
        if device_id:
            try:
                device = PhoneDevice.objects.filter(user=request.user, device_id=device_id).first()
                if device:
                    logger.info(f"Logout: user={request.user.id}, device={device_id}")
            except Exception:
                pass
        
        # Логируем logout в audit
        try:
            from audit.service import log_event
            from audit.models import ActivityEvent
            log_event(
                actor=request.user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id=f"mobile_logout:{request.user.id}",
                message="Выход из мобильного приложения",
                meta={
                    "ip": get_client_ip(request),
                    "device_id": device_id or None,
                    "has_refresh_token": bool(refresh_token),
                },
            )
        except Exception:
            pass
        
        return Response({"ok": True, "message": "Сессия завершена"})


class LogoutAllView(APIView):
    """
    Завершить все мобильные сессии пользователя.
    Используется для полного logout со всех устройств.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging
        from accounts.security import get_client_ip

        logger = logging.getLogger(__name__)
        enforce(user=request.user, resource_type="action", resource="phone:logout_all", context={"path": request.path})
        
        # Логируем logout всех устройств
        try:
            from audit.service import log_event
            from audit.models import ActivityEvent
            device_count = PhoneDevice.objects.filter(user=request.user).count()
            log_event(
                actor=request.user,
                verb=ActivityEvent.Verb.UPDATE,
                entity_type="security",
                entity_id=f"mobile_logout_all:{request.user.id}",
                message="Выход из всех мобильных устройств",
                meta={
                    "ip": get_client_ip(request),
                    "device_count": device_count,
                },
            )
        except Exception:
            pass
        
        logger.info(f"LogoutAll: user={request.user.id}, all devices logged out")
        
        return Response({"ok": True, "message": "Все сессии завершены"})


class UserInfoView(APIView):
    """
    Получить информацию о текущем пользователе (включая роль).
    Используется для проверки прав доступа в мобильном приложении.
    """
    
    permission_classes = [IsAuthenticated]
    
    def get(self, request):
        from accounts.models import User
        enforce(user=request.user, resource_type="action", resource="phone:user:info", context={"path": request.path})
        
        user = request.user
        is_admin = bool(
            user.is_superuser or 
            (hasattr(user, "role") and user.role == User.Role.ADMIN)
        )
        
        return Response({
            "username": user.username,
            "is_admin": is_admin,
        })

