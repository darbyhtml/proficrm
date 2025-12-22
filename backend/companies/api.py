from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from django_filters.rest_framework import DjangoFilterBackend

from accounts.models import User
from .permissions import can_edit_company
from .models import Company, Contact, CompanyNote


class CompanySerializer(serializers.ModelSerializer):
    class Meta:
        model = Company
        fields = [
            "id",
            "name",
            "legal_name",
            "inn",
            "kpp",
            "address",
            "website",
            "phone",
            "email",
            "contact_name",
            "contact_position",
            "status",
            "spheres",
            "responsible",
            "branch",
            "created_at",
            "updated_at",
        ]


class CompanyViewSet(viewsets.ModelViewSet):
    serializer_class = CompanySerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("branch", "responsible", "status")
    search_fields = ("name", "inn", "legal_name", "address", "phone", "email", "contact_name", "contact_position")
    ordering_fields = ("updated_at", "created_at", "name")

    def get_queryset(self):
        return Company.objects.all().order_by("-updated_at")

    def perform_create(self, serializer):
        user: User = self.request.user
        data = dict(serializer.validated_data)

        # РОП: только просмотр компаний + заметки (без создания/редактирования компаний)
        if user.role == User.Role.SALES_HEAD:
            raise PermissionDenied("Руководитель отдела продаж не может создавать компании.")

        responsible = data.get("responsible")
        branch = data.get("branch")

        # По умолчанию: ответственный = создатель
        if responsible is None:
            responsible = user

        # Роли/ограничения
        if user.role == User.Role.MANAGER:
            # менеджер не может назначать чужого ответственного
            if responsible.id != user.id:
                raise PermissionDenied("Менеджер не может назначать другого ответственного.")
            # филиал только свой
            if branch is not None and user.branch_id and branch.id != user.branch_id:
                raise PermissionDenied("Менеджер не может назначать другой филиал.")

        if user.role == User.Role.BRANCH_DIRECTOR:
            # директор филиала назначает только внутри своего филиала
            if user.branch_id and responsible.branch_id and responsible.branch_id != user.branch_id:
                raise PermissionDenied("Можно назначать ответственного только в своём филиале.")

        # Автовывод филиала, если не задан
        if branch is None:
            branch = responsible.branch

        serializer.save(responsible=responsible, branch=branch, created_by=user)

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: Company = self.get_object()
        data = dict(serializer.validated_data)

        if not can_edit_company(user, obj):
            raise PermissionDenied("Нет прав на редактирование компании.")

        new_responsible = data.get("responsible", obj.responsible)
        new_branch = data.get("branch", obj.branch)

        if user.role == User.Role.MANAGER:
            # менеджер не может менять ответственного/филиал у существующей компании
            if "responsible" in data and obj.responsible_id != new_responsible.id:
                raise PermissionDenied("Менеджер не может менять ответственного у существующей компании.")
            if "branch" in data and (obj.branch_id != (new_branch.id if new_branch else None)):
                raise PermissionDenied("Менеджер не может менять филиал у существующей компании.")

        if user.role == User.Role.BRANCH_DIRECTOR and user.branch_id:
            # директор филиала может переназначать только внутри филиала
            if new_responsible and new_responsible.branch_id and new_responsible.branch_id != user.branch_id:
                raise PermissionDenied("Можно назначать ответственного только в своём филиале.")
            if new_branch and new_branch.id != user.branch_id:
                raise PermissionDenied("Нельзя назначать компании другой филиал.")

        serializer.save()


class ContactSerializer(serializers.ModelSerializer):
    class Meta:
        model = Contact
        fields = [
            "id",
            "company",
            "first_name",
            "last_name",
            "position",
            "status",
            "note",
            "created_at",
            "updated_at",
        ]


class ContactViewSet(viewsets.ModelViewSet):
    serializer_class = ContactSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("company",)
    search_fields = ("first_name", "last_name", "position", "company__name")
    ordering_fields = ("updated_at", "created_at", "last_name")

    def get_queryset(self):
        return Contact.objects.all().order_by("-updated_at")

    def perform_create(self, serializer):
        user: User = self.request.user
        company: Company = serializer.validated_data["company"]
        if not can_edit_company(user, company):
            raise PermissionDenied("Нет прав на добавление контактов для этой компании.")
        serializer.save()

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: Contact = self.get_object()
        if not can_edit_company(user, obj.company):
            raise PermissionDenied("Нет прав на редактирование контактов этой компании.")
        serializer.save()

    def perform_destroy(self, instance):
        user: User = self.request.user
        if not can_edit_company(user, instance.company):
            raise PermissionDenied("Нет прав на удаление контактов этой компании.")
        instance.delete()


class CompanyNoteSerializer(serializers.ModelSerializer):
    class Meta:
        model = CompanyNote
        fields = ["id", "company", "author", "text", "created_at"]
        read_only_fields = ["author", "created_at"]


class CompanyNoteViewSet(viewsets.ModelViewSet):
    serializer_class = CompanyNoteSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("company",)
    ordering_fields = ("created_at",)

    def get_queryset(self):
        return CompanyNote.objects.all().order_by("-created_at")

    def perform_create(self, serializer):
        user: User = self.request.user
        company: Company = serializer.validated_data["company"]
        if not can_edit_company(user, company):
            raise PermissionDenied("Нет прав на добавление заметок для этой компании.")
        serializer.save(author=user)

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: CompanyNote = self.get_object()
        if not can_edit_company(user, obj.company):
            raise PermissionDenied("Нет прав на редактирование заметок этой компании.")
        # правило: обычный пользователь может править только свои заметки
        if not (user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER)) and obj.author_id != user.id:
            raise PermissionDenied("Можно редактировать только свои заметки.")
        serializer.save()

    def perform_destroy(self, instance):
        user: User = self.request.user
        if not can_edit_company(user, instance.company):
            raise PermissionDenied("Нет прав на удаление заметок этой компании.")
        if not (user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER)) and instance.author_id != user.id:
            raise PermissionDenied("Можно удалять только свои заметки.")
        instance.delete()


