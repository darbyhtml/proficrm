import mimetypes
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet, ValidationError
from django.contrib.auth.password_validation import validate_password

from accounts.models import Branch, User
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact, ContactEmail, ContactPhone
from tasksapp.models import Task, TaskType
from ui.models import UiGlobalConfig


class CompanyCreateForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = [
            "name",
            "legal_name",
            "inn",
            "kpp",
            "address",
            "website",
            "lead_state",
            "activity_kind",
            "contract_type",
            "contract_until",
            "head_company",
            "phone",
            "email",
            "contact_name",
            "contact_position",
            "status",
            "spheres",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "legal_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "inn": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "lead_state": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "activity_kind": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "Напр.: строительство, услуги, производство…"}),
            "contract_type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "contract_until": forms.DateInput(attrs={"type": "date", "class": "w-full rounded-lg border px-3 py-2"}),
            "head_company": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "phone": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "+7 ..."}),
            "email": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "email@example.com"}),
            "contact_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "ФИО"}),
            "contact_position": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "Должность"}),
            "status": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "spheres": forms.SelectMultiple(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }

class CompanyQuickEditForm(forms.ModelForm):
    class Meta:
        model = Company
        fields = ["status", "spheres"]
        widgets = {
            "status": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "spheres": forms.SelectMultiple(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyEditForm(forms.ModelForm):
    """
    Полное редактирование данных компании (без смены ответственного/филиала).
    Статус/сферы здесь тоже доступны, чтобы редактирование было "в одном месте".
    """
    class Meta:
        model = Company
        fields = [
            "name",
            "legal_name",
            "inn",
            "kpp",
            "address",
            "website",
            "activity_kind",
            "contract_type",
            "contract_until",
            "head_company",
            "phone",
            "email",
            "contact_name",
            "contact_position",
            "status",
            "spheres",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "legal_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "inn": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "activity_kind": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "Напр.: строительство, услуги, производство…"}),
            "contract_type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "contract_until": forms.DateInput(attrs={"type": "date", "class": "w-full rounded-lg border px-3 py-2"}),
            "head_company": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "phone": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "+7 ..."}),
            "email": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "email@example.com"}),
            "contact_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "ФИО"}),
            "contact_position": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "Должность"}),
            "status": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "spheres": forms.SelectMultiple(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyContractForm(forms.ModelForm):
    """
    Мини-форма для редактирования договора прямо из карточки компании.
    """

    class Meta:
        model = Company
        fields = ["contract_type", "contract_until"]
        widgets = {
            "contract_type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "contract_until": forms.DateInput(attrs={"type": "date", "class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyNoteForm(forms.ModelForm):
    class Meta:
        model = CompanyNote
        fields = ["text", "attachment"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 4, "placeholder": "Заметка/комментарий...", "class": "w-full rounded-lg border px-3 py-2"}),
        }

    MAX_SIZE = 15 * 1024 * 1024  # 15 MB
    ALLOWED_EXT = {
        "pdf",
        "doc", "docx",
        "xls", "xlsx",
        "ppt", "pptx",
        "png", "jpg", "jpeg", "gif", "webp",
        "txt", "csv",
    }
    
    # Соответствие расширений и MIME типов для дополнительной проверки
    ALLOWED_MIME_TYPES = {
        "pdf": ["application/pdf"],
        "doc": ["application/msword"],
        "docx": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
        "xls": ["application/vnd.ms-excel"],
        "xlsx": ["application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"],
        "ppt": ["application/vnd.ms-powerpoint"],
        "pptx": ["application/vnd.openxmlformats-officedocument.presentationml.presentation"],
        "png": ["image/png"],
        "jpg": ["image/jpeg"],
        "jpeg": ["image/jpeg"],
        "gif": ["image/gif"],
        "webp": ["image/webp"],
        "txt": ["text/plain"],
        "csv": ["text/csv", "text/plain", "application/csv"],
    }

    def clean(self):
        cleaned = super().clean()
        text = (cleaned.get("text") or "").strip()
        f = cleaned.get("attachment")

        if not text and not f:
            raise ValidationError("Нужно написать заметку или прикрепить файл.")

        if f:
            size = int(getattr(f, "size", 0) or 0)
            if size <= 0:
                raise ValidationError("Пустой файл.")
            if size > self.MAX_SIZE:
                raise ValidationError("Слишком большой файл. Максимум 15 МБ.")
            
            name = (getattr(f, "name", "") or "").strip().lower()
            ext = name.rsplit(".", 1)[-1] if "." in name else ""
            
            if ext and ext not in self.ALLOWED_EXT:
                raise ValidationError("Формат файла не поддерживается. Разрешены: PDF, DOC/DOCX, XLS/XLSX, PPT/PPTX, изображения, TXT/CSV.")
            
            # Дополнительная проверка MIME типа
            if ext:
                # Читаем первые байты для определения реального типа файла
                file_content = f.read(1024)  # Читаем первые 1024 байта
                f.seek(0)  # Возвращаемся в начало файла
                
                # Определяем MIME тип по содержимому
                detected_mime = None
                if file_content.startswith(b'%PDF'):
                    detected_mime = 'application/pdf'
                elif file_content.startswith(b'\x89PNG'):
                    detected_mime = 'image/png'
                elif file_content.startswith(b'\xff\xd8\xff'):
                    detected_mime = 'image/jpeg'
                elif file_content.startswith(b'GIF'):
                    detected_mime = 'image/gif'
                elif file_content.startswith(b'RIFF') and b'WEBP' in file_content[:20]:
                    detected_mime = 'image/webp'
                else:
                    # Используем стандартное определение MIME
                    detected_mime = mimetypes.guess_type(name)[0]
                
                # Проверяем соответствие MIME типа расширению
                allowed_mimes = self.ALLOWED_MIME_TYPES.get(ext, [])
                if detected_mime and allowed_mimes and detected_mime not in allowed_mimes:
                    raise ValidationError(f"Тип файла не соответствует расширению. Ожидался {ext}, обнаружен {detected_mime}.")

        return cleaned


class TaskForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "w-full rounded-lg border px-3 py-2", "required": "required"}),
        label="Дедлайн",
    )
    apply_to_org_branches = forms.BooleanField(
        required=False,
        initial=False,
        label="Применить ко всем филиалам организации",
        help_text="Если у компании есть филиалы клиента (или она сама филиал) — задача будет создана на все карточки этой организации.",
    )

    class Meta:
        model = Task
        # Заголовок теперь не вводится руками — он берётся из выбранного типа/статуса.
        fields = ["description", "company", "type", "assigned_to", "due_at", "recurrence_rrule"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "class": "w-full rounded-lg border px-3 py-2"}),
            "company": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            # type фактически используется как «Статус задачи»
            "type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "assigned_to": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "recurrence_rrule": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class TaskEditForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        required=True,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "w-full rounded-lg border px-3 py-2", "required": "required"}),
        label="Дедлайн",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from django.utils import timezone
        from datetime import timedelta
        
        # Форматируем дату для datetime-local input
        if self.instance and self.instance.due_at:
            local_dt = timezone.localtime(self.instance.due_at)
            self.initial['due_at'] = local_dt.strftime('%Y-%m-%dT%H:%M')
        elif self.instance and not self.instance.due_at:
            # Если у задачи нет дедлайна, устанавливаем дефолтный (завтра в 18:00)
            local_now = timezone.localtime(timezone.now())
            default_due = local_now + timedelta(days=1)
            default_due = default_due.replace(hour=18, minute=0, second=0, microsecond=0)
            self.initial['due_at'] = default_due.strftime('%Y-%m-%dT%H:%M')

    class Meta:
        model = Task
        # Заголовок не редактируется вручную, берётся из выбранного типа/статуса.
        fields = ["description", "type", "due_at"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "class": "w-full rounded-lg border px-3 py-2"}),
            "type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }

