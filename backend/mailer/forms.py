from __future__ import annotations

from django import forms

from mailer.models import Campaign, MailAccount, GlobalMailAccount
from mailer.utils import sanitize_email_html


class EmailSignatureForm(forms.Form):
    signature_html = forms.CharField(
        label="Подпись (HTML)",
        required=False,
        widget=forms.Textarea(attrs={"class": "textarea", "rows": 10, "id": "id_signature_html"}),
        help_text="HTML-подпись, которая будет добавляться в конец письма.",
    )

    def clean_signature_html(self):
        html = (self.cleaned_data.get("signature_html") or "").strip()
        return sanitize_email_html(html)


class MailAccountForm(forms.ModelForm):
    smtp_password = forms.CharField(
        label="Пароль SMTP",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "input"}),
        help_text="Для smtp.bz создайте логин/пароль в личном кабинете. Для Яндекса используйте пароль приложения.",
    )

    class Meta:
        model = MailAccount
        fields = [
            "smtp_host",
            "smtp_port",
            "use_starttls",
            "smtp_username",
            "from_email",
            "from_name",
            "reply_to",
            "rate_per_minute",
            "rate_per_day",
            "is_enabled",
        ]
        widgets = {
            "smtp_host": forms.TextInput(attrs={"class": "input"}),
            "smtp_port": forms.NumberInput(attrs={"class": "input"}),
            "use_starttls": forms.CheckboxInput(),
            "smtp_username": forms.TextInput(attrs={"class": "input"}),
            "from_email": forms.EmailInput(attrs={"class": "input"}),
            "from_name": forms.TextInput(attrs={"class": "input"}),
            "reply_to": forms.EmailInput(attrs={"class": "input"}),
            "rate_per_minute": forms.NumberInput(attrs={"class": "input"}),
            "rate_per_day": forms.NumberInput(attrs={"class": "input"}),
        }

    def save(self, commit=True):
        obj: MailAccount = super().save(commit=False)
        p = (self.cleaned_data.get("smtp_password") or "").strip()
        if p:
            obj.set_password(p)
        if commit:
            obj.save()
        return obj


