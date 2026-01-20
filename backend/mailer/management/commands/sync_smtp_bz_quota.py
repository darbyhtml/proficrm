"""
Django management command для ручной синхронизации квоты smtp.bz.
Полезно для тестирования и отладки.
"""
from django.core.management.base import BaseCommand
from mailer.tasks import sync_smtp_bz_quota
from mailer.models import GlobalMailAccount, SmtpBzQuota


class Command(BaseCommand):
    help = "Синхронизирует информацию о тарифе и квоте smtp.bz через API"

    def handle(self, *args, **options):
        cfg = GlobalMailAccount.load()
        
        if not cfg.smtp_bz_api_key:
            self.stdout.write(self.style.ERROR("❌ API ключ smtp.bz не настроен"))
            self.stdout.write("   Установите API ключ в настройках SMTP (раздел 'Почта')")
            return
        
        self.stdout.write("🔄 Запуск синхронизации квоты smtp.bz...")
        self.stdout.write(f"   API ключ: {cfg.smtp_bz_api_key[:10]}...")
        
        # Запускаем синхронизацию синхронно (не через Celery)
        result = sync_smtp_bz_quota()
        
        quota = SmtpBzQuota.load()
        
        if result.get("status") == "success":
            self.stdout.write(self.style.SUCCESS("✅ Синхронизация успешна!"))
            self.stdout.write(f"   Тариф: {quota.tariff_name or '—'}")
            self.stdout.write(f"   Доступно писем: {quota.emails_available} / {quota.emails_limit}")
            self.stdout.write(f"   Лимит в час: {quota.max_per_hour}")
            if quota.tariff_renewal_date:
                self.stdout.write(f"   Дата продления: {quota.tariff_renewal_date}")
            self.stdout.write(f"   Последняя синхронизация: {quota.last_synced_at}")
        elif result.get("status") == "error":
            self.stdout.write(self.style.ERROR("❌ Ошибка синхронизации"))
            if quota.sync_error:
                self.stdout.write(self.style.ERROR(f"   Ошибка: {quota.sync_error}"))
            self.stdout.write("   Проверьте:")
            self.stdout.write("   1. Правильность API ключа в личном кабинете smtp.bz")
            self.stdout.write("   2. Что API включен для вашего аккаунта")
            self.stdout.write("   3. Логи для подробностей: docker-compose logs celery --tail=50")
        else:
            self.stdout.write(self.style.WARNING("⚠️ Синхронизация пропущена"))
            self.stdout.write(f"   Причина: {result.get('reason', 'неизвестно')}")
