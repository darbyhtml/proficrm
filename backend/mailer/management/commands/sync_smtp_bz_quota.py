"""
Django management command для ручной синхронизации квоты smtp.bz.
Полезно для тестирования и отладки.
"""
import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from mailer.models import GlobalMailAccount, SmtpBzQuota
from mailer.smtp_bz_api import get_quota_info

logger = logging.getLogger(__name__)


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
        
        try:
            quota_info = get_quota_info(cfg.smtp_bz_api_key)
            
            if not quota_info:
                quota = SmtpBzQuota.load()
                quota.sync_error = "Не удалось получить данные через API. Проверьте правильность API ключа в личном кабинете smtp.bz и убедитесь, что API включен для вашего аккаунта."
                quota.save(update_fields=["sync_error", "updated_at"])
                self.stdout.write(self.style.ERROR("❌ Ошибка синхронизации"))
                self.stdout.write(self.style.ERROR(f"   Ошибка: {quota.sync_error}"))
                self.stdout.write("   Проверьте:")
                self.stdout.write("   1. Правильность API ключа в личном кабинете smtp.bz")
                self.stdout.write("   2. Что API включен для вашего аккаунта")
                return
            
            # Обновляем информацию о квоте
            quota = SmtpBzQuota.load()
            quota.tariff_name = quota_info.get("tariff_name", "")
            quota.tariff_renewal_date = quota_info.get("tariff_renewal_date")
            quota.emails_available = quota_info.get("emails_available", 0)
            quota.emails_limit = quota_info.get("emails_limit", 0)
            quota.sent_per_hour = quota_info.get("sent_per_hour", 0)
            quota.max_per_hour = quota_info.get("max_per_hour", 100)
            quota.last_synced_at = timezone.now()
            quota.sync_error = ""
            quota.save()
            
            self.stdout.write(self.style.SUCCESS("✅ Синхронизация успешна!"))
            self.stdout.write(f"   Тариф: {quota.tariff_name or '—'}")
            self.stdout.write(f"   Доступно писем: {quota.emails_available} / {quota.emails_limit}")
            self.stdout.write(f"   Лимит в час: {quota.max_per_hour}")
            if quota.tariff_renewal_date:
                self.stdout.write(f"   Дата продления: {quota.tariff_renewal_date}")
            self.stdout.write(f"   Последняя синхронизация: {quota.last_synced_at}")
            
        except Exception as e:
            logger.error(f"Error syncing smtp.bz quota: {e}", exc_info=True)
            quota = SmtpBzQuota.load()
            quota.sync_error = str(e)
            quota.save(update_fields=["sync_error", "updated_at"])
            self.stdout.write(self.style.ERROR("❌ Ошибка синхронизации"))
            self.stdout.write(self.style.ERROR(f"   Ошибка: {str(e)}"))
            self.stdout.write("   Проверьте логи для подробностей")
