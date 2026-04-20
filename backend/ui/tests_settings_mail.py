"""F6 R2 tests (2026-04-18): SMTP config edit + toggle enabled.

Проверяем:
- /admin/mail/setup/save-config/ меняет host/port/username/from/limits.
- /admin/mail/setup/toggle-enabled/ переключает is_enabled.
- Нельзя включить массовую отправку если Fernet-пароль невалиден.
- Валидация: целочисленные диапазоны, email-формат.
- Доступ только ADMIN / superuser.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from mailer.models import GlobalMailAccount

User = get_user_model()


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class MailSaveConfigTests(TestCase):
    """Редактирование SMTP-конфига."""

    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="mail_admin",
            email="admin@example.com",
        )
        # Сбрасываем singleton к дефолтам.
        GlobalMailAccount.objects.filter(id=1).delete()
        self.client.force_login(self.admin)

    def test_save_config_updates_all_fields(self):
        resp = self.client.post(
            "/admin/mail/setup/save-config/",
            {
                "smtp_host": "smtp.example.com",
                "smtp_port": "465",
                "use_starttls": "on",
                "smtp_username": "sender@example.com",
                "from_email": "from@example.com",
                "from_name": "Test Sender",
                "rate_per_minute": "5",
                "rate_per_day": "10000",
                "per_user_daily_limit": "200",
            },
        )
        self.assertEqual(resp.status_code, 302)
        acc = GlobalMailAccount.load()
        self.assertEqual(acc.smtp_host, "smtp.example.com")
        self.assertEqual(acc.smtp_port, 465)
        self.assertTrue(acc.use_starttls)
        self.assertEqual(acc.smtp_username, "sender@example.com")
        self.assertEqual(acc.from_email, "from@example.com")
        self.assertEqual(acc.from_name, "Test Sender")
        self.assertEqual(acc.rate_per_minute, 5)
        self.assertEqual(acc.rate_per_day, 10000)
        self.assertEqual(acc.per_user_daily_limit, 200)

    def test_save_config_starttls_unchecked_sets_false(self):
        # Предварительно ставим True через load-default, обновляем.
        acc = GlobalMailAccount.load()
        acc.use_starttls = True
        acc.save()

        # Checkbox не передаём — должен стать False.
        self.client.post(
            "/admin/mail/setup/save-config/",
            {
                "smtp_host": "smtp.x.ru",
                "smtp_port": "587",
                "smtp_username": "u",
                "from_email": "a@b.ru",
                "from_name": "n",
                "rate_per_minute": "1",
                "rate_per_day": "100",
                "per_user_daily_limit": "50",
            },
        )
        acc.refresh_from_db()
        self.assertFalse(acc.use_starttls)

    def test_save_config_rejects_port_out_of_range(self):
        resp = self.client.post(
            "/admin/mail/setup/save-config/",
            {
                "smtp_host": "smtp.x.ru",
                "smtp_port": "99999",  # > 65535
                "smtp_username": "u",
                "from_email": "a@b.ru",
                "from_name": "n",
                "rate_per_minute": "1",
                "rate_per_day": "100",
                "per_user_daily_limit": "50",
            },
        )
        self.assertEqual(resp.status_code, 302)  # редирект на setup с ошибкой
        acc = GlobalMailAccount.load()
        # Поля не изменились (дефолты остались).
        self.assertNotEqual(acc.smtp_port, 99999)

    def test_save_config_rejects_invalid_email(self):
        resp = self.client.post(
            "/admin/mail/setup/save-config/",
            {
                "smtp_host": "smtp.x.ru",
                "smtp_port": "587",
                "smtp_username": "u",
                "from_email": "not-an-email",
                "from_name": "n",
                "rate_per_minute": "1",
                "rate_per_day": "100",
                "per_user_daily_limit": "50",
            },
        )
        self.assertEqual(resp.status_code, 302)
        acc = GlobalMailAccount.load()
        self.assertNotEqual(acc.from_email, "not-an-email")

    def test_save_config_denied_for_non_admin(self):
        regular = User.objects.create_user(
            username="regular",
            email="regular@example.com",
            role=User.Role.MANAGER,
        )
        self.client.force_login(regular)
        resp = self.client.post(
            "/admin/mail/setup/save-config/",
            {
                "smtp_host": "hack.example.com",
                "smtp_port": "587",
                "smtp_username": "h",
                "from_email": "h@h.h",
                "from_name": "H",
                "rate_per_minute": "1",
                "rate_per_day": "100",
                "per_user_daily_limit": "50",
            },
        )
        self.assertEqual(resp.status_code, 302)
        acc = GlobalMailAccount.load()
        self.assertNotEqual(acc.smtp_host, "hack.example.com")


@override_settings(
    SECURE_SSL_REDIRECT=False,
    SECURE_HSTS_SECONDS=0,
    SESSION_COOKIE_SECURE=False,
)
class MailToggleEnabledTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            username="toggle_admin",
            email="admin@example.com",
        )
        GlobalMailAccount.objects.filter(id=1).delete()
        self.acc = GlobalMailAccount.load()
        # Сохраняем валидный пароль — чтобы Fernet расшифровка работала.
        self.acc.set_password("valid_smtp_password_for_tests")
        self.acc.save()
        self.client.force_login(self.admin)

    def test_toggle_turns_on_when_fernet_valid(self):
        self.assertFalse(self.acc.is_enabled)
        resp = self.client.post("/admin/mail/setup/toggle-enabled/")
        self.assertEqual(resp.status_code, 302)
        self.acc.refresh_from_db()
        self.assertTrue(self.acc.is_enabled)

    def test_toggle_turns_off_when_enabled(self):
        self.acc.is_enabled = True
        self.acc.save(update_fields=["is_enabled"])
        self.client.post("/admin/mail/setup/toggle-enabled/")
        self.acc.refresh_from_db()
        self.assertFalse(self.acc.is_enabled)

    def test_toggle_denied_when_fernet_invalid(self):
        # Эмулируем InvalidToken: мусор в smtp_password_enc.
        self.acc.smtp_password_enc = "definitely-not-fernet-ciphertext"
        self.acc.is_enabled = False
        self.acc.save(update_fields=["smtp_password_enc", "is_enabled"])

        resp = self.client.post("/admin/mail/setup/toggle-enabled/")
        self.assertEqual(resp.status_code, 302)
        self.acc.refresh_from_db()
        self.assertFalse(self.acc.is_enabled)  # не включилось

    def test_toggle_denied_for_non_admin(self):
        regular = User.objects.create_user(
            username="toggle_regular",
            email="r@e.r",
            role=User.Role.MANAGER,
        )
        self.client.force_login(regular)
        resp = self.client.post("/admin/mail/setup/toggle-enabled/")
        self.assertEqual(resp.status_code, 302)
        self.acc.refresh_from_db()
        self.assertFalse(self.acc.is_enabled)
