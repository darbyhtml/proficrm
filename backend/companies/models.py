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

    class ContractType(models.TextChoices):
        FRAME = "frame", "Рамочный"
        TENDER = "tender", "Тендер"
        LEGAL = "legal", "Юр. лицо"
        INDIVIDUAL = "individual", "Физ. лицо"

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
    activity_kind = models.CharField("Вид деятельности", max_length=255, blank=True, default="", db_index=True)
    class LeadState(models.TextChoices):
        COLD = "cold", "Холодный контакт"
        WARM = "warm", "Теплый контакт"

    lead_state = models.CharField(
        "Состояние контакта",
        max_length=8,
        choices=LeadState.choices,
        default=LeadState.WARM,
        db_index=True,
        help_text="Холодный/тёплый контакт (влияет на доступность отметок «холодный звонок»).",
    )
    # Устаревшее: раньше отметка была на всю компанию. Оставляем поле для обратной совместимости/данных,
    # но в UI/логике используем отметки на контактах.
    is_cold_call = models.BooleanField("Холодный звонок (устар.)", default=False, db_index=True)
    primary_contact_is_cold_call = models.BooleanField("Холодный звонок (основной контакт)", default=False, db_index=True)
    primary_cold_marked_at = models.DateTimeField("Холодный (осн. контакт): когда отметили", null=True, blank=True, db_index=True)
    primary_cold_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Холодный (осн. контакт): кто отметил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_cold_marks",
    )
    primary_cold_marked_call = models.ForeignKey(
        "phonebridge.CallRequest",
        verbose_name="Холодный (осн. контакт): звонок",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    contract_type = models.CharField(
        "Вид договора",
        max_length=16,
        choices=ContractType.choices,
        blank=True,
        default="",
        db_index=True,
    )
    contract_until = models.DateField("Действует до", null=True, blank=True, db_index=True)

    head_company = models.ForeignKey(
        "self",
        verbose_name="Головная организация",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="client_branches",
        help_text="Если эта карточка — филиал/подразделение клиента, выберите головную организацию.",
    )

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
        # Защита от длинных значений (особенно важно при импорте из amoCRM)
        # ВАЖНО: обрезаем ВСЕГДА, даже если значение уже установлено (защита от любых источников данных)
        if self.inn:
            self.inn = str(self.inn).strip()[:20]
        if self.kpp:
            self.kpp = str(self.kpp).strip()[:20]
        if self.legal_name:
            self.legal_name = str(self.legal_name).strip()[:255]
        if self.address:
            self.address = str(self.address).strip()[:500]
        if self.website:
            self.website = str(self.website).strip()[:255]
        if self.contact_name:
            self.contact_name = str(self.contact_name).strip()[:255]
        if self.contact_position:
            self.contact_position = str(self.contact_position).strip()[:255]
        if self.activity_kind:
            self.activity_kind = str(self.activity_kind).strip()[:255]
        if self.name:
            self.name = str(self.name).strip()[:255]
        if self.phone:
            # Нормализуем номер телефона: убираем форматирование, оставляем только цифры и +7
            phone = str(self.phone).strip()
            # Убираем все нецифровые символы, кроме + в начале
            digits = ''.join(c for c in phone if c.isdigit() or (c == '+' and phone.startswith('+')))
            # Если начинается с +7, оставляем как есть
            if digits.startswith('+7') and len(digits) == 12:
                self.phone = digits[:50]
            # Если начинается с 8 и 11 цифр, заменяем на +7
            elif digits.startswith('8') and len(digits) == 11:
                self.phone = '+7' + digits[1:][:50]
            # Если начинается с 7 и 11 цифр, добавляем +
            elif digits.startswith('7') and len(digits) == 11:
                self.phone = '+' + digits[:50]
            # Если 10 цифр, добавляем +7
            elif len(digits) == 10:
                self.phone = '+7' + digits[:50]
            # Иначе обрезаем до 50 символов
            else:
                self.phone = phone[:50]
        if self.email:
            self.email = str(self.email).strip()[:254]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class CompanyNote(models.Model):
    company = models.ForeignKey(Company, verbose_name="Компания", on_delete=models.CASCADE, related_name="notes")
    author = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name="Автор", null=True, on_delete=models.SET_NULL, related_name="company_notes")
    text = models.TextField("Текст")
    attachment = models.FileField("Файл (вложение)", upload_to="company_notes/%Y/%m/%d/", null=True, blank=True)
    attachment_name = models.CharField("Имя файла", max_length=255, blank=True, default="")
    attachment_ext = models.CharField("Расширение", max_length=16, blank=True, default="", db_index=True)
    attachment_size = models.BigIntegerField("Размер (байт)", default=0)
    attachment_content_type = models.CharField("MIME тип", max_length=120, blank=True, default="")
    is_pinned = models.BooleanField("Закреплено", default=False, db_index=True)
    pinned_at = models.DateTimeField("Когда закрепили", null=True, blank=True)
    pinned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто закрепил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="pinned_company_notes",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    edited_at = models.DateTimeField("Редактировано", null=True, blank=True, db_index=True)
    external_source = models.CharField("Внешний источник", max_length=32, blank=True, default="", db_index=True)
    external_uid = models.CharField("Внешний UID", max_length=120, blank=True, default="", db_index=True)

    def __str__(self) -> str:
        return f"Note({self.company_id})"

    def save(self, *args, **kwargs):
        # Снэпшоты метаданных файла (если не заданы)
        try:
            if self.attachment and not self.attachment_name:
                self.attachment_name = (getattr(self.attachment, "name", "") or "").split("/")[-1].split("\\")[-1]
            if self.attachment and not self.attachment_ext:
                self.attachment_ext = _safe_ext(self.attachment_name or getattr(self.attachment, "name", ""))
            if self.attachment and not self.attachment_size:
                self.attachment_size = int(getattr(self.attachment, "size", 0) or 0)
        except Exception:
            pass
        super().save(*args, **kwargs)


