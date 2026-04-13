"""
URL configuration for messenger app.
"""

from django.urls import path

from . import views
from messenger.api import (
    branches_list_view,
    heartbeat_view,
    transfer_conversation,
)

urlpatterns = [
    path("widget-demo/", views.widget_demo, name="messenger_widget_demo"),
    path("widget-test/", views.widget_test_page, name="messenger_widget_test"),
    # Heartbeat онлайн-статуса оператора (префикс api/messenger/ задаётся здесь,
    # т.к. messenger.urls подключён в crm/urls.py с пустым префиксом)
    path("api/messenger/heartbeat/", heartbeat_view, name="messenger-heartbeat"),
    path("api/messenger/branches/", branches_list_view, name="messenger-branches"),
    path(
        "api/messenger/conversations/<int:conversation_id>/transfer/",
        transfer_conversation,
        name="messenger-transfer",
    ),
]
