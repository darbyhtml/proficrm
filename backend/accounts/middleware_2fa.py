"""W2.2 — TwoFactorMandatoryMiddleware.

Soft-mandatory pattern для admin users:
- Admin без confirmed TOTPDevice → redirect к /2fa/setup/ (не hard block).
- Admin с device но без session verification → redirect к /2fa/verify/.
- Non-admin users → pass-through.

NOT enabled by default. Register в settings.MIDDLEWARE когда готов
(CRITICAL: после того как first admin (sdm) configured device, иначе lockout).
"""

from __future__ import annotations

from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

SAFE_PATH_PREFIXES = (
    "/accounts/login/",
    "/accounts/logout/",
    "/accounts/2fa/setup/",
    "/accounts/2fa/verify/",
    "/static/",
    "/media/",
    "/live/",
    "/ready/",
    "/health/",
    # API endpoints используют свою auth (JWT) — не 2FA gated.
    "/api/",
)


def _user_requires_2fa(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    return bool(user.is_superuser or user.is_staff or getattr(user, "role", "") == "admin")


class TwoFactorMandatoryMiddleware(MiddlewareMixin):
    """Enforce 2FA для admin-role users.

    Flow:
    - Anonymous → pass-through (login_required elsewhere handles).
    - Non-admin authenticated → pass-through.
    - Admin authenticated с verified session flag → pass-through.
    - Admin authenticated без device → redirect к setup.
    - Admin authenticated с device но без session flag → redirect к verify.

    Safe paths bypass middleware entirely.
    """

    def process_request(self, request):
        if not request.user.is_authenticated:
            return None

        path = request.path
        if any(path.startswith(prefix) for prefix in SAFE_PATH_PREFIXES):
            return None

        user = request.user
        if not _user_requires_2fa(user):
            return None

        # Check if session already verified
        if request.session.get("otp_verified"):
            return None

        # Look up device
        from accounts.models import AdminTOTPDevice

        device = AdminTOTPDevice.objects.filter(user=user, confirmed=True).first()
        if not device:
            # No confirmed device → setup flow
            return HttpResponseRedirect(f"{reverse('totp_setup')}?next={path}")

        # Has device, session not verified → verify flow
        return HttpResponseRedirect(f"{reverse('totp_verify')}?next={path}")
