import hashlib
import secrets
from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone


class Branch(models.Model):
    code = models.SlugField("Код", max_length=50, unique=True)
    name = models.CharField("Название", max_length=120, unique=True)
    is_active = models.BooleanField("Активен", default=True, db_index=True)

    def __str__(self) -> str:
        return self.name

    def delete(self, *args, **kwargs):
        from django.core.exceptions import ValidationError

        if self.users.filter(is_active=True).exists():
            raise ValidationError(
                f"Нельзя удалить подразделение «{self.name}»: к нему привязаны активные сотрудники."
            )
        if self.companies.filter().exists():
            raise ValidationError(
                f"Нельзя удалить подразделение «{self.name}»: к нему привязаны компании."
            )
        super().delete(*args, **kwargs)


class User(AbstractUser):
    class Role(models.TextChoices):
        # NOTE: `sales_head` в коде = «РОП» в UI (см. docs/decisions.md 2026-04-15).
        MANAGER = "manager", "Менеджер"
        BRANCH_DIRECTOR = "branch_director", "Директор подразделения"
        SALES_HEAD = "sales_head", "РОП"
        GROUP_MANAGER = "group_manager", "Управляющий группой компаний"
        TENDERIST = "tenderist", "Тендерист"
        ADMIN = "admin", "Администратор"

    class DataScope(models.TextChoices):
        GLOBAL = "global", "Вся база"
        BRANCH = "branch", "Только подразделение"
        SELF = "self", "Только мои компании"

    role = models.CharField("Роль", max_length=32, choices=Role.choices, default=Role.MANAGER)

    # Онлайн-статус оператора в мессенджере (обновляется heartbeat-эндпоинтом)
    messenger_online = models.BooleanField(
        "Онлайн в мессенджере",
        default=False,
        db_index=True,
    )
    messenger_last_seen = models.DateTimeField(
        "Последняя активность в мессенджере",
        null=True,
        blank=True,
    )

    branch = models.ForeignKey(
        Branch,
        verbose_name="Подразделение",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="users",
    )

    # По умолчанию доступ "вся база", но админ может ограничить.
    data_scope = models.CharField(
        "Доступ к базе", max_length=16, choices=DataScope.choices, default=DataScope.GLOBAL
    )

    email_signature_html = models.TextField("Подпись в письме (HTML)", blank=True, default="")

    avatar = models.ImageField(
        "Фото профиля",
        upload_to="users/avatars/",
        null=True,
        blank=True,
    )

    def __str__(self) -> str:
        full = f"{self.last_name} {self.first_name}".strip()
        return full or self.get_username()

    @property
    def is_tenderist(self) -> bool:
        """Тендерист — read-only роль для тендерного отдела (см. decisions.md 2026-04-15)."""
        return self.role == self.Role.TENDERIST

    @property
    def is_admin_role(self) -> bool:
        """True для role=ADMIN или is_superuser. Использовать вместо is_staff в бизнес-логике."""
        return self.is_superuser or self.role == self.Role.ADMIN

    def is_currently_absent(self, on_date=None) -> bool:
        """True если на указанную дату (по умолчанию — сегодня) у сотрудника
        есть активное отсутствие (UserAbsence).

        Используется в messenger.services.auto_assign_conversation — не назначать
        диалоги отсутствующим сотрудникам, даже если их CRM-вкладка открыта.
        """
        from django.utils import timezone

        from accounts.models import UserAbsence

        today = on_date or timezone.localdate()
        # UserAbsence import здесь (не в топе) — модель определена ниже в этом файле.
        return UserAbsence.objects.filter(
            user_id=self.id,
            start_date__lte=today,
            end_date__gte=today,
        ).exists()


