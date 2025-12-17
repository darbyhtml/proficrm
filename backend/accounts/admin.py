from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Branch, User


@admin.register(Branch)
class BranchAdmin(admin.ModelAdmin):
    list_display = ("code", "name")
    search_fields = ("code", "name")


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ("username", "email", "role", "data_scope", "branch", "is_active", "is_staff")
    list_filter = ("role", "data_scope", "branch", "is_active", "is_staff")
    fieldsets = DjangoUserAdmin.fieldsets + (
        ("CRM", {"fields": ("role", "data_scope", "branch")}),
    )

# Register your models here.
