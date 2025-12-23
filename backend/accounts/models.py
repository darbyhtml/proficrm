from django.contrib.auth.models import AbstractUser
from django.db import models


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

# Create your models here.
