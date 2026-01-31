"""
Создание пользователя с ролью «Администратор» или смена роли существующего пользователя на Администратор.

Примеры:
  python manage.py create_admin_user admin --email admin@example.com --password secret
  python manage.py create_admin_user admin --email admin@example.com
  python manage.py create_admin_user --promote manager1
"""
import secrets
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

User = get_user_model()


class Command(BaseCommand):
    help = "Создать пользователя с ролью «Администратор» или повысить существующего до Администратора."

    def add_arguments(self, parser):
        parser.add_argument(
            "username",
            nargs="?",
            default=None,
            help="Логин пользователя (для создания или для --promote)",
        )
        parser.add_argument("--email", default="", help="Email (при создании)")
        parser.add_argument("--password", default="", help="Пароль (если пусто — генерируется и выводится)")
        parser.add_argument(
            "--promote",
            action="store_true",
            help="Повысить существующего пользователя до роли Администратор",
        )
        parser.add_argument("--first-name", dest="first_name", default="", help="Имя (при создании)")
        parser.add_argument("--last-name", dest="last_name", default="Администратор", help="Фамилия (при создании)")

    def handle(self, *args, **options):
        username = options.get("username")
        promote = options.get("promote")

        if not username:
            self.stderr.write("Укажите username: create_admin_user <username> [--email ...] [--password ...] или --promote <username>")
            return

        if promote:
            try:
                user = User.objects.get(username=username)
            except User.DoesNotExist:
                self.stderr.write(f"Пользователь с логином «{username}» не найден.")
                return
            user.role = User.Role.ADMIN
            user.is_staff = True
            user.save(update_fields=["role", "is_staff"])
            self.stdout.write(self.style.SUCCESS(f"Пользователь «{username}» теперь с ролью «Администратор»."))
            return

        # Создание нового пользователя
        if User.objects.filter(username=username).exists():
            self.stderr.write(f"Пользователь «{username}» уже существует. Используйте --promote, чтобы сделать его администратором.")
            return

        password = (options.get("password") or "").strip()
        if not password:
            password = secrets.token_urlsafe(16)
            self.stdout.write(self.style.WARNING(f"Пароль сгенерирован (сохраните его): {password}"))

        user = User.objects.create_user(
            username=username,
            email=options.get("email") or f"{username}@localhost",
            password=password,
            first_name=options.get("first_name") or username,
            last_name=options.get("last_name") or "Администратор",
            role=User.Role.ADMIN,
            is_staff=True,
            is_active=True,
        )
        self.stdout.write(self.style.SUCCESS(f"Создан пользователь «{username}» с ролью «Администратор»."))
        if not options.get("password"):
            self.stdout.write(self.style.WARNING(f"Пароль для входа: {password}"))
