"""
Команда для миграции старых пользователей на систему ключей доступа.

Разлогинивает всех пользователей и генерирует для них ключи доступа.
"""
from django.core.management.base import BaseCommand
from django.contrib.sessions.models import Session
from django.contrib.auth import SESSION_KEY
from django.utils import timezone
from accounts.models import User, MagicLinkToken


class Command(BaseCommand):
    help = "Миграция пользователей на систему ключей доступа: разлогинивает всех и генерирует ключи"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Показать, что будет сделано, без выполнения",
        )
        parser.add_argument(
            "--admin-user",
            type=str,
            help="Логин администратора, который будет указан как создатель ключей (по умолчанию первый ADMIN)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        admin_username = options.get("admin_user")

        # Находим администратора для создания ключей
        if admin_username:
            try:
                admin_user = User.objects.get(username=admin_username)
                if admin_user.role != User.Role.ADMIN and not admin_user.is_superuser:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Пользователь {admin_username} не является администратором. Используется первый найденный ADMIN."
                        )
                    )
                    admin_user = None
            except User.DoesNotExist:
                self.stdout.write(
                    self.style.WARNING(
                        f"Пользователь {admin_username} не найден. Используется первый найденный ADMIN."
                    )
                )
                admin_user = None
        else:
            admin_user = None

        if not admin_user:
            admin_user = User.objects.filter(
                role=User.Role.ADMIN, is_active=True
            ).first()
            if not admin_user:
                admin_user = User.objects.filter(is_superuser=True, is_active=True).first()

        if not admin_user:
            self.stdout.write(
                self.style.ERROR(
                    "Не найден администратор для создания ключей. Создайте администратора или укажите --admin-user."
                )
            )
            return

        self.stdout.write(f"Используется администратор: {admin_user}")

        # Разлогиниваем всех пользователей
        self.stdout.write("\n=== Разлогинивание пользователей ===")
        sessions_deleted = 0
        users_logged_out = set()

        if not dry_run:
            for session in Session.objects.filter(expire_date__gte=timezone.now()):
                session_data = session.get_decoded()
                user_id_from_session = session_data.get(SESSION_KEY)
                if user_id_from_session:
                    try:
                        user = User.objects.get(id=int(user_id_from_session))
                        if user.is_active:
                            session.delete()
                            sessions_deleted += 1
                            users_logged_out.add(user.id)
                    except (User.DoesNotExist, ValueError, TypeError):
                        pass

            self.stdout.write(
                self.style.SUCCESS(
                    f"Удалено сессий: {sessions_deleted}, разлогинено пользователей: {len(users_logged_out)}"
                )
            )
        else:
            # В dry-run режиме просто считаем
            for session in Session.objects.filter(expire_date__gte=timezone.now()):
                session_data = session.get_decoded()
                user_id_from_session = session_data.get(SESSION_KEY)
                if user_id_from_session:
                    try:
                        user = User.objects.get(id=int(user_id_from_session))
                        if user.is_active:
                            sessions_deleted += 1
                            users_logged_out.add(user.id)
                    except (User.DoesNotExist, ValueError, TypeError):
                        pass

            self.stdout.write(
                f"[DRY-RUN] Будет удалено сессий: {sessions_deleted}, будет разлогинено пользователей: {len(users_logged_out)}"
            )

        # Генерируем ключи доступа для всех активных пользователей
        self.stdout.write("\n=== Генерация ключей доступа ===")
        active_users = User.objects.filter(is_active=True).exclude(
            id=admin_user.id
        )  # Исключаем администратора, который создаёт ключи

        keys_created = 0
        for user in active_users:
            # Проверяем, есть ли уже активный ключ
            has_active_key = MagicLinkToken.objects.filter(
                user=user, used_at__isnull=True, expires_at__gt=timezone.now()
            ).exists()

            if has_active_key:
                self.stdout.write(
                    f"  Пропущен {user}: уже есть активный ключ доступа"
                )
                continue

            if not dry_run:
                # Устанавливаем неиспользуемый пароль
                user.set_unusable_password()
                user.save(update_fields=["password"])

                # Генерируем ключ доступа
                magic_link, plain_token = MagicLinkToken.create_for_user(
                    user=user, created_by=admin_user
                )
                keys_created += 1
                self.stdout.write(
                    self.style.SUCCESS(
                        f"  Создан ключ для {user} (истекает {magic_link.expires_at.strftime('%d.%m.%Y %H:%M')})"
                    )
                )
            else:
                keys_created += 1
                self.stdout.write(f"  [DRY-RUN] Будет создан ключ для {user}")

        if not dry_run:
            self.stdout.write(
                self.style.SUCCESS(f"\n✅ Миграция завершена. Создано ключей: {keys_created}")
            )
        else:
            self.stdout.write(
                f"\n[DRY-RUN] Будет создано ключей: {keys_created}"
            )
            self.stdout.write(
                self.style.WARNING(
                    "\nДля выполнения миграции запустите команду без флага --dry-run"
                )
            )
