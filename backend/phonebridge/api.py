from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CallRequest, PhoneDevice, PhoneTelemetry, PhoneLogBundle


class RegisterDeviceSerializer(serializers.Serializer):
    device_id = serializers.CharField(max_length=64)
    device_name = serializers.CharField(max_length=120, required=False, allow_blank=True)
    fcm_token = serializers.CharField(required=False, allow_blank=True)


class RegisterDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
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

        s = DeviceHeartbeatSerializer(data=request.data)
        s.is_valid(raise_exception=True)
        device_id = s.validated_data["device_id"].strip()
        device_name = (s.validated_data.get("device_name") or "").strip()
        app_version = (s.validated_data.get("app_version") or "").strip()
        last_poll_code = s.validated_data.get("last_poll_code")
        last_poll_at = s.validated_data.get("last_poll_at")
        encryption_enabled = s.validated_data.get("encryption_enabled", True)

        if not device_id:
            return Response({"detail": "device_id is required"}, status=400)

        # Определяем IP для диагностики (поддержка X-Forwarded-For и X-Real-IP для работы за прокси)
        # ВАЖНО: в production нужно настроить ALLOWED_HOSTS и доверие к прокси в Django settings
        ip = (
            request.META.get("HTTP_X_REAL_IP", "").strip() or
            (request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip() if request.META.get("HTTP_X_FORWARDED_FOR") else "") or
            request.META.get("REMOTE_ADDR", "")
        )

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
            },
        )
        
        # Логируем предупреждение, если шифрование отключено
        if not encryption_enabled:
            logger.warning(f"DeviceHeartbeat: user={request.user.id}, device={device_id} - encryption DISABLED (security risk)")

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

        logger.info(f"PullCallView: delivered call {call.id} to user {request.user.id}, phone {call.phone_raw}")

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
    call_request_id = serializers.UUIDField()
    call_status = serializers.ChoiceField(choices=CallRequest.CallStatus.choices, required=False, allow_null=True)
    call_started_at = serializers.DateTimeField(required=False, allow_null=True)
    call_duration_seconds = serializers.IntegerField(required=False, allow_null=True, min_value=0)


class UpdateCallInfoView(APIView):
    """
    Endpoint для отправки данных о фактическом звонке из Android приложения.
    Android приложение собирает данные из CallLog и отправляет сюда.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging
        logger = logging.getLogger(__name__)
        
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

        # Обновляем данные о звонке
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


class PhoneTelemetryView(APIView):
    """
    Принимает батч телеметрии от Android-приложения.
    Минимальный, неблокирующий endpoint: лишние поля тихо игнорируются.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)

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
    payload = serializers.CharField()


class PhoneLogUploadView(APIView):
    """
    Принимает небольшие "бандлы" логов для диагностики.
    Использовать редко, только при ошибках — не для всего logcat.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        import logging

        logger = logging.getLogger(__name__)

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

