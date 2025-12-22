import uuid

from django.conf import settings
from django.db import models


def _safe_ext(name: str) -> str:
    n = (name or "").strip().lower()
    if "." not in n:
        return ""
    ext = n.rsplit(".", 1)[-1]
    # sanity: keep only short extensions
    return ext[:16]


class CompanyStatus(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)

    def __str__(self) -> str:
        return self.name


class CompanySphere(models.Model):
    name = models.CharField("Название", max_length=120, unique=True)

    def __str__(self) -> str:
        return self.name


class Company(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Создатель",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="created_companies",
    )

    name = models.CharField("Название", max_length=255, db_index=True)
    legal_name = models.CharField("Юр. название", max_length=255, blank=True, default="")
    inn = models.CharField("ИНН", max_length=20, blank=True, default="", db_index=True)
    kpp = models.CharField("КПП", max_length=20, blank=True, default="")
    address = models.CharField("Адрес", max_length=500, blank=True, default="")
    website = models.CharField("Сайт", max_length=255, blank=True, default="")

    phone = models.CharField("Телефон (основной)", max_length=50, blank=True, default="", db_index=True)
    email = models.EmailField("Email (основной)", max_length=254, blank=True, default="", db_index=True)
    contact_name = models.CharField("Контакт (ФИО)", max_length=255, blank=True, default="")
    contact_position = models.CharField("Контакт (должность)", max_length=255, blank=True, default="")

    status = models.ForeignKey(CompanyStatus, verbose_name="Статус", null=True, blank=True, on_delete=models.SET_NULL, related_name="companies")
    spheres = models.ManyToManyField(CompanySphere, verbose_name="Сферы", blank=True, related_name="companies")

    responsible = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Ответственный", null=True, blank=True, on_delete=models.SET_NULL, related_name="companies")
    branch = models.ForeignKey("accounts.Branch", verbose_name="Филиал", null=True, blank=True, on_delete=models.SET_NULL, related_name="companies")

    amocrm_company_id = models.BigIntegerField("ID компании (amo)", null=True, blank=True, db_index=True)

    raw_fields = models.JSONField("Сырые поля (импорт)", default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["inn"]),
            models.Index(fields=["name"]),
        ]

    def save(self, *args, **kwargs):
        if self.branch_id is None and self.responsible_id is not None:
            # Филиал компании по умолчанию = филиалу ответственного (если не задан явно).
            self.branch = getattr(self.responsible, "branch", None)
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class CompanyNote(models.Model):
    company = models.ForeignKey(Company, verbose_name="Компания", on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Автор", null=True, on_delete=models.SET_NULL, related_name="company_notes")
    text = models.TextField("Текст")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"Note({self.company_id})"


class CompanyDocument(models.Model):
    """
    Документы компании (файлы: pdf/doc/xls/изображения и т.п.).
    Храним в MEDIA_ROOT, показываем в карточке компании.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, verbose_name="Компания", on_delete=models.CASCADE, related_name="documents")

    title = models.CharField("Название", max_length=255, blank=True, default="")
    file = models.FileField("Файл", upload_to="company_docs/%Y/%m/%d/")

    original_name = models.CharField("Оригинальное имя файла", max_length=255, blank=True, default="")
    ext = models.CharField("Расширение", max_length=16, blank=True, default="", db_index=True)
    size = models.BigIntegerField("Размер (байт)", default=0)
    content_type = models.CharField("MIME тип", max_length=120, blank=True, default="")

    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто загрузил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="uploaded_company_documents",
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "created_at"], name="companydoc_company_created_idx"),
        ]
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Снэпшоты метаданных файла (если не заданы)
        try:
            if self.file and not self.original_name:
                self.original_name = (getattr(self.file, "name", "") or "").split("/")[-1].split("\\")[-1]
            if self.file and not self.ext:
                self.ext = _safe_ext(self.original_name or getattr(self.file, "name", ""))
            if self.file and not self.size:
                self.size = int(getattr(self.file, "size", 0) or 0)
        except Exception:
            # не ломаем сохранение из-за метаданных
            pass
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        label = self.title or self.original_name or str(self.id)
        return f"{label} ({self.company_id})"


class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, verbose_name="Компания", null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts")

    first_name = models.CharField("Имя", max_length=120, blank=True, default="")
    last_name = models.CharField("Фамилия", max_length=120, blank=True, default="")
    position = models.CharField("Должность", max_length=255, blank=True, default="")

    status = models.CharField("Статус", max_length=120, blank=True, default="")
    note = models.TextField("Примечание", blank=True, default="")

    amocrm_contact_id = models.BigIntegerField("ID контакта (amo)", null=True, blank=True, db_index=True)

    raw_fields = models.JSONField("Сырые поля (импорт)", default=dict, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"{self.last_name} {self.first_name}".strip() or str(self.id)


class ContactEmail(models.Model):
    class EmailType(models.TextChoices):
        WORK = "work", "Рабочий"
        PERSONAL = "personal", "Личный"
        OTHER = "other", "Другой"

    contact = models.ForeignKey(Contact, verbose_name="Контакт", on_delete=models.CASCADE, related_name="emails")
    type = models.CharField(max_length=16, choices=EmailType.choices, default=EmailType.WORK)
    value = models.EmailField("Email", max_length=254, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["value"])]

    def __str__(self) -> str:
        return self.value


class ContactPhone(models.Model):
    class PhoneType(models.TextChoices):
        WORK = "work", "Рабочий"
        WORK_DIRECT = "work_direct", "Рабочий прямой"
        MOBILE = "mobile", "Мобильный"
        OTHER = "other", "Другой"
        HOME = "home", "Домашний"
        FAX = "fax", "Факс"

    contact = models.ForeignKey(Contact, verbose_name="Контакт", on_delete=models.CASCADE, related_name="phones")
    type = models.CharField(max_length=24, choices=PhoneType.choices, default=PhoneType.WORK)
    value = models.CharField("Телефон", max_length=50, db_index=True)

    class Meta:
        indexes = [models.Index(fields=["value"])]

    def __str__(self) -> str:
        return self.value

# Create your models here.
