from __future__ import annotations

from django import forms

from mailer.models import Campaign, MailAccount, GlobalMailAccount


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

    class Meta:
        model = GlobalMailAccount
        fields = [
            "smtp_host",
            "smtp_port",
            "use_starttls",
            "smtp_username",
            "from_email",
            "from_name",
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
            "rate_per_minute": forms.NumberInput(attrs={"class": "input"}),
            "rate_per_day": forms.NumberInput(attrs={"class": "input"}),
        }

    def save(self, commit=True):
        obj: GlobalMailAccount = super().save(commit=False)
        p = (self.cleaned_data.get("smtp_password") or "").strip()
        if p:
            obj.set_password(p)
        if commit:
            obj.save()
        return obj


class CampaignForm(forms.ModelForm):
    class Meta:
        model = Campaign
        fields = ["name", "subject", "sender_name", "body_html"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input"}),
            "subject": forms.TextInput(attrs={"class": "input"}),
            "sender_name": forms.TextInput(attrs={"class": "input", "placeholder": "Например: CRM ПРОФИ / Отдел продаж"}),
            "body_html": forms.Textarea(attrs={"class": "textarea", "rows": 10, "placeholder": "<p>...</p>", "id": "id_body_html"}),
        }


class CampaignGenerateRecipientsForm(forms.Form):
    limit = forms.IntegerField(label="Лимит получателей", min_value=1, max_value=5000, initial=200)


class CampaignRecipientAddForm(forms.Form):
    email = forms.EmailField(
        label="Email",
        widget=forms.EmailInput(attrs={"class": "input", "placeholder": "email@example.com"}),
    )

