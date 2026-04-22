"""W2.2 — TOTP 2FA views: setup, verify, recovery.

Soft-mandatory pattern:
- Setup: GET displays QR + secret, POST verifies token and saves device.
- Verify: GET form, POST checks TOTP code (or recovery code), sets session flag.
- Both: require login.

NO middleware enforcement до explicit commit в settings.py.
"""

from __future__ import annotations

import base64
import io
import logging
import secrets

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from accounts.models import AdminRecoveryCode, AdminTOTPDevice

logger = logging.getLogger(__name__)


def _user_requires_2fa(user) -> bool:
    """Kтo должен иметь 2FA (mandatory-ready list)."""
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.is_staff or getattr(user, "role", "") == "admin")


def _generate_totp_secret() -> str:
    """Generate base32 TOTP secret (20 bytes = 32 base32 chars per RFC 6238)."""
    import pyotp

    return pyotp.random_base32()


def _qr_png_base64(uri: str) -> str:
    """Render QR code as base64-encoded PNG для inline <img src='data:...'>."""
    import qrcode

    qr = qrcode.QRCode(version=None, box_size=8, border=2)
    qr.add_data(uri)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _generate_recovery_codes(user, count: int = 10) -> list[str]:
    """Generate & store `count` one-time recovery codes.

    Format: 8 uppercase hex chars (e.g. A1B2-C3D4). Hash stored, plaintext returned
    once.
    """
    # Wipe old codes
    AdminRecoveryCode.objects.filter(user=user).delete()
    plain_codes = []
    for _ in range(count):
        raw = secrets.token_hex(4).upper()  # 8 chars
        formatted = f"{raw[:4]}-{raw[4:]}"
        plain_codes.append(formatted)
        AdminRecoveryCode.objects.create(
            user=user,
            code_hash=AdminRecoveryCode.hash_code(raw),
        )
    return plain_codes


@login_required
@require_http_methods(["GET", "POST"])
def totp_setup(request: HttpRequest) -> HttpResponse:
    """Setup TOTP device: generate secret + QR, verify на POST."""
    user = request.user
    if not _user_requires_2fa(user):
        return HttpResponseForbidden("2FA setup доступен только админам.")

    device = AdminTOTPDevice.objects.filter(user=user).first()

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        if not device:
            messages.error(request, "Нет unconfirmed device. Повторите setup с GET.")
            return redirect("totp_setup")
        if device.verify(token):
            device.confirmed = True
            device.save(update_fields=["confirmed"])
            # Recovery codes generation
            codes = _generate_recovery_codes(user)
            request.session["otp_verified"] = True
            request.session.set_expiry(0)  # session cookie, не persist
            logger.info("TOTP setup completed для user %s", user.username)
            return render(
                request,
                "accounts/2fa/setup_complete.html",
                {"recovery_codes": codes},
            )
        messages.error(request, "Неверный код. Проверьте приложение.")

    # GET — create unconfirmed device если его нет, показать QR
    if not device or not device.confirmed:
        if device:
            # Regenerate secret для unconfirmed device
            device.secret_key = _generate_totp_secret()
            device.save(update_fields=["secret_key"])
        else:
            device = AdminTOTPDevice.objects.create(
                user=user,
                secret_key=_generate_totp_secret(),
                confirmed=False,
            )
        uri = device.provisioning_uri()
        qr_b64 = _qr_png_base64(uri)
        return render(
            request,
            "accounts/2fa/setup.html",
            {
                "qr_base64": qr_b64,
                "secret": device.secret_key,
            },
        )

    # Already confirmed
    messages.info(
        request, "2FA уже настроен. Для смены устройства обратитесь к администратору системы."
    )
    return redirect("dashboard")


@login_required
@require_http_methods(["GET", "POST"])
def totp_verify(request: HttpRequest) -> HttpResponse:
    """Verify TOTP token или recovery code на login."""
    user = request.user
    device = AdminTOTPDevice.objects.filter(user=user, confirmed=True).first()
    if not device:
        # Нет confirmed device — setup flow
        return redirect("totp_setup")

    next_url = (request.POST.get("next") or request.GET.get("next") or "/").strip() or "/"
    # Safe-next check
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/"

    if request.method == "POST":
        token = request.POST.get("token", "").strip()
        recovery = request.POST.get("recovery_code", "").strip()

        if recovery and AdminRecoveryCode.verify_and_consume(user, recovery):
            request.session["otp_verified"] = True
            logger.info("2FA verified via recovery code для %s", user.username)
            messages.success(request, "Recovery code принят. Рекомендуется перегенерировать 2FA.")
            return redirect(next_url)

        if token and device.verify(token):
            request.session["otp_verified"] = True
            logger.info("2FA verified via TOTP для %s", user.username)
            return redirect(next_url)

        messages.error(request, "Неверный код. Попробуйте снова.")

    return render(
        request,
        "accounts/2fa/verify.html",
        {"next": next_url},
    )
