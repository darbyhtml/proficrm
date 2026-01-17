from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from datetime import timedelta
import hashlib
import secrets


class Branch(models.Model):
    code = models.SlugField("Код", max_length=50, unique=True)
    name = models.CharField("Название", max_length=120, unique=True)

    def __str__(self) -> str:
        return self.name


class User(AbstractUser):
    class Role(models.TextChoices):
        MANAGER = "manager", "Менеджер"
        BRANCH_DIRECTOR = "branch_director", "Директор филиала"
        SALES_HEAD = "sales_head", "Руководитель отдела продаж"
        GROUP_MANAGER = "group_manager", "Управляющий группой компаний"
        ADMIN = "admin", "Администратор"

    class DataScope(models.TextChoices):
        GLOBAL = "global", "Вся база"
        BRANCH = "branch", "Только филиал"
        SELF = "self", "Только мои компании"

    role = models.CharField("Роль", max_length=32, choices=Role.choices, default=Role.MANAGER)
    branch = models.ForeignKey(Branch, verbose_name="Филиал", null=True, blank=True, on_delete=models.SET_NULL, related_name="users")

    # По умолчанию доступ "вся база", но админ может ограничить.
    data_scope = models.CharField("Доступ к базе", max_length=16, choices=DataScope.choices, default=DataScope.GLOBAL)

    email_signature_html = models.TextField("Подпись в письме (HTML)", blank=True, default="")

    def __str__(self) -> str:
        full = f"{self.last_name} {self.first_name}".strip()
        return full or self.get_username()


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
    user_agent = models.CharField("User-Agent при использовании", max_length=255, blank=True, default="")

    class Meta:
        verbose_name = "Токен входа"
        verbose_name_plural = "Токены входа"
        indexes = [
            models.Index(fields=["token_hash"]),
            models.Index(fields=["user", "expires_at", "used_at"]),
        ]

    def __str__(self) -> str:
        status = "использован" if self.used_at else ("истёк" if self.expires_at < timezone.now() else "активен")
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
        """
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        return token, token_hash

    @staticmethod
    def create_for_user(user: User, created_by: User, ttl_minutes: int = 30) -> tuple["MagicLinkToken", str]:
        """
        Создаёт новый токен для пользователя.
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
