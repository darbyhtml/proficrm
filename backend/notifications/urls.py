from django.urls import path

from . import views

urlpatterns = [
    path("notifications/mark-all-read/", views.mark_all_read, name="notifications_mark_all_read"),
    path("notifications/<int:notification_id>/read/", views.mark_read, name="notifications_mark_read"),
    path("notifications/poll/", views.poll, name="notifications_poll"),
]


