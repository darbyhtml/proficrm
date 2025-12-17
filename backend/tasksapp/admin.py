from django.contrib import admin

from accounts.models import User
from .models import Task, TaskType


@admin.register(TaskType)
class TaskTypeAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "status", "assigned_to", "company", "due_at", "created_at")
    list_filter = ("status",)
    search_fields = ("title", "description", "company__name", "assigned_to__username")

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        user: User = request.user
        if user.role == User.Role.MANAGER:
            ro += ["assigned_to"]
        return tuple(dict.fromkeys(ro))

    def save_model(self, request, obj, form, change):
        user: User = request.user
        if obj.created_by_id is None:
            obj.created_by = user
        if user.role == User.Role.MANAGER:
            obj.assigned_to = user
        super().save_model(request, obj, form, change)

# Register your models here.
