"""
URL configuration for messenger app.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("widget-demo/", views.widget_demo, name="messenger_widget_demo"),
]
