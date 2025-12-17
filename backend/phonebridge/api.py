from __future__ import annotations

from django.utils import timezone
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
        device_id = (request.query_params.get("device_id") or "").strip()
        if not device_id:
            return Response({"detail": "device_id is required"}, status=400)

        # обновим last_seen
        PhoneDevice.objects.filter(user=request.user, device_id=device_id).update(last_seen_at=timezone.now())

        call = (
            CallRequest.objects.filter(user=request.user, status=CallRequest.Status.PENDING)
            .order_by("created_at")
            .first()
        )
        if not call:
            return Response(status=204)

        call.status = CallRequest.Status.CONSUMED
        now = timezone.now()
        call.delivered_at = now
        call.consumed_at = now
        call.save(update_fields=["status", "delivered_at", "consumed_at"])

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


