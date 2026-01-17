"""
Django management command для очистки кэша dashboard.
"""
from django.core.management.base import BaseCommand
from django.core.cache import cache
from django.utils import timezone
from accounts.models import User


class Command(BaseCommand):
    help = 'Очищает кэш dashboard для всех пользователей или конкретного пользователя'

    def add_arguments(self, parser):
        parser.add_argument(
            '--user-id',
            type=int,
            help='ID пользователя для очистки кэша (если не указан, очищает для всех)',
        )

    def handle(self, *args, **options):
        user_id = options.get('user_id')
        
        if user_id:
            # Очищаем кэш для конкретного пользователя
            today = timezone.localdate(timezone.now())
            yesterday = today - timezone.timedelta(days=1)
            tomorrow = today + timezone.timedelta(days=1)
            
            cache_keys = [
                f"dashboard_{user_id}_{today.isoformat()}",
                f"dashboard_{user_id}_{yesterday.isoformat()}",
                f"dashboard_{user_id}_{tomorrow.isoformat()}",
            ]
            
            for key in cache_keys:
                cache.delete(key)
            
            self.stdout.write(
                self.style.SUCCESS(f'Кэш dashboard очищен для пользователя {user_id}')
            )
        else:
            # Очищаем весь кэш
            cache.clear()
            self.stdout.write(
                self.style.SUCCESS('Весь кэш очищен')
            )