class Contact(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, verbose_name="Компания", null=True, blank=True, on_delete=models.SET_NULL, related_name="contacts")

    first_name = models.CharField("Имя", max_length=120, blank=True, default="")
    last_name = models.CharField("Фамилия", max_length=120, blank=True, default="")
    position = models.CharField("Должность", max_length=255, blank=True, default="")

    status = models.CharField("Статус", max_length=120, blank=True, default="")
    note = models.TextField("Примечание", blank=True, default="")
    is_cold_call = models.BooleanField("Холодный звонок", default=False, db_index=True)
    cold_marked_at = models.DateTimeField("Холодный: когда отметили", null=True, blank=True, db_index=True)
    cold_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Холодный: кто отметил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="contact_cold_marks",
    )
    cold_marked_call = models.ForeignKey(
        "phonebridge.CallRequest",
        verbose_name="Холодный: звонок",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
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

    def save(self, *args, **kwargs):
        if self.value:
            # Нормализуем номер телефона: убираем форматирование, оставляем только цифры и +7
            phone = str(self.value).strip()
            # Убираем все нецифровые символы, кроме + в начале
            digits = ''.join(c for c in phone if c.isdigit() or (c == '+' and phone.startswith('+')))
            # Если начинается с +7, оставляем как есть
            if digits.startswith('+7') and len(digits) == 12:
                self.value = digits[:50]
            # Если начинается с 8 и 11 цифр, заменяем на +7
            elif digits.startswith('8') and len(digits) == 11:
                self.value = '+7' + digits[1:][:50]
            # Если начинается с 7 и 11 цифр, добавляем +
            elif digits.startswith('7') and len(digits) == 11:
                self.value = '+' + digits[:50]
            # Если 10 цифр, добавляем +7
            elif len(digits) == 10:
                self.value = '+7' + digits[:50]
            # Иначе обрезаем до 50 символов
            else:
                self.value = phone[:50]
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.value


class CompanyLeadStateRequest(models.Model):
    """
    Запрос менеджера на смену состояния карточки (холодная/тёплая).
    Подтверждается РОП или директором филиала; после решения у второго уведомление должно исчезнуть.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        APPROVED = "approved", "Подтверждено"
        CANCELLED = "cancelled", "Отклонено"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name="lead_state_requests", verbose_name="Компания")
    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="lead_state_requests",
        verbose_name="Кто запросил",
    )
    requested_state = models.CharField("Запрошенное состояние", max_length=8, choices=Company.LeadState.choices)
    note = models.TextField("Примечание", blank=True, default="")
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)
    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="lead_state_decisions",
        verbose_name="Кто решил",
    )
    decision_note = models.TextField("Комментарий решения", blank=True, default="")
    decided_at = models.DateTimeField("Когда решили", null=True, blank=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["company", "status", "created_at"]),
            models.Index(fields=["status", "created_at"]),
        ]

class CompanyDeletionRequest(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Ожидает решения"
        CANCELLED = "cancelled", "Отклонено"
        APPROVED = "approved", "Подтверждено"

    company = models.ForeignKey(
        Company,
        verbose_name="Компания",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="deletion_requests",
    )
    company_id_snapshot = models.UUIDField("ID компании (снимок)", db_index=True)
    company_name_snapshot = models.CharField("Название компании (снимок)", max_length=255, blank=True, default="")

    requested_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто запросил",
        null=True,
        on_delete=models.SET_NULL,
        related_name="company_delete_requests",
    )
    requested_by_branch = models.ForeignKey(
        "accounts.Branch",
        verbose_name="Филиал автора (снимок)",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    note = models.TextField("Примечание (почему удалить)", blank=True, default="")
    status = models.CharField("Статус", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True)

    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name="Кто решил",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="company_delete_decisions",
    )
    decision_note = models.TextField("Комментарий решения", blank=True, default="")
    decided_at = models.DateTimeField("Когда решили", null=True, blank=True)

    created_at = models.DateTimeField("Создано", auto_now_add=True, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=["company_id_snapshot", "status"]),
            models.Index(fields=["status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"DeleteRequest({self.company_id_snapshot}) {self.status}"

