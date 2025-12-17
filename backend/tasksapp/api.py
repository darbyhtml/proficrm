from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from django_filters.rest_framework import DjangoFilterBackend

from accounts.models import User
from accounts.scope import apply_company_scope
from companies.models import Company
from .models import Task, TaskType


class TaskTypeSerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskType
        fields = ["id", "name"]


class TaskSerializer(serializers.ModelSerializer):
    class Meta:
        model = Task
        fields = [
            "id",
            "title",
            "description",
            "status",
            "created_by",
            "assigned_to",
            "company",
            "type",
            "created_at",
            "due_at",
            "completed_at",
            "recurrence_rrule",
        ]
        read_only_fields = ["created_by", "created_at", "completed_at"]


class TaskTypeViewSet(viewsets.ModelViewSet):
    serializer_class = TaskTypeSerializer
    queryset = TaskType.objects.all().order_by("name")
    search_fields = ("name",)


class TaskViewSet(viewsets.ModelViewSet):
    serializer_class = TaskSerializer
    filter_backends = (DjangoFilterBackend,)
    filterset_fields = ("status", "assigned_to", "company", "type")
    search_fields = ("title", "description", "company__name")
    ordering_fields = ("created_at", "due_at")

    def get_queryset(self):
        user: User = self.request.user
        qs = Task.objects.select_related("company", "assigned_to", "created_by").order_by("-created_at")

        # Ограничиваем задачи через scope компаний + всегда показываем задачи, назначенные пользователю.
        company_qs = apply_company_scope(Company.objects.all(), user)
        qs = qs.filter(company__in=company_qs) | qs.filter(assigned_to=user)
        return qs.distinct()

    def perform_create(self, serializer):
        user: User = self.request.user
        data = dict(serializer.validated_data)
        assigned_to = data.get("assigned_to") or user

        if user.role == User.Role.MANAGER and assigned_to.id != user.id:
            raise PermissionDenied("Менеджер может назначать задачи только себе.")

        if user.role == User.Role.BRANCH_DIRECTOR and user.branch_id:
            if assigned_to.branch_id and assigned_to.branch_id != user.branch_id:
                raise PermissionDenied("Директор филиала может назначать задачи только сотрудникам своего филиала.")

        serializer.save(created_by=user, assigned_to=assigned_to)

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: Task = self.get_object()
        data = dict(serializer.validated_data)

        if "assigned_to" in data:
            assigned_to = data["assigned_to"]

            if user.role == User.Role.MANAGER and assigned_to.id != user.id:
                raise PermissionDenied("Менеджер не может переназначать задачи другим.")

            if user.role == User.Role.BRANCH_DIRECTOR and user.branch_id:
                if assigned_to.branch_id and assigned_to.branch_id != user.branch_id:
                    raise PermissionDenied("Директор филиала может переназначать задачи только внутри филиала.")

        serializer.save()


