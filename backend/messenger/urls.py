"""
URL configuration for messenger app.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("widget-demo/", views.widget_demo, name="messenger_widget_demo"),
    path("widget-test/", views.widget_test_page, name="messenger_widget_test"),
]
