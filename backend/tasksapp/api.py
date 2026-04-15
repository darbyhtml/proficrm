from datetime import timedelta

from django.utils import timezone
from rest_framework import serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import IsAuthenticated
from rest_framework.filters import SearchFilter, OrderingFilter
from django_filters.rest_framework import DjangoFilterBackend

from accounts.models import User
from companies.models import Company
from companies.permissions import can_edit_company
from .models import Task, TaskType
from .policy import visible_tasks_qs, can_manage_task_status
from companies.services import resolve_target_companies
from policy.drf import PolicyPermission


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
            "apply_to_org_branches",
        ]
        read_only_fields = ["created_by", "created_at", "completed_at"]

    apply_to_org_branches = serializers.BooleanField(
        required=False,
        default=False,
        write_only=True,
        help_text="Если включено и у компании есть организация (головная/филиалы), задача будет создана по всей организации.",
    )
    
    def validate_recurrence_rrule(self, value):
        """Валидация строки RRULE (iCalendar RFC 5545).

        Допустимые значения:
          ""                          — без повтора
          "FREQ=DAILY"                — каждый день
          "FREQ=WEEKLY;BYDAY=MO,FR"   — пн и пт каждую неделю
          "FREQ=MONTHLY;BYMONTHDAY=1" — первого числа каждого месяца

        Поддерживаемые FREQ: DAILY, WEEKLY, MONTHLY, YEARLY.
        Опциональные параметры: INTERVAL, BYDAY, BYMONTHDAY, UNTIL, COUNT.
        """
        value = (value or "").strip()
        if not value:
            return ""

        value_upper = value.upper()

        if not value_upper.startswith("FREQ="):
            raise serializers.ValidationError(
                "RRULE должна начинаться с FREQ=. "
                "Пример: FREQ=DAILY или FREQ=WEEKLY;BYDAY=MO,WE,FR"
            )

        valid_freqs = ("DAILY", "WEEKLY", "MONTHLY", "YEARLY")
        freq_part = value_upper.split(";")[0].replace("FREQ=", "")
        if freq_part not in valid_freqs:
            raise serializers.ValidationError(
                f"Недопустимое значение FREQ={freq_part}. "
                f"Допустимые: {', '.join(valid_freqs)}"
            )

        # Защита от DoS: ограничиваем COUNT и проверяем, что правило вообще
        # парсится (rrulestr бросит исключение на мусоре).
        parts = {p.split("=", 1)[0]: p.split("=", 1)[1] for p in value_upper.split(";") if "=" in p}
        try:
            count_val = int(parts.get("COUNT", "0") or "0")
        except ValueError:
            raise serializers.ValidationError("COUNT должен быть целым числом.")
        if count_val and count_val > 1000:
            raise serializers.ValidationError("COUNT не может превышать 1000.")
        try:
            interval_val = int(parts.get("INTERVAL", "1") or "1")
        except ValueError:
            raise serializers.ValidationError("INTERVAL должен быть целым числом.")
        if interval_val < 1 or interval_val > 366:
            raise serializers.ValidationError("INTERVAL должен быть в диапазоне 1..366.")

        try:
            from dateutil.rrule import rrulestr
            from django.utils import timezone as _tz
            rrulestr(value, dtstart=_tz.now())
        except Exception as exc:
            raise serializers.ValidationError(f"Некорректное правило RRULE: {exc}")

        return value


class TaskTypeViewSet(viewsets.ModelViewSet):
    serializer_class = TaskTypeSerializer
    queryset = TaskType.objects.all().order_by("name")
    search_fields = ("name",)
    permission_classes = [IsAuthenticated, PolicyPermission]
    policy_resource_prefix = "api:task_types"


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
        return visible_tasks_qs(user)

    def perform_create(self, serializer):
        user: User = self.request.user
        # ВАЖНО: сначала читаем флаг, потом удаляем из validated_data,
        # иначе DRF попробует передать его в Task.objects.create()
        apply_to_org = bool(serializer.validated_data.pop("apply_to_org_branches", False))
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

        # Если указан apply_to_org_branches и есть компания — создаём задачи по всем целевым компаниям.
        if apply_to_org and company is not None:
            target_companies = resolve_target_companies(
                selected_company=company,
                apply_to_org_branches=True,
            )

            seen_ids: set = set()
            created_tasks: list[Task] = []

            for c in target_companies:
                if not c or c.id in seen_ids:
                    continue
                seen_ids.add(c.id)

                if not can_edit_company(user, c):
                    continue

                # Защита от случайного дублирования задач при повторном запросе:
                # если за последние несколько секунд уже есть такая же задача по этой компании,
                # просто возвращаемся к существующей.
                title_value = data.get("title") or (data.get("type").name if data.get("type") else "")
                recent_cutoff = timezone.now() - timedelta(seconds=10)
                duplicate_qs = Task.objects.filter(
                    created_by=user,
                    assigned_to=assigned_to,
                    company=c,
                    type=data.get("type"),
                    title=title_value,
                    due_at=data.get("due_at"),
                    recurrence_rrule=data.get("recurrence_rrule") or "",
                    created_at__gte=recent_cutoff,
                ).order_by("-created_at")
                existing_task = duplicate_qs.first()
                if existing_task:
                    created_tasks.append(existing_task)
                    continue

                task = Task.objects.create(
                    created_by=user,
                    assigned_to=assigned_to,
                    company=c,
                    type=data.get("type"),
                    title=title_value,
                    description=data.get("description", ""),
                    status=data.get("status") or Task.Status.NEW,
                    due_at=data.get("due_at"),
                    recurrence_rrule=data.get("recurrence_rrule") or "",
                )
                created_tasks.append(task)

            if not created_tasks:
                raise PermissionDenied("Не удалось создать задачи по организации (нет прав ни по одной компании).")

            # Для DRF важно вернуть один объект — берём первую созданную задачу как "представителя".
            serializer.instance = created_tasks[0]
            return

        # Обычное создание одной задачи
        # Защита от дублирования: если за последние несколько секунд уже есть такая же задача,
        # считаем запрос идемпотентным и возвращаем существующую.
        title_value = data.get("title") or (data.get("type").name if data.get("type") else "")
        recent_cutoff = timezone.now() - timedelta(seconds=10)
        duplicate_qs = Task.objects.filter(
            created_by=user,
            assigned_to=assigned_to,
            company=company,
            type=data.get("type"),
            title=title_value,
            due_at=data.get("due_at"),
            recurrence_rrule=data.get("recurrence_rrule") or "",
            created_at__gte=recent_cutoff,
        ).order_by("-created_at")
        existing_task = duplicate_qs.first()
        if existing_task:
            serializer.instance = existing_task
            return

        serializer.save(created_by=user, assigned_to=assigned_to)

    def perform_update(self, serializer):
        user: User = self.request.user
        obj: Task = self.get_object()
        data = dict(serializer.validated_data)

        # Доступ: исполнитель/создатель/руководители (по филиалу) или админ/управляющий.
        if not can_manage_task_status(user, obj):
            raise PermissionDenied("Нет прав на изменение задачи.")

        if "assigned_to" in data:
            assigned_to = data["assigned_to"]

            if user.role == User.Role.MANAGER and assigned_to.id != user.id:
                raise PermissionDenied("Менеджер не может переназначать задачи другим.")

            if user.role in (User.Role.BRANCH_DIRECTOR, User.Role.SALES_HEAD) and user.branch_id:
                if assigned_to.branch_id and assigned_to.branch_id != user.branch_id:
                    raise PermissionDenied("Можно переназначать задачи только внутри филиала.")

        serializer.save()


