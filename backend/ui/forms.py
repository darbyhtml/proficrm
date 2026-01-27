import mimetypes
from ui.timezone_utils import RUS_TZ_CHOICES, guess_ru_timezone_from_address
from companies.normalizers import normalize_phone, normalize_inn, normalize_work_schedule
from uuid import UUID
from django import forms
from django.forms import inlineformset_factory, BaseInlineFormSet, ValidationError
from django.contrib.auth.password_validation import validate_password
from django.db.models import Q

from accounts.models import Branch, User
from companies.models import Company, CompanyNote, CompanySphere, CompanyStatus, Contact, ContactEmail, ContactPhone
from tasksapp.models import Task, TaskType
from ui.models import UiGlobalConfig
from ui.widgets import TaskTypeSelectWidget, UserSelectWithBranchWidget
from ui.cleaners import clean_int_id


class FlexibleUserChoiceField(forms.ModelChoiceField):
    """
    Кастомное поле для выбора пользователя, которое правильно обрабатывает значения,
    приходящие в различных форматах (например, "['1']" вместо "1").
    """
    def to_python(self, value):
        """
        Преобразует значение в объект User.
        Обрабатывает случаи, когда значение приходит как строка "['1']" вместо "1".
        """
        if value in self.empty_values:
            return None
        
        # Если значение уже является объектом User, возвращаем его
        if isinstance(value, User):
            return value
        
        # Очищаем значение, если оно пришло в неправильном формате
        cleaned_value = value
        if isinstance(value, str) or isinstance(value, (list, tuple)):
            cleaned_id = clean_int_id(value)
            if cleaned_id is not None:
                cleaned_value = cleaned_id
        
        # Вызываем стандартный метод to_python с очищенным значением
        return super().to_python(cleaned_value)
    
    def clean(self, value):
        """
        Переопределяем clean для обработки ошибок валидации.
        Если стандартная валидация не прошла, пытаемся найти пользователя по очищенному значению.
        """
        if value in self.empty_values:
            if self.required:
                raise forms.ValidationError(self.error_messages['required'], code='required')
            return None
        
        # Сначала пытаемся стандартную валидацию
        try:
            return super().clean(value)
        except forms.ValidationError as e:
            # Если валидация не прошла, пытаемся очистить значение и найти пользователя
            cleaned_id = clean_int_id(value)
            if cleaned_id is not None:
                try:
                    # Пытаемся найти пользователя по очищенному ID
                    user = User.objects.get(id=cleaned_id, is_active=True)
                    # Проверяем, что пользователь в queryset (или добавляем его)
                    if not self.queryset.filter(id=cleaned_id).exists():
                        # Временно расширяем queryset, чтобы валидация прошла
                        self.queryset = User.objects.filter(
                            Q(id__in=self.queryset.values_list('id', flat=True)) | Q(id=cleaned_id)
                        )
                    return user
                except (User.DoesNotExist, ValueError, TypeError):
                    pass
            
            # Если не удалось найти пользователя, пробрасываем оригинальную ошибку
            raise e


class FlexibleCompanyChoiceField(forms.ModelChoiceField):
    """
    Поле выбора компании, которое корректно работает с AJAX-подгрузкой опций:
    если выбранное значение не входит в текущий queryset (который может быть пустым/минимальным),
    пробуем загрузить объект из "разрешённого" queryset по ID.
    """

    allowed_qs_getter = None

    def clean(self, value):
        if value in self.empty_values:
            if self.required:
                raise forms.ValidationError(self.error_messages["required"], code="required")
            return None

        try:
            return super().clean(value)
        except forms.ValidationError as e:
            # Попытка восстановить объект по UUID (для AJAX-опций)
            try:
                company_id = UUID(str(value).strip())
            except Exception:
                raise e

            qs = self.allowed_qs_getter() if callable(self.allowed_qs_getter) else self.queryset
            try:
                obj = qs.only("id", "name", "head_company_id").get(id=company_id)
            except Company.DoesNotExist:
                raise e

            # Временно расширяем queryset, чтобы последующие проверки/рендер были консистентны
            self.queryset = Company.objects.filter(
                Q(id__in=self.queryset.values_list("id", flat=True)) | Q(id=company_id)
            ).only("id", "name", "head_company_id")
            return obj


