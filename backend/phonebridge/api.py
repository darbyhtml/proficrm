from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.db import transaction
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CallRequest, PhoneDevice


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


