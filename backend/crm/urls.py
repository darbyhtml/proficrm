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
from django.contrib import admin
from django.urls import include, path
from django.contrib.auth import views as auth_views
from django.views.generic import RedirectView
from django.templatetags.static import static

from rest_framework import routers
from rest_framework_simplejwt.views import TokenRefreshView

from accounts.models import User
from accounts.views import SecureLoginView
from accounts.jwt_views import SecureTokenObtainPairView
from companies.api import CompanyNoteViewSet, CompanyViewSet, ContactViewSet
from tasksapp.api import TaskTypeViewSet, TaskViewSet
from phonebridge.api import PullCallView, RegisterDeviceView, UpdateCallInfoView
from crm.views import robots_txt

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

router = routers.DefaultRouter()
router.register(r"companies", CompanyViewSet, basename="company")
router.register(r"contacts", ContactViewSet, basename="contact")
router.register(r"company-notes", CompanyNoteViewSet, basename="company-note")
router.register(r"task-types", TaskTypeViewSet, basename="task-type")
router.register(r"tasks", TaskViewSet, basename="task")

urlpatterns = [
    path("robots.txt", robots_txt, name="robots_txt"),
    path("favicon.ico", RedirectView.as_view(url=static("ui/favicon-v2.svg"), permanent=True)),
    path('admin/', admin.site.urls),
    path("", include("ui.urls")),
    path("", include("mailer.urls")),
    path("", include("notifications.urls")),
    # Session auth for UI (without weird /login/login/ prefixes) - с защитой от брутфорса
    path("login/", SecureLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("api/token/", SecureTokenObtainPairView.as_view(), name="token_obtain_pair"),
    path("api/token/refresh/", TokenRefreshView.as_view(), name="token_refresh"),
    path("api/phone/devices/register/", RegisterDeviceView.as_view(), name="phone_register_device"),
    path("api/phone/calls/pull/", PullCallView.as_view(), name="phone_pull_call"),
    path("api/phone/calls/update/", UpdateCallInfoView.as_view(), name="phone_update_call_info"),
    path("api/", include(router.urls)),
]