class CompanyCreateForm(forms.ModelForm):
    head_company = FlexibleCompanyChoiceField(queryset=Company.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        super().__init__(*args, **kwargs)

        # Для head_company используем AJAX-поиск (см. base.html), поэтому не грузим огромный <select>.
        # Но при этом должны валидировать любой выбранный ID — через FlexibleCompanyChoiceField.
        self.fields["head_company"].empty_label = "— Не выбрано —"
        self.fields["head_company"].allowed_qs_getter = (lambda: Company.objects.all())
        
        # Оптимизация queryset для status и spheres: используем only() для загрузки только необходимых полей
        self.fields["status"].queryset = CompanyStatus.objects.only("id", "name").order_by("name")
        self.fields["spheres"].queryset = CompanySphere.objects.only("id", "name").order_by("name")
        
        # Поле email необязательное (бывают ситуации, когда email еще неизвестен)
        self.fields["email"].required = False

        # Часовой пояс: показываем понятный селект по РФ + авто по адресу при пустом значении.
        self.fields["work_timezone"].required = False
        _choices = [("", "Авто (по адресу)")] + RUS_TZ_CHOICES
        self.fields["work_timezone"].widget = forms.Select(choices=_choices, attrs={"class": "w-full rounded-lg border px-3 py-2"})
        # На GET (когда форма не связана) подставляем предположение из адреса
        if not self.is_bound:
            addr = (getattr(self.instance, "address", "") or "").strip()
            if addr and not (getattr(self.instance, "work_timezone", "") or "").strip():
                guessed = guess_ru_timezone_from_address(addr)
                if guessed:
                    self.initial["work_timezone"] = guessed

    def clean(self):
        cleaned = super().clean()
        tz = (cleaned.get("work_timezone") or "").strip()
        addr = (cleaned.get("address") or "").strip()
        if not tz and addr:
            guessed = guess_ru_timezone_from_address(addr)
            if guessed:
                cleaned["work_timezone"] = guessed
        ws = (cleaned.get("work_schedule") or "").strip()
        if ws:
            cleaned["work_schedule"] = normalize_work_schedule(ws)
        return cleaned

    def clean_inn(self):
        return normalize_inn(self.cleaned_data.get("inn"))
    
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
            "employees_count",
            "work_timezone",
            "work_schedule",
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
            "inn": forms.Textarea(
                attrs={
                    "rows": 2,
                    "class": "w-full rounded-lg border px-3 py-2 font-mono",
                    "placeholder": "Можно несколько ИНН: через /, запятую, пробел или с новой строки",
                }
            ),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "activity_kind": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2", "placeholder": "Напр.: строительство, услуги, производство… (можно с новой строки)"}),
            "employees_count": forms.NumberInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "min": "0", "placeholder": "Напр.: 120"}),
            "work_timezone": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "work_schedule": forms.Textarea(attrs={"rows": 6, "class": "w-full rounded-lg border px-3 py-2 font-mono text-sm", "placeholder": "Например:\nПн-Пт: 09:00-18:00\nСб: 10:00-16:00\nВс: выходной\n\nИли скопируйте режим работы с сайта компании. Время автоматически форматируется в формат HH:MM."}),
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
        fields = ["status", "spheres", "region"]
        widgets = {
            "status": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "spheres": forms.SelectMultiple(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "region": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyEditForm(forms.ModelForm):
    """
    Полное редактирование данных компании (без смены ответственного/филиала).
    Статус/сферы здесь тоже доступны, чтобы редактирование было "в одном месте".
    """
    head_company = FlexibleCompanyChoiceField(queryset=Company.objects.none(), required=False)

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        super().__init__(*args, **kwargs)
        # Поле email необязательное (бывают ситуации, когда email еще неизвестен)
        self.fields["email"].required = False

        # head_company: минимальный queryset для рендера (текущая выбранная + пустая),
        # дальше работает AJAX-поиск, а валидация идёт через allowed_qs_getter.
        self.fields["head_company"].empty_label = "— Не выбрано —"
        self.fields["head_company"].allowed_qs_getter = (lambda: Company.objects.all())
        current_id = getattr(self.instance, "head_company_id", None)
        if current_id:
            self.fields["head_company"].queryset = Company.objects.filter(id=current_id).only("id", "name", "head_company_id")
        else:
            self.fields["head_company"].queryset = Company.objects.none()

        # Часовой пояс: селект по РФ + авто по адресу при пустом значении.
        self.fields["work_timezone"].required = False
        _choices = [("", "Авто (по адресу)")] + RUS_TZ_CHOICES
        current_tz = (getattr(self.instance, "work_timezone", "") or "").strip()
        if current_tz and all(v != current_tz for v, _ in _choices):
            _choices = _choices + [(current_tz, current_tz)]
        self.fields["work_timezone"].widget = forms.Select(choices=_choices, attrs={"class": "w-full rounded-lg border px-3 py-2"})
        if not self.is_bound:
            addr = (getattr(self.instance, "address", "") or "").strip()
            if addr and not (getattr(self.instance, "work_timezone", "") or "").strip():
                guessed = guess_ru_timezone_from_address(addr)
                if guessed:
                    self.initial["work_timezone"] = guessed

    def clean(self):
        cleaned = super().clean()
        tz = (cleaned.get("work_timezone") or "").strip()
        addr = (cleaned.get("address") or "").strip()
        if not tz and addr:
            guessed = guess_ru_timezone_from_address(addr)
            if guessed:
                cleaned["work_timezone"] = guessed
        ws = (cleaned.get("work_schedule") or "").strip()
        if ws:
            cleaned["work_schedule"] = normalize_work_schedule(ws)
        return cleaned

    def clean_inn(self):
        return normalize_inn(self.cleaned_data.get("inn"))

    def clean_head_company(self):
        hc = self.cleaned_data.get("head_company")
        if not hc:
            return None
        if not getattr(self.instance, "id", None):
            return hc

        if hc.id == self.instance.id:
            raise ValidationError("Нельзя выбрать эту компанию как головную для самой себя.")

        # Защита от циклов: у выбранной головной не должно быть в цепочке родителей текущей компании.
        cur_id = hc.id
        seen: set = set()
        while cur_id:
            if cur_id in seen:
                break
            seen.add(cur_id)
            if cur_id == self.instance.id:
                raise ValidationError("Нельзя выбрать дочернюю карточку этой организации как головную (получится цикл).")
            cur = Company.objects.only("id", "head_company_id").filter(id=cur_id).first()
            if not cur or not cur.head_company_id:
                break
            cur_id = cur.head_company_id

        return hc
    
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
            "employees_count",
            "work_timezone",
            "work_schedule",
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
            "inn": forms.Textarea(
                attrs={
                    "rows": 2,
                    "class": "w-full rounded-lg border px-3 py-2 font-mono",
                    "placeholder": "Можно несколько ИНН: через /, запятую, пробел или с новой строки",
                }
            ),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "activity_kind": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2", "placeholder": "Напр.: строительство, услуги, производство… (можно с новой строки)"}),
            "employees_count": forms.NumberInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "min": "0", "placeholder": "Напр.: 120"}),
            "work_timezone": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "work_schedule": forms.Textarea(attrs={"rows": 6, "class": "w-full rounded-lg border px-3 py-2 font-mono text-sm", "placeholder": "Например:\nПн-Пт: 09:00-18:00\nСб: 10:00-16:00\nВс: выходной\n\nИли скопируйте режим работы с сайта компании. Время автоматически форматируется в формат HH:MM."}),
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


