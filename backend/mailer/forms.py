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
            self.fields["smtp_password"].help_text = (
                "Оставьте пустым, чтобы не менять сохраненный пароль. "
                + (self.fields["smtp_password"].help_text or "")
            )
        # Если API ключ уже сохранен, показываем подсказку
        if self.instance and self.instance.pk and self.instance.smtp_bz_api_key_enc:
            self.fields["smtp_bz_api_key"].help_text = (
                "Оставьте пустым, чтобы не менять сохраненный ключ. "
                + (self.fields["smtp_bz_api_key"].help_text or "")
            )

    class Meta:
        model = GlobalMailAccount
        fields = [
            "smtp_host",
            "smtp_port",
            "use_starttls",
            "smtp_username",
            "from_email",
            "from_name",
            "is_enabled",
        ]
        widgets = {
            "smtp_host": forms.TextInput(attrs={"class": "input"}),
            "smtp_port": forms.NumberInput(attrs={"class": "input"}),
            "use_starttls": forms.CheckboxInput(),
            "smtp_username": forms.TextInput(attrs={"class": "input"}),
            "from_email": forms.EmailInput(attrs={"class": "input"}),
            "from_name": forms.TextInput(attrs={"class": "input"}),
        }

    def clean(self):
        cleaned_data = super().clean()
        # Проверяем, что либо пароль указан, либо он уже сохранен
        password = (cleaned_data.get("smtp_password") or "").strip()
        if not password and self.instance and self.instance.pk:
            # Пароль не указан, но это редактирование - проверяем, есть ли сохраненный пароль
            if not self.instance.smtp_password_enc:
                # Пароль не был сохранен ранее - требуем его указать
                raise forms.ValidationError(
                    {
                        "smtp_password": "Пароль SMTP обязателен для настройки отправки писем. API ключ используется только для получения информации о квоте."
                    }
                )
        return cleaned_data

    def save(self, commit=True):
        obj: GlobalMailAccount = super().save(commit=False)
        p = (self.cleaned_data.get("smtp_password") or "").strip()
        if p:
            obj.set_password(p)
        # Сохраняем API ключ зашифрованным (если указан; пусто — не меняем)
        api_key = (self.cleaned_data.get("smtp_bz_api_key") or "").strip()
        if api_key:
            obj.set_api_key(api_key)
        if commit:
            obj.save()
        return obj