class GlobalMailAccountForm(forms.ModelForm):
    smtp_password = forms.CharField(
        label="Пароль SMTP",
        required=False,
        widget=forms.PasswordInput(attrs={"class": "input"}),
        help_text="smtp.bz: логин/пароль берутся из кабинета smtp.bz. Яндекс: пароль приложения.",
    )

    smtp_bz_api_key = forms.CharField(
        label="API ключ smtp.bz",
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "type": "password", "autocomplete": "off"}),
        help_text="API ключ для получения информации о тарифе и квоте. Можно получить в личном кабинете smtp.bz.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Если пароль уже сохранен, делаем поле необязательным и добавляем подсказку
        if self.instance and self.instance.pk and self.instance.smtp_password_enc:
            self.fields["smtp_password"].help_text = "Оставьте пустым, чтобы не менять сохраненный пароль. " + (self.fields["smtp_password"].help_text or "")
        # Если API ключ уже сохранен, показываем подсказку
        if self.instance and self.instance.pk and self.instance.smtp_bz_api_key:
            self.fields["smtp_bz_api_key"].help_text = "Оставьте пустым, чтобы не менять сохраненный ключ. " + (self.fields["smtp_bz_api_key"].help_text or "")

    class Meta:
        model = GlobalMailAccount
        fields = [
            "smtp_host",
            "smtp_port",
            "use_starttls",
            "smtp_username",
            "from_email",
            "from_name",
            "smtp_bz_api_key",
            "is_enabled",
        ]
        widgets = {
            "smtp_host": forms.TextInput(attrs={"class": "input"}),
            "smtp_port": forms.NumberInput(attrs={"class": "input"}),
            "use_starttls": forms.CheckboxInput(),
            "smtp_username": forms.TextInput(attrs={"class": "input"}),
            "from_email": forms.EmailInput(attrs={"class": "input"}),
            "from_name": forms.TextInput(attrs={"class": "input"}),
            "smtp_bz_api_key": forms.TextInput(attrs={"class": "input", "type": "password", "autocomplete": "off"}),
        }
    
    def clean(self):
        cleaned_data = super().clean()
        # Проверяем, что либо пароль указан, либо он уже сохранен
        password = (cleaned_data.get("smtp_password") or "").strip()
        if not password and self.instance and self.instance.pk:
            # Пароль не указан, но это редактирование - проверяем, есть ли сохраненный пароль
            if not self.instance.smtp_password_enc:
                # Пароль не был сохранен ранее - требуем его указать
                raise forms.ValidationError({
                    "smtp_password": "Пароль SMTP обязателен для настройки отправки писем. API ключ используется только для получения информации о квоте."
                })
        return cleaned_data
    
    def save(self, commit=True):
        obj: GlobalMailAccount = super().save(commit=False)
        p = (self.cleaned_data.get("smtp_password") or "").strip()
        if p:
            obj.set_password(p)
        # Сохраняем API ключ, если он был указан (если пусто - не меняем существующий)
        api_key = (self.cleaned_data.get("smtp_bz_api_key") or "").strip()
        if api_key:
            obj.smtp_bz_api_key = api_key
        if commit:
            obj.save()
        return obj


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "subject", "sender_name", "body_html", "attachment"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input"}),
            "sender_name": forms.TextInput(attrs={"class": "input", "placeholder": "Например: CRM ПРОФИ / Отдел продаж"}),
            "body_html": forms.Textarea(attrs={"class": "textarea", "rows": 10, "placeholder": "<p>...</p>", "id": "id_body_html"}),
            "attachment": forms.FileInput(attrs={"class": "input", "accept": ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.zip,.rar"}),
        }
    
    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if attachment:
            # Проверка размера файла (максимум 15 МБ)
            max_size = 15 * 1024 * 1024  # 15 МБ
            if attachment.size > max_size:
                raise forms.ValidationError(f"Размер файла не должен превышать 15 МБ. Текущий размер: {attachment.size / 1024 / 1024:.2f} МБ")
            # Allowlist расширений (дублируем accept атрибут, но на сервере)
            allowed_ext = {
                ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                ".png", ".jpg", ".jpeg", ".gif", ".webp",
                ".txt", ".csv", ".zip", ".rar",
            }
            name = getattr(attachment, "name", "") or ""
            lower = name.lower()
            ext = "." + lower.split(".")[-1] if "." in lower else ""
            if ext and ext not in allowed_ext:
                raise forms.ValidationError("Недопустимый тип файла вложения.")
        return attachment

    def clean_body_html(self):
        html = (self.cleaned_data.get("body_html") or "").strip()
        return sanitize_email_html(html)

    def save(self, commit=True):
        obj: Campaign = super().save(commit=False)
        att = self.cleaned_data.get("attachment")
        if att is not None:
            # Если пользователь загрузил новый файл, фиксируем оригинальное имя.
            try:
                obj.attachment_original_name = (getattr(att, "name", "") or "").strip()[:255]
            except Exception:
                pass
        if commit:
            obj.save()
            self.save_m2m()
        return obj


class CampaignGenerateRecipientsForm(forms.Form):
    limit = forms.IntegerField(label="Лимит получателей", min_value=1, max_value=5000, initial=200)
    include_company_email = forms.BooleanField(
        label="Включить основной email компании",
        required=False,
        initial=True,
        help_text="Email из поля 'Email (основной)' в карточке компании"
    )
    include_contact_emails = forms.BooleanField(
        label="Включить email'ы контактов",
        required=False,
        initial=True,
        help_text="Email'ы из контактов компании"
    )
    contact_email_types = forms.MultipleChoiceField(
        label="Типы email'ов контактов",
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Выберите типы email'ов для включения (если включены email'ы контактов)"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from companies.models import ContactEmail
        self.fields["contact_email_types"].choices = ContactEmail.EmailType.choices
        # По умолчанию выбираем все типы, если форма не была отправлена
        if not self.is_bound:
            self.fields["contact_email_types"].initial = [choice[0] for choice in ContactEmail.EmailType.choices]


class CampaignRecipientAddForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "input", "placeholder": "email@example.com"}),
    )

