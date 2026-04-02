"""
Тесты для mailer/smtp_sender.py:
_sanitize_header, _inline_data_images, build_message, format_smtp_error.
send_via_smtp и open_smtp_connection не тестируются напрямую (требуют реальный SMTP).
"""
from __future__ import annotations

import base64
import smtplib
from unittest.mock import MagicMock, patch
from email.message import EmailMessage

from django.test import TestCase

from mailer.smtp_sender import (
    _sanitize_header,
    _inline_data_images,
    build_message,
    format_smtp_error,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_account(**kwargs):
    """Минимальный mock-аккаунт для build_message."""
    acc = MagicMock()
    acc.smtp_host = kwargs.get("smtp_host", "mail.example.com")
    acc.smtp_port = kwargs.get("smtp_port", 587)
    acc.smtp_username = kwargs.get("smtp_username", "user@example.com")
    acc.from_email = kwargs.get("from_email", "user@example.com")
    acc.from_name = kwargs.get("from_name", "Test User")
    acc.reply_to = kwargs.get("reply_to", "")
    acc.is_enabled = kwargs.get("is_enabled", True)
    acc.use_starttls = kwargs.get("use_starttls", True)
    acc.get_password.return_value = kwargs.get("password", "secret")
    return acc


def _tiny_png_b64() -> str:
    """Минимальный PNG в base64 (1x1 пиксель)."""
    return (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
    )


# ---------------------------------------------------------------------------
# _sanitize_header
# ---------------------------------------------------------------------------

class SanitizeHeaderTest(TestCase):

    def test_removes_cr(self):
        self.assertEqual(_sanitize_header("hello\rworld"), "helloworld")

    def test_removes_lf(self):
        self.assertEqual(_sanitize_header("hello\nworld"), "helloworld")

    def test_removes_crlf(self):
        self.assertEqual(_sanitize_header("Subject\r\nInjected: header"), "SubjectInjected: header")

    def test_passthrough_clean(self):
        self.assertEqual(_sanitize_header("Clean header value"), "Clean header value")

    def test_empty_string(self):
        self.assertEqual(_sanitize_header(""), "")


# ---------------------------------------------------------------------------
# _inline_data_images
# ---------------------------------------------------------------------------

class InlineDataImagesTest(TestCase):

    def _img_tag(self, b64: str, mime: str = "png") -> str:
        return f'<img src="data:image/{mime};base64,{b64}">'

    def test_empty_html_returns_unchanged(self):
        html, imgs = _inline_data_images("")
        self.assertEqual(html, "")
        self.assertEqual(imgs, [])

    def test_no_data_images_unchanged(self):
        html = '<img src="/static/logo.png">'
        result, imgs = _inline_data_images(html)
        self.assertEqual(result, html)
        self.assertEqual(imgs, [])

    def test_converts_data_image_to_cid(self):
        b64 = _tiny_png_b64()
        html, imgs = _inline_data_images(self._img_tag(b64))
        self.assertEqual(len(imgs), 1)
        self.assertIn("cid:", html)
        self.assertNotIn("data:image", html)

    def test_cid_tuple_structure(self):
        b64 = _tiny_png_b64()
        _, imgs = _inline_data_images(self._img_tag(b64))
        cid, raw, subtype = imgs[0]
        self.assertIsInstance(cid, str)
        self.assertIsInstance(raw, bytes)
        self.assertEqual(subtype, "png")

    def test_limit_30_images(self):
        b64 = _tiny_png_b64()
        html = "".join(self._img_tag(b64) for _ in range(35))
        _, imgs = _inline_data_images(html)
        self.assertLessEqual(len(imgs), 30)

    def test_invalid_base64_skipped(self):
        html = '<img src="data:image/png;base64,NOT_VALID!!!">'
        result, imgs = _inline_data_images(html)
        # Не должно упасть; невалидный тег остаётся или пропускается
        self.assertIsInstance(result, str)

    def test_single_quotes(self):
        b64 = _tiny_png_b64()
        html = f"<img src='data:image/png;base64,{b64}'>"
        _, imgs = _inline_data_images(html)
        self.assertEqual(len(imgs), 1)


# ---------------------------------------------------------------------------
# build_message
# ---------------------------------------------------------------------------

class BuildMessageTest(TestCase):

    def setUp(self):
        self.account = _make_account()

    def _build(self, **kwargs) -> EmailMessage:
        defaults = dict(
            account=self.account,
            to_email="recipient@example.com",
            subject="Тестовое письмо",
            body_text="Текст письма",
            body_html="<p>HTML</p>",
        )
        defaults.update(kwargs)
        return build_message(**defaults)

    def test_returns_email_message(self):
        msg = self._build()
        self.assertIsInstance(msg, EmailMessage)

    def test_subject_set(self):
        msg = self._build(subject="Hello World")
        self.assertEqual(msg["Subject"], "Hello World")

    def test_to_set(self):
        msg = self._build(to_email="test@example.com")
        self.assertEqual(msg["To"], "test@example.com")

    def test_from_uses_account_defaults(self):
        msg = self._build(from_email=None, from_name=None)
        self.assertIn("user@example.com", msg["From"])

    def test_from_name_override(self):
        msg = self._build(from_name="Иван Иванов", from_email="ivan@example.com")
        self.assertIn("Иван Иванов", msg["From"])

    def test_reply_to_set(self):
        msg = self._build(reply_to="reply@example.com")
        self.assertEqual(msg["Reply-To"], "reply@example.com")

    def test_message_id_present(self):
        msg = self._build()
        self.assertIsNotNone(msg["Message-ID"])

    def test_subject_header_injection_sanitized(self):
        msg = self._build(subject="Evil\r\nInjected: header")
        self.assertNotIn("\r", msg["Subject"])
        self.assertNotIn("\n", msg["Subject"])

    def test_plain_text_only(self):
        msg = self._build(body_html="")
        content_type = msg.get_content_type()
        self.assertEqual(content_type, "text/plain")

    def test_html_creates_alternative(self):
        msg = self._build(body_html="<p>HTML</p>", body_text="текст")
        # Должен быть multipart
        self.assertTrue(msg.is_multipart())

    def test_attachment_content_bytes(self):
        msg = self._build(
            attachment_content=b"fake pdf content",
            attachment_filename="report.pdf",
        )
        attachments = list(msg.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "report.pdf")

    def test_data_image_inline(self):
        b64 = _tiny_png_b64()
        html = f'<p>Фото: <img src="data:image/png;base64,{b64}"></p>'
        msg = self._build(body_html=html)
        # Message должен быть multipart и содержать inline изображение
        self.assertTrue(msg.is_multipart())


# ---------------------------------------------------------------------------
# format_smtp_error
# ---------------------------------------------------------------------------

class FormatSmtpErrorTest(TestCase):

    def setUp(self):
        self.account = _make_account()

    def _fmt(self, exc):
        return format_smtp_error(exc, self.account)

    def test_authentication_535(self):
        err = smtplib.SMTPAuthenticationError(535, b"authentication failed")
        result = self._fmt(err)
        self.assertIn("аутентификации", result.lower())

    def test_connection_refused(self):
        err = ConnectionRefusedError("Connection refused")
        result = self._fmt(err)
        self.assertIn(self.account.smtp_host, result)

    def test_timeout(self):
        err = OSError("timed out")
        result = self._fmt(err)
        self.assertIn("аймаут", result.lower())

    def test_recipients_refused(self):
        err = smtplib.SMTPRecipientsRefused({"user@example.com": (550, b"No such user")})
        result = self._fmt(err)
        self.assertIn("получатель", result.lower())

    def test_smtp_data_error(self):
        err = smtplib.SMTPDataError(552, b"Mailbox full")
        result = self._fmt(err)
        self.assertIn("данных", result.lower())

    def test_smtp_known_code_550(self):
        err = smtplib.SMTPException("550 5.1.1 User unknown")
        result = self._fmt(err)
        self.assertIn("550", result)

    def test_runtime_error_passthrough(self):
        err = RuntimeError("Понятное сообщение")
        result = self._fmt(err)
        self.assertEqual(result, "Понятное сообщение")

    def test_unknown_error_fallback(self):
        err = ValueError("something unexpected")
        result = self._fmt(err)
        self.assertIn("Ошибка", result)

    def test_starttls_error(self):
        err = smtplib.SMTPException("STARTTLS not supported")
        result = self._fmt(err)
        self.assertIn("STARTTLS", result)
