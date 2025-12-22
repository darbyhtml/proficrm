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
        fields = ["name", "legal_name", "inn", "kpp", "address", "website", "phone", "email", "contact_name", "contact_position", "status", "spheres"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "legal_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "inn": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
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
        fields = ["name", "legal_name", "inn", "kpp", "address", "website", "phone", "email", "contact_name", "contact_position", "status", "spheres"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "legal_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "inn": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "kpp": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "address": forms.Textarea(attrs={"rows": 3, "class": "w-full rounded-lg border px-3 py-2"}),
            "website": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "phone": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "+7 ..."}),
            "email": forms.EmailInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "email@example.com"}),
            "contact_name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "ФИО"}),
            "contact_position": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2", "placeholder": "Должность"}),
            "status": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "spheres": forms.SelectMultiple(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
        }


class CompanyNoteForm(forms.ModelForm):
    class Meta:
        model = CompanyNote
        fields = ["text"]
        widgets = {
            "text": forms.Textarea(attrs={"rows": 4, "placeholder": "Заметка/комментарий...", "class": "w-full rounded-lg border px-3 py-2"}),
        }


class TaskForm(forms.ModelForm):
    due_at = forms.DateTimeField(
        required=False,
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S"],
        widget=forms.DateTimeInput(attrs={"type": "datetime-local", "class": "w-full rounded-lg border px-3 py-2"}),
        label="Дедлайн",
    )

    class Meta:
        model = Task
        fields = ["title", "description", "company", "type", "assigned_to", "due_at", "recurrence_rrule"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "description": forms.Textarea(attrs={"rows": 4, "class": "w-full rounded-lg border px-3 py-2"}),
            "company": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "type": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "assigned_to": forms.Select(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
            "recurrence_rrule": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
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
    class Meta:
        model = TaskType
        fields = ["name"]
        widgets = {"name": forms.TextInput(attrs={"class": "w-full rounded-lg border px-3 py-2"})}


class UserCreateForm(forms.ModelForm):
    password1 = forms.CharField(label="Пароль", widget=forms.PasswordInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}))
    password2 = forms.CharField(label="Пароль ещё раз", widget=forms.PasswordInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}))

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

    def clean(self):
        cleaned = super().clean()
        p1 = cleaned.get("password1") or ""
        p2 = cleaned.get("password2") or ""
        if p1 != p2:
            raise ValidationError("Пароли не совпадают.")
        validate_password(p1)
        return cleaned

    def save(self, commit=True):
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password1"])
        # Админка доступна только ADMIN + is_staff
        user.is_staff = user.role == User.Role.ADMIN
        if commit:
            user.save()
        return user


class UserEditForm(forms.ModelForm):
    new_password = forms.CharField(
        label="Новый пароль (не обязательно)",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "w-full rounded-lg border px-3 py-2"}),
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
        p = self.cleaned_data.get("new_password") or ""
        if p:
            user.set_password(p)
        if commit:
            user.save()
        return user


class ImportCompaniesForm(forms.Form):
    csv_file = forms.FileField(label="CSV файл")
    limit_companies = forms.IntegerField(label="Сколько компаний импортировать", min_value=1, max_value=1000, initial=20)
    dry_run = forms.BooleanField(label="Только проверить (dry-run)", required=False, initial=True)


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
            values.append(v)

        if len(values) != len(set(values)):
            raise ValidationError("Есть повторяющиеся телефоны в форме.")

        contact_id = getattr(self.instance, "id", None)
        for v in set(values):
            qs = ContactPhone.objects.filter(value=v)
            if contact_id:
                qs = qs.exclude(contact_id=contact_id)
            if qs.exists():
                raise ValidationError(f"Телефон {v} уже используется в другом контакте.")


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


