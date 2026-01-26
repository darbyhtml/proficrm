from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend
from django.db.models import Q

from accounts.models import User
from companies.models import Company
from companies.permissions import can_edit_company
from .models import Task, TaskType
from policy.drf import PolicyPermission


def _can_manage_task_status_api(user: User, task: Task) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser or user.role in (User.Role.ADMIN, User.Role.GROUP_MANAGER):
        return True
    # Создатель всегда может менять статус своей задачи (проверяем ПЕРВЫМ)
    if task.created_by_id and task.created_by_id == user.id:
        return True
    # Исполнитель может менять статус назначенной ему задачи
    if task.assigned_to_id and task.assigned_to_id == user.id:
        return True
    # По ТЗ: менеджер управляет статусом только своих задач (создатель или исполнитель).
    # Проверка создателя и исполнителя уже выполнена выше, поэтому здесь просто блокируем доступ
    # для менеджеров к чужим задачам (если они не создатель и не исполнитель)
    if user.role == User.Role.MANAGER:
        return False
    if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
        # По задачам работаем по "филиалу компании" (карточка) в первую очередь.
        branch_id = None
        if getattr(task, "company_id", None) and getattr(task, "company", None):
            branch_id = getattr(task.company, "branch_id", None)
        if not branch_id and getattr(task, "assigned_to", None):
            branch_id = getattr(task.assigned_to, "branch_id", None)
        return bool(branch_id and branch_id == user.branch_id)
    return False


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
    permission_classes = [IsAuthenticated, PolicyPermission]
    policy_resource_prefix = "api:tasks"
    filter_backends = (DjangoFilterBackend, SearchFilter, OrderingFilter)
    filterset_fields = ("status", "assigned_to", "company", "type")
    search_fields = ("title", "description", "company__name")
    ordering_fields = ("created_at", "due_at")

    def get_queryset(self):
        """
        Важно: видимость задач в API должна совпадать с Web UI.
        Иначе возможна утечка задач (например, менеджер увидит чужие через /api/tasks/).
        """
        user: User = getattr(self.request, "user", None)
        qs = Task.objects.select_related("company", "assigned_to", "created_by").order_by("-created_at")

        if not user or not user.is_authenticated or not user.is_active:
            return qs.none()

        # По ТЗ/логике UI:
        # - менеджер: только свои задачи (исполнитель)
        # - директор/РОП: задачи своего филиала + свои
        # - админ/управляющий: все
        if user.role == User.Role.MANAGER:
            qs = qs.filter(assigned_to=user)
        elif user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
            qs = qs.filter(
                Q(assigned_to__branch_id=user.branch_id)
                | Q(company__branch_id=user.branch_id)
                | Q(assigned_to=user)
            )

        return qs.distinct()

    def perform_create(self, serializer):
        user: User = self.request.user
        data = dict(serializer.validated_data)
        assigned_to = data.get("assigned_to") or user
        company: Company | None = data.get("company")

        if company is not None and not can_edit_company(user, company):
            raise PermissionDenied("Нет прав на постановку задач по этой компании.")

        if user.role == User.Role.MANAGER and assigned_to.id != user.id:
            raise PermissionDenied("Менеджер может назначать задачи только себе.")

        if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
            if assigned_to.branch_id and assigned_to.branch_id != user.branch_id:
                raise PermissionDenied("Можно назначать задачи только сотрудникам своего филиала.")

        serializer.save(created_by=user, assigned_to=assigned_to)

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: Task = self.get_object()
        data = dict(serializer.validated_data)

        # Доступ: исполнитель, либо руководители (по филиалу), либо админ/управляющий.
        if not _can_manage_task_status_api(user, obj):
            raise PermissionDenied("Нет прав на изменение задачи.")

        if "assigned_to" in data:
            assigned_to = data["assigned_to"]

            if user.role == User.Role.MANAGER and assigned_to.id != user.id:
                raise PermissionDenied("Менеджер не может переназначать задачи другим.")

            if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
                if assigned_to.branch_id and assigned_to.branch_id != user.branch_id:
                    raise PermissionDenied("Можно переназначать задачи только внутри филиала.")

        serializer.save()