class CampaignForm(forms.ModelForm):
    remove_attachment = forms.BooleanField(
        label="Удалить текущее вложение",
        required=False,
        initial=False,
        help_text="Если включено — текущее вложение будет удалено при сохранении.",
    )
    send_at = forms.DateTimeField(
        label="Запланировано на",
        required=False,
        widget=forms.DateTimeInput(
            attrs={"class": "input", "type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
        ),
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"],
        help_text="Оставьте пустым для немедленного старта. Рассылка не начнётся раньше указанного времени. Время — МСК (UTC+3).",
    )

    class Meta:
        model = Campaign
        fields = ["name", "subject", "sender_name", "body_html", "attachment", "send_at"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input"}),
            "sender_name": forms.TextInput(
                attrs={"class": "input", "placeholder": "Например: CRM ПРОФИ / Отдел продаж"}
            ),
            "body_html": forms.Textarea(
                attrs={
                    "class": "textarea",
                    "rows": 10,
                    "placeholder": "<p>...</p>",
                    "id": "id_body_html",
                }
            ),
            "attachment": forms.FileInput(
                attrs={
                    "class": "input",
                    "accept": ".pdf,.doc,.docx,.xls,.xlsx,.ppt,.pptx,.png,.jpg,.jpeg,.gif,.webp,.txt,.csv,.zip,.rar",
                }
            ),
        }

    # Таблица магических байтов: расширение → (сигнатура(ы), смещение)
    _MAGIC_BYTES: dict[str, list[tuple[bytes, int]]] = {
        ".pdf": [(b"%PDF", 0)],
        ".png": [(b"\x89PNG\r\n\x1a\n", 0)],
        ".jpg": [(b"\xff\xd8\xff", 0)],
        ".jpeg": [(b"\xff\xd8\xff", 0)],
        ".gif": [(b"GIF87a", 0), (b"GIF89a", 0)],
        # WEBP: байты 0-3 = "RIFF", байты 8-11 = "WEBP" — оба условия обязательны.
        # Хранится как два отдельных entry; проверяются через all() в clean_attachment.
        ".webp": [(b"RIFF", 0), (b"WEBP", 8)],
        # Office Open XML (.docx/.xlsx/.pptx) и .zip — ZIP-контейнер
        ".docx": [(b"PK\x03\x04", 0)],
        ".xlsx": [(b"PK\x03\x04", 0)],
        ".pptx": [(b"PK\x03\x04", 0)],
        ".zip": [(b"PK\x03\x04", 0), (b"PK\x05\x06", 0)],
        ".rar": [(b"Rar!\x1a\x07", 0)],
        # Устаревший OLE2 (бинарный .doc/.xls/.ppt)
        ".doc": [(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0)],
        ".xls": [(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0)],
        ".ppt": [(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0)],
        # Текстовые форматы — магических байтов нет; проверяем только расширение
        ".txt": [],
        ".csv": [],
    }

    def clean_attachment(self):
        attachment = self.cleaned_data.get("attachment")
        if attachment:
            # Проверка размера файла (максимум 15 МБ)
            max_size = 15 * 1024 * 1024  # 15 МБ
            if attachment.size > max_size:
                raise forms.ValidationError(
                    f"Размер файла не должен превышать 15 МБ. Текущий размер: {attachment.size / 1024 / 1024:.2f} МБ"
                )
            # Allowlist расширений (дублируем accept атрибут, но на сервере)
            allowed_ext = set(self._MAGIC_BYTES.keys())
            name = getattr(attachment, "name", "") or ""
            lower = name.lower()
            ext = "." + lower.rsplit(".", 1)[-1] if "." in lower else ""
            if ext not in allowed_ext:
                raise forms.ValidationError("Недопустимый тип файла вложения.")
            # Верификация по magic bytes (для форматов, где сигнатура определена).
            # GIF / ZIP: несколько допустимых сигнатур — any().
            # WEBP: требует ОБЕ сигнатуры (RIFF @ 0 И WEBP @ 8) — all().
            signatures = self._MAGIC_BYTES.get(ext, [])
            if signatures:
                try:
                    attachment.seek(0)
                    header = attachment.read(16)
                    attachment.seek(0)
                except Exception:
                    header = b""
                if ext == ".webp":
                    matched = all(
                        header[offset : offset + len(sig)] == sig for sig, offset in signatures
                    )
                else:
                    matched = any(
                        header[offset : offset + len(sig)] == sig for sig, offset in signatures
                    )
                if not matched:
                    raise forms.ValidationError(
                        "Содержимое файла не соответствует его расширению. "
                        "Проверьте, что файл не повреждён."
                    )
        return attachment

    def clean_body_html(self):
        html = (self.cleaned_data.get("body_html") or "").strip()
        sanitized = sanitize_email_html(html)
        # Проверяем что после санитизации осталось видимое содержимое
        import re as _re

        text_only = _re.sub(r"<[^>]+>", "", sanitized).strip()
        if not text_only:
            raise forms.ValidationError("Текст письма не может быть пустым.")
        return sanitized

    def save(self, commit=True):
        obj: Campaign = super().save(commit=False)
        remove_att = bool(self.cleaned_data.get("remove_attachment"))
        att = self.cleaned_data.get("attachment")
        if att is not None:
            # Если пользователь загрузил новый файл, фиксируем оригинальное имя.
            # os.path.basename защищает от path-traversal (старые браузеры слали полный путь).
            try:
                import os as _os

                raw_name = (getattr(att, "name", "") or "").strip()
                obj.attachment_original_name = _os.path.basename(raw_name)[:255]
            except Exception:
                pass
            # При замене снимаем флаг удаления (на случай, если пользователь отметил галку и выбрал файл)
            remove_att = False

        if remove_att and getattr(obj, "attachment", None):
            # Удаляем файл со стораджа и очищаем поля
            try:
                obj.attachment.delete(save=False)
            except Exception:
                pass
            obj.attachment = None
            obj.attachment_original_name = ""
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
        help_text="Email из поля 'Email (основной)' в карточке компании",
    )
    include_contact_emails = forms.BooleanField(
        label="Включить email'ы контактов",
        required=False,
        initial=True,
        help_text="Email'ы из контактов компании",
    )
    contact_email_types = forms.MultipleChoiceField(
        label="Типы email'ов контактов",
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple(),
        help_text="Выберите типы email'ов для включения (если включены email'ы контактов)",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Импортируем здесь, чтобы избежать циклических зависимостей
        from companies.models import ContactEmail

        self.fields["contact_email_types"].choices = ContactEmail.EmailType.choices
        # По умолчанию выбираем все типы, если форма не была отправлена
        if not self.is_bound:
            self.fields["contact_email_types"].initial = [
                choice[0] for choice in ContactEmail.EmailType.choices
            ]


class CampaignRecipientAddForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "input", "placeholder": "email@example.com"}),
    )
