"""
URL configuration for crm project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.templatetags.static import static
from django.urls import include, path
from django.views.generic import RedirectView
from rest_framework import routers

from accounts.jwt_views import LoggedTokenRefreshView, SecureTokenObtainPairView
from accounts.models import User
from accounts.views import SecureLoginView, magic_link_login
from accounts.views_2fa import totp_setup as _totp_setup
from accounts.views_2fa import totp_verify as _totp_verify
from companies.api import CompanyNoteViewSet, CompanyViewSet, ContactViewSet
from core.api import FeatureFlagsView  # Wave 0.3
from crm.health import health as liveness_view  # Wave 0.4
from crm.health import ready as readiness_view  # Wave 0.4
from crm.health import sentry_smoke, staff_trigger_test_error  # Wave 0.4
from crm.views import health_check, metrics_endpoint, robots_txt, security_txt, sw_push_js
from messenger.api import (
    AutomationRuleViewSet,
    CampaignViewSet,
    CannedResponseViewSet,
    ConversationLabelViewSet,
    ConversationViewSet,
    MacroViewSet,
    PushSubscriptionViewSet,
    ReportingViewSet,
)
from messenger.widget_api import (
    widget_attachment_download,
    widget_bootstrap,
    widget_campaigns,
    widget_contact_update,
    widget_mark_read,
    widget_offhours_request,
    widget_poll,
    widget_rate,
    widget_send,
    widget_stream,
    widget_typing,
)
from phonebridge.api import (
    DeviceHeartbeatView,
    LogoutAllView,
    LogoutView,
    MobileAppLatestView,
    PhoneLogUploadView,
    PhoneTelemetryView,
    PullCallView,
    QrTokenCreateView,
    QrTokenExchangeView,
    QrTokenStatusView,
    RegisterDeviceView,
    UpdateCallInfoView,
    UserInfoView,
)
from tasksapp.api import TaskTypeViewSet, TaskViewSet

handler404 = "crm.views.handler404"


admin.site.site_header = "CRM — Админка"
admin.site.site_title = "CRM Admin"
admin.site.index_title = "Управление"


def _admin_has_permission(request):
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated or not user.is_active:
        return False
    # Админка доступна только администратору (роль) или суперпользователю.
    if user.is_superuser:
        return True
    return bool(user.is_staff and getattr(user, "role", None) == User.Role.ADMIN)


admin.site.has_permission = _admin_has_permission

# Canonical router — SimpleRouter (no API root browser page, avoids endpoint discovery)
router = routers.SimpleRouter()
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"contacts", ContactViewSet, basename="contact")
router.register(r"company-notes", CompanyNoteViewSet, basename="company-note")
router.register(r"task-types", TaskTypeViewSet, basename="task-type")
router.register(r"tasks", TaskViewSet, basename="task")
router.register(r"conversations", ConversationViewSet, basename="conversation")
router.register(r"canned-responses", CannedResponseViewSet, basename="canned-response")
router.register(r"conversation-labels", ConversationLabelViewSet, basename="conversation-label")
router.register(r"push", PushSubscriptionViewSet, basename="push")
router.register(r"campaigns", CampaignViewSet, basename="campaign")
router.register(r"automation-rules", AutomationRuleViewSet, basename="automation-rule")
router.register(r"messenger-reports", ReportingViewSet, basename="messenger-report")
router.register(r"macros", MacroViewSet, basename="macro")

# Versioned router at /api/v1/ — same viewsets, separate basenames to avoid URL name conflicts.
# SimpleRouter = no API root browser page (cleaner for versioned endpoint).
router_v1 = routers.SimpleRouter()
router_v1.register(r"companies", CompanyViewSet, basename="v1-company")
router_v1.register(r"contacts", ContactViewSet, basename="v1-contact")
router_v1.register(r"company-notes", CompanyNoteViewSet, basename="v1-company-note")
router_v1.register(r"task-types", TaskTypeViewSet, basename="v1-task-type")
router_v1.register(r"tasks", TaskViewSet, basename="v1-task")
router_v1.register(r"conversations", ConversationViewSet, basename="v1-conversation")
router_v1.register(r"canned-responses", CannedResponseViewSet, basename="v1-canned-response")

urlpatterns = [
    path("robots.txt", robots_txt, name="robots_txt"),
    path(".well-known/security.txt", security_txt, name="security_txt"),
    path("health/", health_check, name="health_check"),
    # Wave 0.4: разделённые endpoint'ы. /live/ — чистый liveness (всегда 200 если
    # процесс жив, НЕ трогает БД/Redis). /ready/ — readiness (БД+Redis без Celery,
    # для K8s-style probe). /health/ оставлен как есть для совместимости.
    path("live/", liveness_view, name="liveness"),
    path("ready/", readiness_view, name="readiness"),
    # Smoke-test GlitchTip SDK — 404 в проде, 500 в DEBUG.
    path("_debug/sentry-error/", sentry_smoke, name="sentry_smoke"),
    # W0.4 closeout: real-traffic verification endpoint для Playwright.
    # 3-level gated: env flag + login + is_staff. 404 если выключено.
    path("_staff/trigger-test-error/", staff_trigger_test_error, name="staff_test_error"),
    path("metrics", metrics_endpoint, name="metrics"),
    # Service Worker для push-уведомлений — отдаём напрямую (браузеры запрещают SW через redirect)
    path("sw-push.js", sw_push_js, name="sw_push"),
    path("favicon.ico", RedirectView.as_view(url=static("ui/favicon-v2.svg"), permanent=True)),
    path("django-admin/", admin.site.urls),
    path("", include("ui.urls")),
    path("", include("messenger.urls")),
    path("", include("mailer.urls")),
    path("", include("notifications.urls")),
    # Session auth for UI (without weird /login/login/ prefixes) - с защитой от брутфорса
    path("login/", SecureLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    # W2.2 — TOTP 2FA для admins (middleware enforcement pending separate commit)
    path("accounts/2fa/setup/", _totp_setup, name="totp_setup"),
    path("accounts/2fa/verify/", _totp_verify, name="totp_verify"),
    # Magic link authentication
    path("auth/magic/<str:token>/", magic_link_login, name="magic_link_login"),
    # JWT token endpoints: canonical + /api/v1/ alias (no names for aliases to avoid conflicts)
    path("api/token/", SecureTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", LoggedTokenRefreshView.as_view(), name="token_refresh"),
    path("api/v1/token/", SecureTokenObtainPairView.as_view()),
    path("api/v1/token/refresh/", LoggedTokenRefreshView.as_view()),
    # Wave 0.3: feature flags API — фронт узнаёт какие флаги активны.
    path("api/v1/feature-flags/", FeatureFlagsView.as_view(), name="feature_flags_list"),
    # Phonebridge: canonical at /api/phone/ (backward compat)
    path("api/phone/devices/register/", RegisterDeviceView.as_view(), name="phone_register_device"),
    path(
        "api/phone/devices/heartbeat/", DeviceHeartbeatView.as_view(), name="phone_device_heartbeat"
    ),
    path("api/phone/calls/pull/", PullCallView.as_view(), name="phone_pull_call"),
    path("api/phone/calls/update/", UpdateCallInfoView.as_view(), name="phone_update_call_info"),
    path("api/phone/telemetry/", PhoneTelemetryView.as_view(), name="phone_telemetry"),
    path("api/phone/logs/", PhoneLogUploadView.as_view(), name="phone_logs"),
    path("api/phone/qr/create/", QrTokenCreateView.as_view(), name="phone_qr_create"),
    path("api/phone/user/info/", UserInfoView.as_view(), name="phone_user_info"),
    path("api/phone/qr/exchange/", QrTokenExchangeView.as_view(), name="phone_qr_exchange"),
    path("api/phone/qr/status/", QrTokenStatusView.as_view(), name="phone_qr_status"),
    # F9 (2026-04-18): latest APK info для CRMProfiDialer auto-update.
    path("api/phone/app/latest/", MobileAppLatestView.as_view(), name="phone_app_latest"),
    path("api/phone/logout/", LogoutView.as_view(), name="phone_logout"),
    path("api/phone/logout/all/", LogoutAllView.as_view(), name="phone_logout_all"),
    # Widget API (публичный, без аутентификации)
    path("api/widget/bootstrap/", widget_bootstrap, name="widget-bootstrap"),
    path("api/widget/contact/", widget_contact_update, name="widget-contact"),
    path("api/widget/offhours-request/", widget_offhours_request, name="widget-offhours-request"),
    path("api/widget/send/", widget_send, name="widget-send"),
    path("api/widget/poll/", widget_poll, name="widget-poll"),
    path("api/widget/stream/", widget_stream, name="widget-stream"),
    path("api/widget/typing/", widget_typing, name="widget-typing"),
    path("api/widget/mark_read/", widget_mark_read, name="widget-mark-read"),
    path("api/widget/rate/", widget_rate, name="widget-rate"),
    path("api/widget/campaigns/", widget_campaigns, name="widget-campaigns"),
    path(
        "api/widget/attachment/<int:attachment_id>/",
        widget_attachment_download,
        name="widget-attachment",
    ),
    # Phonebridge: /api/v1/phone/ aliases (no names to avoid conflicts)
    path("api/v1/phone/devices/register/", RegisterDeviceView.as_view()),
    path("api/v1/phone/devices/heartbeat/", DeviceHeartbeatView.as_view()),
    path("api/v1/phone/calls/pull/", PullCallView.as_view()),
    path("api/v1/phone/calls/update/", UpdateCallInfoView.as_view()),
    path("api/v1/phone/telemetry/", PhoneTelemetryView.as_view()),
    path("api/v1/phone/logs/", PhoneLogUploadView.as_view()),
    path("api/v1/phone/qr/create/", QrTokenCreateView.as_view()),
    path("api/v1/phone/user/info/", UserInfoView.as_view()),
    path("api/v1/phone/qr/exchange/", QrTokenExchangeView.as_view()),
    path("api/v1/phone/qr/status/", QrTokenStatusView.as_view()),
    path("api/v1/phone/logout/", LogoutView.as_view()),
    path("api/v1/phone/logout/all/", LogoutAllView.as_view()),
    # DRF resources: /api/ canonical + /api/v1/ versioned (separate routers, no name conflict)
    path("api/", include(router.urls)),
    path("api/v1/", include(router_v1.urls)),
]

# Serve user-uploaded media files in development
if settings.DEBUG:
    from django.conf.urls.static import static as static_files

    urlpatterns += static_files(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

    # OpenAPI schema + Swagger UI — только в development (не экспонировать в production)
    from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path(
            "api/schema/swagger-ui/",
            SpectacularSwaggerView.as_view(url_name="schema"),
            name="swagger-ui",
        ),
    ]