class CompanyInlineEditForm(forms.ModelForm):
    """
    Инлайн-редактирование отдельных полей компании из карточки компании.
    Используется для частичного обновления одного поля через AJAX.
    """

    ALLOWED_FIELDS = (
        "name",
        "legal_name",
        "inn",
        "kpp",
        "address",
        "website",
        "activity_kind",
        "work_timezone",
        "work_schedule",
        "region",
    )

    def __init__(self, *args, **kwargs):
        # field: какое именно поле редактируем (для частичного update)
        field = (kwargs.pop("field", None) or "").strip()
        super().__init__(*args, **kwargs)
        self._inline_field = field

        # Оставляем только одно поле, которое редактируем
        if field:
            for f in list(self.fields.keys()):
                if f != field:
                    self.fields.pop(f, None)

    def clean_name(self):
        v = (self.cleaned_data.get("name") or "").strip()
        if not v:
            raise ValidationError("Название обязательно.")
        return v

    def clean_legal_name(self):
        return (self.cleaned_data.get("legal_name") or "").strip()

    def clean_inn(self):
        return normalize_inn(self.cleaned_data.get("inn"))

    def clean_kpp(self):
        return (self.cleaned_data.get("kpp") or "").strip()

    def clean_address(self):
        return (self.cleaned_data.get("address") or "").strip()

    def clean_website(self):
        return (self.cleaned_data.get("website") or "").strip()

    def clean_activity_kind(self):
        return (self.cleaned_data.get("activity_kind") or "").strip()

    def clean_work_timezone(self):
        from ui.timezone_utils import RUS_TZ_CHOICES

        v = (self.cleaned_data.get("work_timezone") or "").strip()
        if not v:
            # пустое = "авто по адресу" (будем брать guessed в UI)
            return ""
        allowed = {tz for tz, _label in (RUS_TZ_CHOICES or [])}
        if v not in allowed:
            raise ValidationError("Недопустимый часовой пояс.")
        return v

    def clean_work_schedule(self):
        v = (self.cleaned_data.get("work_schedule") or "").strip()
        if not v:
            return ""
        return normalize_work_schedule(v)

    def clean_region(self):
        # Разрешаем пустое значение (region = None) или валидный регион справочника.
        return self.cleaned_data.get("region")

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
            "work_timezone",
            "work_schedule",
            "region",
        ]


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
        fields = ["description", "company", "type", "assigned_to", "due_at"]
        widgets = {
            "description": forms.Textarea(attrs={"rows": 4, "class": "w-full rounded-lg border px-3 py-2"}),
            "company": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            # type фактически используется как «Задача» (тип задачи из справочника)
            "type": TaskTypeSelectWidget(attrs={"class": "w-full rounded-lg border px-3 py-2 task-type-select"}),
            "assigned_to": UserSelectWithBranchWidget(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Заменяем стандартное поле assigned_to на кастомное, которое правильно обрабатывает значения
        # ВАЖНО: queryset будет установлен в view через _set_assigned_to_queryset,
        # поэтому здесь устанавливаем временный queryset на всех пользователей
        if 'assigned_to' in self.fields:
            # Сохраняем оригинальные параметры поля
            original_field = self.fields['assigned_to']
            self.fields['assigned_to'] = FlexibleUserChoiceField(
                queryset=User.objects.filter(is_active=True).select_related("branch"),
                widget=original_field.widget,
                required=original_field.required,
                label=original_field.label,
                help_text=original_field.help_text,
            )
            # ВАЖНО: queryset будет переустановлен в view, поэтому не ограничиваем его здесь
    
    def clean_assigned_to(self):
        """
        Переопределяем валидацию assigned_to, чтобы принимать любое значение,
        если оно было передано в форме. Валидация прав будет выполнена в view.
        """
        assigned_to = self.cleaned_data.get('assigned_to')
        if assigned_to:
            # Проверяем, что пользователь существует
            if not User.objects.filter(id=assigned_to.id, is_active=True).exists():
                raise forms.ValidationError("Выбранный пользователь не найден или неактивен.")
        return assigned_to

    def clean_type(self):
        """
        Тип задачи (поле «Задача») обязателен.
        Оставлять «Без статуса» / пустое значение больше нельзя.
        """
        task_type = self.cleaned_data.get("type")
        if not task_type:
            raise forms.ValidationError("Пожалуйста, выберите тип задачи в поле «Задача».")
        return task_type


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
            "type": TaskTypeSelectWidget(attrs={"class": "w-full rounded-lg border px-3 py-2 task-type-select"}),
        }

    def clean_type(self):
        """
        Тип задачи (поле «Задача») обязателен при редактировании.
        """
        task_type = self.cleaned_data.get("type")
        if not task_type:
            raise forms.ValidationError("Пожалуйста, выберите тип задачи в поле «Задача».")
        return task_type

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

    def save(self, commit=True, created_by=None, request=None):
        """
        Сохраняет пользователя.
        Для администраторов автоматически генерируется пароль.
        Для остальных пользователей генерируется ключ доступа.
        created_by - администратор, который создаёт пользователя (для логирования).
        request - объект HttpRequest для сохранения данных в сессии (опционально).
        """
        user = super().save(commit=False)
        # Админка доступна только ADMIN + is_staff
        user.is_staff = user.role == User.Role.ADMIN
        
        if commit:
            # Для администраторов генерируем пароль
            if user.role == User.Role.ADMIN:
                import secrets
                import string
                # Генерируем сложный пароль: минимум 16 символов, включая буквы, цифры и спецсимволы
                alphabet = string.ascii_letters + string.digits + "!@#$%^&*()_+-=[]{}|;:,.<>?"
                password = ''.join(secrets.choice(alphabet) for _ in range(16))
                user.set_password(password)
                # Сохраняем пароль в сессии для отображения (только один раз)
                if request and created_by:
                    request.session["admin_password_generated"] = {
                        "user_id": None,  # Будет установлен после сохранения user
                        "password": password,
                    }
            else:
                # Для не-администраторов устанавливаем неиспользуемый пароль и генерируем ключ доступа
                user.set_unusable_password()
            
            user.save()
            
            # Обновляем user_id в сессии, если пароль был сохранен
            if user.role == User.Role.ADMIN and request and "admin_password_generated" in request.session:
                request.session["admin_password_generated"]["user_id"] = user.id
            
            # Автоматически генерируем ключ доступа только для не-администраторов
            if user.role != User.Role.ADMIN and created_by:
                from accounts.models import MagicLinkToken
                magic_link, plain_token = MagicLinkToken.create_for_user(user=user, created_by=created_by)
                # Сохраняем plain_token в сессии для отображения
                if request:
                    from django.conf import settings as django_settings
                    public_base_url = getattr(django_settings, "PUBLIC_BASE_URL", None)
                    if public_base_url:
                        base_url = public_base_url.rstrip("/")
                    else:
                        # Если нет request, base_url будет установлен в view
                        base_url = ""
                    if base_url:
                        magic_link_url = f"{base_url}/auth/magic/{plain_token}/"
                    else:
                        magic_link_url = None
                    
                    request.session["magic_link_generated"] = {
                        "token": plain_token,
                        "link": magic_link_url,
                        "expires_at": magic_link.expires_at.isoformat(),
                        "user_id": user.id,
                    }
        return user


class UserEditForm(forms.ModelForm):
    """
    Форма редактирования пользователя.
    Для администраторов доступна генерация пароля.
    """
    new_password = forms.CharField(
        label="Новый пароль (только для администраторов)",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "id": "id_new_password"}),
        help_text="Оставьте пустым, чтобы не менять пароль. Доступно только для пользователей с ролью 'Администратор'.",
    )

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

    def clean_new_password(self):
        p = self.cleaned_data.get("new_password") or ""
        if p:
            validate_password(p)
        return p

    def save(self, commit=True):
        user = super().save(commit=False)
        # Админка доступна только ADMIN + is_staff
        user.is_staff = user.role == User.Role.ADMIN
        
        # Устанавливаем пароль только для администраторов
        p = self.cleaned_data.get("new_password") or ""
        if p and user.role == User.Role.ADMIN:
            user.set_password(p)
        elif user.role != User.Role.ADMIN:
            # Для не-администраторов устанавливаем неиспользуемый пароль
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
    region_custom_field_id = forms.IntegerField(
        label="ID кастомного поля региона (amoCRM)",
        required=False,
        help_text="Опционально. Если указать — при импорте компаний регион будет пытаться заполняться по этому полю.",
    )


class AmoMigrateFilterForm(forms.Form):
    dry_run = forms.BooleanField(label="Только проверить (dry-run)", required=False, initial=True)
    limit_companies = forms.IntegerField(label="Размер пачки компаний", min_value=1, max_value=5000, initial=50, required=False)
    offset = forms.IntegerField(label="Offset", required=False, initial=0)
    responsible_user_id = forms.CharField(
        label="Ответственный (amo, менеджер)",
        required=True,
        help_text="Выберите одного ответственного (менеджера) amoCRM."
    )
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

        # Дубли в рамках формы (внутри одного контакта нельзя иметь одинаковые email)
        if len(values) != len(set(values)):
            raise ValidationError("Есть повторяющиеся email в форме.")

        # ПРИМЕЧАНИЕ: Убрана проверка на дубликаты между разными контактами.
        # Один email может использоваться несколькими контактами (например, общий email отдела
        # или человек работает в разных компаниях).
        # Дедупликация email адресов выполняется в разделе "Почта" при составлении списка рассылки.


def _normalize_phone(phone: str) -> str:
    """Нормализует номер телефона используя единый нормализатор"""
    return normalize_phone(phone)


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