class MagicLinkToken(models.Model):
    """
    Одноразовый токен для входа в систему.
    Генерируется администратором для конкретного пользователя.
    """

    user = models.ForeignKey(
        User,
        verbose_name="Пользователь",
        on_delete=models.CASCADE,
        related_name="magic_link_tokens",
    )
    token_hash = models.CharField("Хэш токена", max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField("Создан", auto_now_add=True, db_index=True)
    expires_at = models.DateTimeField("Истекает", db_index=True)
    used_at = models.DateTimeField("Использован", null=True, blank=True, db_index=True)
    created_by = models.ForeignKey(
        User,
        verbose_name="Создан администратором",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_magic_links",
    )
    ip_address = models.GenericIPAddressField("IP адрес при использовании", null=True, blank=True)
    user_agent = models.CharField(
        "User-Agent при использовании", max_length=255, blank=True, default=""
    )

    class Meta:
        verbose_name = "Токен входа"
        verbose_name_plural = "Токены входа"
        indexes = [
            models.Index(fields=["user", "expires_at", "used_at"]),
        ]

    def __str__(self) -> str:
        status = (
            "использован"
            if self.used_at
            else ("истёк" if self.expires_at < timezone.now() else "активен")
        )
        return f"Токен для {self.user} ({status})"

    def is_valid(self) -> bool:
        """Проверка, что токен валиден (не истёк и не использован)."""
        if self.used_at is not None:
            return False
        return timezone.now() < self.expires_at

    def mark_as_used(self, ip_address: str | None = None, user_agent: str = "") -> None:
        """Пометить токен как использованный."""
        self.used_at = timezone.now()
        if ip_address:
            self.ip_address = ip_address
        if user_agent:
            self.user_agent = user_agent[:255]
        self.save(update_fields=["used_at", "ip_address", "user_agent"])

    @staticmethod
    def generate_token() -> tuple[str, str]:
        """
        Генерирует новый токен и его хэш.
        Возвращает (token, token_hash).
        Токен длиной 48 байт (64 символа в base64) для максимальной безопасности.
        """
        token = secrets.token_urlsafe(48)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash

    @staticmethod
    def create_for_user(
        user: User, created_by: User, ttl_minutes: int = 1440
    ) -> tuple["MagicLinkToken", str]:
        """
        Создаёт новый токен для пользователя.
        По умолчанию TTL = 24 часа (1440 минут).
        Возвращает (MagicLinkToken instance, plain_token).
        """
        token, token_hash = MagicLinkToken.generate_token()
        expires_at = timezone.now() + timedelta(minutes=ttl_minutes)
        magic_link = MagicLinkToken.objects.create(
            user=user,
            token_hash=token_hash,
            expires_at=expires_at,
            created_by=created_by,
        )
        return magic_link, token


# Справочник регионов подразделений (в отдельном файле чтобы не раздувать models.py)
from accounts.models_region import BranchRegion


class UserAbsence(models.Model):
    """Учёт отсутствия сотрудника (отпуск / больничный / отгул).

    F5: используется в messenger.services.auto_assign_conversation для
    исключения отсутствующих менеджеров из кандидатов на диалог.
    Ранее auto_assign мог назначить диалог на менеджера с открытой CRM-вкладкой,
    но фактически находящегося в отпуске/отгуле.

    Даты хранятся как DateField (не DateTime) — точность до дня достаточна:
    менеджер в отпуск уходит целиком на день, а не на полчаса.

    Для активного периода (end_date >= today) диалоги на пользователя
    назначаться не будут.
    """

    class Type(models.TextChoices):
        VACATION = "vacation", "Отпуск"
        SICK = "sick", "Больничный"
        DAYOFF = "dayoff", "Отгул"
        OTHER = "other", "Другое"

    user = models.ForeignKey(
        "accounts.User",
        on_delete=models.CASCADE,
        related_name="absences",
        verbose_name="Сотрудник",
    )
    start_date = models.DateField("Начало (включительно)")
    end_date = models.DateField(
        "Окончание (включительно)",
        help_text="День возвращения — последний день отсутствия, не первый день работы.",
    )
    type = models.CharField(
        "Тип",
        max_length=16,
        choices=Type.choices,
        default=Type.VACATION,
    )
    note = models.CharField(
        "Комментарий",
        max_length=255,
        blank=True,
        default="",
    )
    created_at = models.DateTimeField("Создано", auto_now_add=True)
    created_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_absences",
        verbose_name="Кто создал",
    )

    class Meta:
        verbose_name = "Отсутствие сотрудника"
        verbose_name_plural = "Отсутствия сотрудников"
        ordering = ["-start_date"]
        indexes = [
            # Для быстрой проверки is_currently_absent: фильтр по user + end_date
            models.Index(
                fields=["user", "end_date"],
                name="user_absence_user_end_idx",
            ),
        ]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="user_absence_end_after_start",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.get_type_display()} {self.start_date}..{self.end_date}"

    def is_active_on(self, date) -> bool:
        """Проверяет, покрывает ли период указанную дату."""
        return self.start_date <= date <= self.end_date
