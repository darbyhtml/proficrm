from django.contrib import admin

from accounts.models import User
from accounts.scope import apply_company_scope
from .models import (
    Company,
    CompanyDeletionRequest,
    CompanyNote,
    CompanySphere,
    CompanyStatus,
    ContractType,
    Region,
    Contact,
    ContactEmail,
    ContactPhone,
)


@admin.register(CompanyStatus)
class CompanyStatusAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(CompanySphere)
class CompanySphereAdmin(admin.ModelAdmin):
    search_fields = ("name",)
    list_display = ("name", "is_important")
    list_editable = ("is_important",)


@admin.register(Region)
class RegionAdmin(admin.ModelAdmin):
    search_fields = ("name",)


@admin.register(ContractType)
class ContractTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "warning_days", "danger_days", "order")
    search_fields = ("name",)
    list_editable = ("warning_days", "danger_days", "order")


class ContactEmailInline(admin.TabularInline):
    model = ContactEmail
    extra = 0


class ContactPhoneInline(admin.TabularInline):
    model = ContactPhone
    extra = 0


@admin.register(Contact)
class ContactAdmin(admin.ModelAdmin):
    list_display = ("last_name", "first_name", "company", "position", "status")
    search_fields = ("first_name", "last_name", "position", "company__name")
    inlines = (ContactEmailInline, ContactPhoneInline)


@admin.register(CompanyNote)
class CompanyNoteAdmin(admin.ModelAdmin):
    list_display = ("company", "author", "created_at")
    search_fields = ("company__name", "author__username", "text")


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "inn", "responsible", "branch", "status", "updated_at")
    search_fields = ("name", "inn", "legal_name", "address")
    list_filter = ("branch", "status")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return apply_company_scope(qs, request.user)

    def get_readonly_fields(self, request, obj=None):
        ro = list(super().get_readonly_fields(request, obj))
        user: User = request.user
        if user.role == User.Role.MANAGER:
            # менеджер всегда работает только со своими компаниями
            ro += ["responsible", "branch"]
            if obj is not None:
                # и не меняет критичные поля уже созданной карточки
                ro += ["inn", "kpp"]
        return tuple(dict.fromkeys(ro))

    def save_model(self, request, obj, form, change):
        user: User = request.user
        if user.role == User.Role.MANAGER:
            obj.responsible = user
            obj.branch = user.branch
        super().save_model(request, obj, form, change)


@admin.register(CompanyDeletionRequest)
class CompanyDeletionRequestAdmin(admin.ModelAdmin):
    list_display = ("company_name_snapshot", "requested_by", "status", "decided_by", "created_at", "decided_at")
    list_filter = ("status", "created_at", "decided_at")
    search_fields = ("company_name_snapshot", "requested_by__username", "note", "decision_note")
    readonly_fields = ("company_id_snapshot", "company_name_snapshot", "requested_by", "requested_by_branch", "created_at")



# Register your models here.