class BranchForm(forms.ModelForm):
    class Meta:
        model = Branch
        fields = ["code", "name"]
        widgets = {
            "code": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyStatusForm(forms.ModelForm):
    class Meta:
        model = CompanyStatus
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"})}


class CompanySphereForm(forms.ModelForm):
    class Meta:
        model = CompanySphere
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"})}


class TaskTypeForm(forms.ModelForm):
    ICON_CHOICES = [
        ("", "— Без иконки —"),
        ("phone", "Телефон"),
        ("mail", "Письмо"),
        ("document", "Документ/прайс"),
        ("calendar", "Календарь"),
        ("question", "Вопрос"),
        ("alert", "Восклицательный знак"),
        ("education", "Обучение/книга"),
        ("send", "Отправить/стрелка"),
        ("check", "Галочка"),
        ("clock", "Часы"),
        ("repeat", "Повтор"),
        ("target", "Цель/мишень"),
        ("user", "Клиент"),
        ("team", "Команда"),
        ("money", "Оплата/деньги"),
        ("cart", "Заказ/заявка"),
        ("chat", "Чат/диалог"),
        ("star", "Важно"),
    ]

    COLOR_CHOICES = [
        ("", "— Без цвета —"),
        ("badge-gray", "Серый"),
        ("badge-blue", "Синий"),
        ("badge-green", "Зелёный"),
        ("badge-red", "Красный"),
        ("badge-amber", "Жёлто-оранжевый"),
        ("badge-orange", "Оранжевый"),
        ("badge-teal", "Бирюзовый"),
        ("badge-indigo", "Индиго"),
        ("badge-purple", "Фиолетовый"),
        ("badge-pink", "Розовый"),
    ]

    icon = forms.ChoiceField(
        choices=ICON_CHOICES,
        required=False,
        label="Иконка",
        widget=forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
    )

    color = forms.ChoiceField(
        choices=COLOR_CHOICES,
        required=False,
        label="Цвет",
        widget=forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
    )

    class Meta:
        model = TaskType
        fields = ["name", "icon", "color"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class UserCreateForm(forms.ModelForm):
    """
    Форма создания пользователя.
    Пароли не используются - вместо них генерируется ключ доступа.
    """
    class Meta:
        model = User
        # data_scope больше не используем: вся база компаний видна всем пользователям.
        fields = ["username", "first_name", "last_name", "email", "role", "branch", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "first_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "last_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "email": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "role": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "branch": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }

    def save(self, commit=True, created_by=None):
        """
        Сохраняет пользователя и автоматически генерирует ключ доступа.
        created_by - администратор, который создаёт пользователя (для логирования).
        """
        user = super().save(commit=False)
        # Устанавливаем неиспользуемый пароль (вход только по ключу доступа)
        user.set_unusable_password()
        # Админка доступна только ADMIN + is_staff
        user.is_staff = user.role == User.Role.ADMIN
        if commit:
            user.save()
            # Автоматически генерируем ключ доступа для нового пользователя
            if created_by:
                from accounts.models import MagicLinkToken
                MagicLinkToken.create_for_user(user=user, created_by=created_by)
        return user


class UserEditForm(forms.ModelForm):
    """
    Форма редактирования пользователя.
    Пароли не используются - вместо них используется генерация ключа доступа.
    """
    class Meta:
        model = User
        # data_scope больше не используем: вся база компаний видна всем пользователям.
        fields = ["username", "first_name", "last_name", "email", "role", "branch", "is_active"]
        widgets = {
            "username": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "first_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "last_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "email": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "role": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "branch": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }

    def save(self, commit=True):
        user = super().save(commit=False)
        # Админка доступна только ADMIN + is_staff
        user.is_staff = user.role == User.Role.ADMIN
        # Убеждаемся, что пароль неиспользуемый (если пользователь был создан до миграции)
        if not user.has_usable_password():
            user.set_unusable_password()
        if commit:
            user.save()
        return user


class ImportCompaniesForm(forms.Form):
    csv_file = forms.FileField(label="CSV файл")
    limit_companies = forms.IntegerField(label="Сколько компаний импортировать", min_value=1, max_value=1000, initial=20)
    dry_run = forms.BooleanField(label="Только проверить (dry-run)", required=False, initial=True)


class ImportTasksIcsForm(forms.Form):
    ics_file = forms.FileField(label="ICS файл (.ics)")
    limit_events = forms.IntegerField(label="Сколько задач импортировать", min_value=1, max_value=20000, initial=500)
    dry_run = forms.BooleanField(label="Только проверить (dry-run)", required=False, initial=True)
    only_linked = forms.BooleanField(
        label="Импортировать только задачи, привязанные к существующей компании",
        required=False,
        initial=True,
    )
    unmatched_mode = forms.ChoiceField(
        label="Если компания не найдена",
        choices=[
            ("skip", "Пропустить задачу"),
            ("keep", "Импортировать без компании"),
            ("create_company", "Создать компанию-заглушку и привязать"),
        ],
        initial="keep",
        required=True,
    )


class AmoApiConfigForm(forms.Form):
    domain = forms.CharField(label="Домен amoCRM", initial="kmrprofi.amocrm.ru")
    client_id = forms.CharField(label="Client ID")
    client_secret = forms.CharField(label="Client Secret", required=False, widget=forms.PasswordInput(render_value=True))
    redirect_uri = forms.CharField(label="Redirect URI", required=False, help_text="Если пусто — возьмём автоматически (callback URL).")
    long_lived_token = forms.CharField(
        label="Долгосрочный токен (рекомендуем для миграции)",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
        help_text="Можно использовать вместо OAuth. Скопируйте из amoCRM: Ключи и доступы → Долгосрочный токен.",
    )


class AmoMigrateFilterForm(forms.Form):
    dry_run = forms.BooleanField(label="Только проверить (dry-run)", required=False, initial=True)
    limit_companies = forms.IntegerField(label="Размер пачки компаний", min_value=1, max_value=5000, initial=50, required=False)
    offset = forms.IntegerField(label="Offset", required=False, initial=0)
    responsible_user_id = forms.IntegerField(label="Ответственный (amo user id)")
    migrate_all_companies = forms.BooleanField(label="Мигрировать все компании ответственного (без фильтра по полю)", required=False, initial=False)
    custom_field_id = forms.IntegerField(label="Кастомное поле (id)", required=False)
    custom_value_label = forms.CharField(label="Значение (текст)", required=False, initial="Новая CRM")
    custom_value_enum_id = forms.IntegerField(label="Значение (enum id)", required=False)
    import_tasks = forms.BooleanField(label="Импортировать задачи", required=False, initial=True)
    import_notes = forms.BooleanField(label="Импортировать заметки", required=False, initial=True)
    import_contacts = forms.BooleanField(label="Импортировать контакты (может быть медленно)", required=False, initial=False)

    def clean(self):
        cleaned_data = super().clean()
        migrate_all = cleaned_data.get("migrate_all_companies", False)
        custom_field_id = cleaned_data.get("custom_field_id")
        
        # Если не выбрана миграция всех компаний, то кастомное поле обязательно
        if not migrate_all and not custom_field_id:
            raise forms.ValidationError("Выберите кастомное поле или включите опцию 'Мигрировать все компании ответственного'.")
        
        return cleaned_data


class CompanyListColumnsForm(forms.Form):
    columns = forms.MultipleChoiceField(
        label="Поля в таблице компаний",
        required=False,
        choices=UiGlobalConfig.COMPANY_LIST_COLUMNS,
        widget=forms.CheckboxSelectMultiple,
    )

    def clean_columns(self):
        cols = self.cleaned_data.get("columns") or []
        # компания должна быть всегда
        if "name" not in cols:
            cols = ["name"] + list(cols)
        # оставляем только известные
        allowed = {k for k, _ in UiGlobalConfig.COMPANY_LIST_COLUMNS}
        cols = [c for c in cols if c in allowed]
        return cols


class ContactForm(forms.ModelForm):
    class Meta:
        model = Contact
        fields = ["last_name", "first_name", "position", "status", "note"]
        widgets = {
            "last_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "first_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "position": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "status": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "note": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
        }


class _BaseEmailFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        values = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if self.can_delete and form.cleaned_data.get("DELETE"):
                continue
            v = (form.cleaned_data.get("value") or "").strip().lower()
            if not v:
                continue
            values.append(v)

        # Дубли в рамках формы
        if len(values) != len(set(values)):
            raise ValidationError("Есть повторяющиеся email в форме.")

        # Дубли в БД (глобально, чтобы не плодить)
        contact_id = getattr(self.instance, "id", None)
        for v in set(values):
            qs = ContactEmail.objects.filter(value__iexact=v)
            if contact_id:
                qs = qs.exclude(contact_id=contact_id)
            if qs.exists():
                raise ValidationError(f"Email {v} уже используется в другом контакте.")


def _normalize_phone(phone: str) -> str:
    """Нормализует номер телефона так же, как в ContactPhone.save()"""
    if not phone:
        return ""
    phone = str(phone).strip()
    # Убираем все нецифровые символы, кроме + в начале
    digits = ''.join(c for c in phone if c.isdigit() or (c == '+' and phone.startswith('+')))
    # Если начинается с +7, проверяем следующую цифру
    if digits.startswith('+7'):
        digits_only = digits[2:]  # Убираем +7
        # Если после +7 идет 8, убираем её (например +78XXXXXXXXX -> +7XXXXXXXXX)
        if digits_only.startswith('8') and len(digits_only) > 10:
            digits_only = digits_only[1:]
        # Если осталось 10 цифр, формируем +7XXXXXXXXXX
        if len(digits_only) == 10:
            return '+7' + digits_only[:50]
        else:
            return phone[:50]
    # Если начинается с 8 и 11 цифр, заменяем на +7
    elif digits.startswith('8') and len(digits) == 11:
        return '+7' + digits[1:][:50]
    # Если начинается с 7 и 11 цифр, добавляем +
    elif digits.startswith('7') and len(digits) == 11:
        return '+' + digits[:50]
    # Если 10 цифр, добавляем +7
    elif len(digits) == 10:
        return '+7' + digits[:50]
    # Иначе обрезаем до 50 символов
    else:
        return phone[:50]


class _BasePhoneFormSet(BaseInlineFormSet):
    def clean(self):
        super().clean()
        values = []
        for form in self.forms:
            if not hasattr(form, "cleaned_data"):
                continue
            if self.can_delete and form.cleaned_data.get("DELETE"):
                continue
            v = (form.cleaned_data.get("value") or "").strip()
            if not v:
                continue
            # Нормализуем телефон так же, как в ContactPhone.save()
            normalized = _normalize_phone(v)
            values.append(normalized)

        if len(values) != len(set(values)):
            raise ValidationError("Есть повторяющиеся телефоны в форме.")


ContactEmailFormSet = inlineformset_factory(
    Contact,
    ContactEmail,
    fields=("type", "value"),
    extra=2,
    can_delete=True,
    formset=_BaseEmailFormSet,
    widgets={
        "type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        "value": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
    },
)


ContactPhoneFormSet = inlineformset_factory(
    Contact,
    ContactPhone,
    fields=("type", "value"),
    extra=2,
    can_delete=True,
    formset=_BasePhoneFormSet,
    widgets={
        "type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        "value": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
    },
)


