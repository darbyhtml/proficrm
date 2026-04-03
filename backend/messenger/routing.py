"""
WebSocket URL routing для messenger.

Маршруты:
- ws/messenger/operator/ — для операторов CRM
- ws/messenger/widget/{widget_token}/ — для виджета посетителя
"""

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    re_path(r"ws/messenger/operator/$", consumers.OperatorConsumer.as_asgi()),
    re_path(r"ws/messenger/widget/(?P<widget_token>[a-zA-Z0-9_-]+)/$", consumers.WidgetConsumer.as_asgi()),
]
