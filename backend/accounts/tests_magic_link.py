"""
Тесты для Magic Link Authentication.
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone
from datetime import timedelta
import hashlib

from accounts.models import MagicLinkToken

User = get_user_model()


class MagicLinkTokenTestCase(TestCase):
    """Тесты для модели MagicLinkToken."""

    def setUp(self):
        """Создаём тестовые данные."""
        self.admin = User.objects.create_user(
            username="admin",
            password="test123",
            role=User.Role.ADMIN,
            first_name="Админ",
            last_name="Системы",
        )
        self.user = User.objects.create_user(
            username="user1",
            password="test123",
            role=User.Role.MANAGER,
            first_name="Пользователь",
            last_name="Один",
        )

    def test_generate_token(self):
        """Генерация токена создаёт уникальный хэш."""
        token1, hash1 = MagicLinkToken.generate_token()
        token2, hash2 = MagicLinkToken.generate_token()
        
        self.assertNotEqual(token1, token2)
        self.assertNotEqual(hash1, hash2)
        self.assertEqual(len(hash1), 64)  # SHA256 hex = 64 символа

    def test_create_for_user(self):
        """Создание токена для пользователя."""
        magic_link, plain_token = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        
        self.assertEqual(magic_link.user, self.user)
        self.assertEqual(magic_link.created_by, self.admin)
        self.assertIsNone(magic_link.used_at)
        self.assertLess(timezone.now(), magic_link.expires_at)
        self.assertLessEqual(
            (magic_link.expires_at - timezone.now()).total_seconds(),
            30 * 60 + 5  # 30 минут + небольшой запас
        )
        
        # Проверяем, что хэш соответствует токену
        expected_hash = hashlib.sha256(plain_token.encode()).hexdigest()
        self.assertEqual(magic_link.token_hash, expected_hash)

    def test_is_valid_fresh_token(self):
        """Свежий токен валиден."""
        magic_link, _ = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        self.assertTrue(magic_link.is_valid())

    def test_is_valid_expired_token(self):
        """Истёкший токен невалиден."""
        magic_link, _ = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        magic_link.expires_at = timezone.now() - timedelta(minutes=1)
        magic_link.save()
        self.assertFalse(magic_link.is_valid())

    def test_is_valid_used_token(self):
        """Использованный токен невалиден."""
        magic_link, _ = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        magic_link.mark_as_used(ip_address="127.0.0.1", user_agent="test")
        self.assertFalse(magic_link.is_valid())

    def test_mark_as_used(self):
        """Пометка токена как использованного."""
        magic_link, _ = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        
        self.assertIsNone(magic_link.used_at)
        self.assertIsNone(magic_link.ip_address)
        self.assertEqual(magic_link.user_agent, "")
        
        magic_link.mark_as_used(ip_address="192.168.1.1", user_agent="Mozilla/5.0")
        
        magic_link.refresh_from_db()
        self.assertIsNotNone(magic_link.used_at)
        self.assertEqual(magic_link.ip_address, "192.168.1.1")
        self.assertEqual(magic_link.user_agent, "Mozilla/5.0")


class MagicLinkLoginTestCase(TestCase):
    """Тесты для входа по magic link."""

    def setUp(self):
        """Создаём тестовые данные."""
        self.admin = User.objects.create_user(
            username="admin",
            password="test123",
            role=User.Role.ADMIN,
        )
        self.user = User.objects.create_user(
            username="user1",
            password="test123",
            role=User.Role.MANAGER,
        )

    def test_magic_link_login_success(self):
        """Успешный вход по валидному токену."""
        magic_link, plain_token = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        token_id = magic_link.id  # Сохраняем ID для последующей проверки
        
        # Используем Client для полного тестирования
        from django.test import Client
        from django.db import transaction
        client = Client()
        
        # Делаем запрос без follow, чтобы view выполнился полностью
        response = client.get(f"/auth/magic/{plain_token}/", follow=False)
        
        # Должен быть редирект на dashboard (302 или 301)
        self.assertIn(response.status_code, [301, 302], 
                     f"Ожидался редирект, получен статус {response.status_code}")
        
        # Важно: в тестах Django использует транзакции, но изменения должны быть видны
        # Проверяем токен сразу после запроса
        # Используем select_for_update() чтобы получить актуальные данные
        with transaction.atomic():
            # Принудительно коммитим транзакцию, если нужно
            transaction.commit()
            magic_link_after = MagicLinkToken.objects.select_for_update().get(id=token_id)
        
        # Если used_at всё ещё None, возможно проблема в том, что view не выполнился
        # Проверяем альтернативный способ - через refresh_from_db на исходном объекте
        if magic_link_after.used_at is None:
            # Пробуем получить объект заново без select_for_update
            magic_link_after = MagicLinkToken.objects.get(id=token_id)
        
        self.assertIsNotNone(magic_link_after.used_at, 
                            f"Токен должен быть помечен как использованный после входа. "
                            f"Текущее значение used_at: {magic_link_after.used_at}, "
                            f"ip_address: {magic_link_after.ip_address}, "
                            f"user_agent: {magic_link_after.user_agent}. "
                            f"Возможно, view не выполнился полностью из-за редиректа.")
        
        # Дополнительно проверяем, что пользователь залогинен после редиректа
        # Используем follow=True для проверки, что сессия создана
        response_follow = client.get("/", follow=True)
        # После входа должен быть доступ к главной странице (не редирект на /login/)
        # Если пользователь не залогинен, будет редирект на /login/
        if response_follow.redirect_chain:
            final_url = response_follow.redirect_chain[-1][0]
            self.assertNotIn("/login/", final_url, 
                            "Пользователь должен быть залогинен после успешного входа")

    def test_magic_link_login_invalid_token(self):
        """Невалидный токен не работает."""
        from django.test import Client
        client = Client()
        # Используем follow=True, чтобы следовать редиректам и получить финальный ответ
        response = client.get("/auth/magic/invalid-token-12345/", follow=True)
        
        # Должна быть ошибка 404 или 400 (после всех редиректов)
        self.assertIn(response.status_code, [400, 404])

    def test_magic_link_login_expired_token(self):
        """Истёкший токен не работает."""
        magic_link, plain_token = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        magic_link.expires_at = timezone.now() - timedelta(minutes=1)
        magic_link.save()
        
        from django.test import Client
        client = Client()
        # Используем follow=True, чтобы следовать редиректам и получить финальный ответ
        response = client.get(f"/auth/magic/{plain_token}/", follow=True)
        
        # Должна быть ошибка 400 (после всех редиректов)
        self.assertEqual(response.status_code, 400)

    def test_magic_link_login_used_token(self):
        """Использованный токен не работает повторно."""
        magic_link, plain_token = MagicLinkToken.create_for_user(
            user=self.user,
            created_by=self.admin,
            ttl_minutes=30,
        )
        magic_link.mark_as_used(ip_address="127.0.0.1", user_agent="test")
        
        from django.test import Client
        client = Client()
        # Используем follow=True, чтобы следовать редиректам и получить финальный ответ
        response = client.get(f"/auth/magic/{plain_token}/", follow=True)
        
        # Должна быть ошибка 400 (после всех редиректов)
        self.assertEqual(response.status_code, 400)
