"""
Django management command для проверки и очистки получателей кампаний по сферам деятельности.
Проверяет, что все получатели соответствуют выбранным при генерации сферам, и удаляет несоответствующих.
"""
from __future__ import annotations

import logging
from django.core.management.base import BaseCommand
from mailer.models import Campaign, CampaignRecipient
from companies.models import Company

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Проверяет получателей кампаний на соответствие выбранным сферам и удаляет несоответствующих"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Режим проверки без удаления (показывает, что будет удалено)",
        )
        parser.add_argument(
            "--campaign-id",
            type=str,
            help="ID конкретной кампании для проверки (если не указан, проверяются все)",
        )
        parser.add_argument(
            "--fix-meta",
            action="store_true",
            help="Исправить некорректные данные в filter_meta (нормализовать формат сфер)",
        )

    def handle(self, *args, **options):
        dry_run = options.get("dry_run", False)
        campaign_id = options.get("campaign_id")
        fix_meta = options.get("fix_meta", False)

        self.stdout.write("🔍 Проверка получателей кампаний по сферам деятельности...")
        if dry_run:
            self.stdout.write(self.style.WARNING("⚠️  Режим проверки (dry-run) - изменения не будут сохранены"))

        # Получаем кампании для проверки
        campaigns_qs = Campaign.objects.filter(filter_meta__sphere__isnull=False).exclude(filter_meta__sphere=[])
        if campaign_id:
            campaigns_qs = campaigns_qs.filter(id=campaign_id)
            if not campaigns_qs.exists():
                self.stdout.write(self.style.ERROR(f"❌ Кампания с ID {campaign_id} не найдена или не имеет фильтра по сферам"))
                return

        campaigns = campaigns_qs.select_related("created_by")
        total_campaigns = campaigns.count()

        if total_campaigns == 0:
            self.stdout.write(self.style.WARNING("⚠️  Не найдено кампаний с фильтром по сферам"))
            return

        self.stdout.write(f"📊 Найдено кампаний для проверки: {total_campaigns}")
        self.stdout.write("")

        total_checked = 0
        total_removed = 0
        total_errors = 0

        for campaign in campaigns:
            self.stdout.write(f"📧 Кампания: {campaign.name} (ID: {campaign.id})")
            
            # Получаем выбранные сферы из filter_meta
            filter_meta = campaign.filter_meta or {}
            sphere_ids_raw = filter_meta.get("sphere", [])
            
            if not sphere_ids_raw:
                self.stdout.write(self.style.WARNING("   ⚠️  Нет выбранных сфер в filter_meta, пропускаем"))
                self.stdout.write("")
                continue

            # Нормализуем: обрабатываем разные форматы (список, строка, число)
            sphere_ids = []
            try:
                # Если это строка (например, "1,8" или "18")
                if isinstance(sphere_ids_raw, str):
                    # Пробуем разбить по запятой
                    if "," in sphere_ids_raw:
                        parts = sphere_ids_raw.split(",")
                        for part in parts:
                            part = part.strip()
                            if part:
                                sphere_ids.append(int(part))
                    else:
                        # Одна строка - преобразуем в число
                        sphere_ids.append(int(sphere_ids_raw.strip()))
                # Если это число (не список)
                elif isinstance(sphere_ids_raw, (int, float)):
                    sphere_ids.append(int(sphere_ids_raw))
                # Если это список
                elif isinstance(sphere_ids_raw, list):
                    for s in sphere_ids_raw:
                        if s is not None:
                            try:
                                sphere_ids.append(int(s))
                            except (ValueError, TypeError):
                                # Пропускаем невалидные значения
                                pass
                else:
                    self.stdout.write(self.style.ERROR(f"   ❌ Неожиданный тип данных для сфер: {type(sphere_ids_raw)}"))
                    total_errors += 1
                    self.stdout.write("")
                    continue
            except (ValueError, TypeError) as e:
                self.stdout.write(self.style.ERROR(f"   ❌ Ошибка при обработке ID сфер: {e}, raw: {sphere_ids_raw}"))
                total_errors += 1
                self.stdout.write("")
                continue

            if not sphere_ids:
                self.stdout.write(self.style.WARNING("   ⚠️  Нет валидных ID сфер после нормализации, пропускаем"))
                self.stdout.write(f"   Исходные данные: {sphere_ids_raw} (тип: {type(sphere_ids_raw)})")
                self.stdout.write("")
                continue

            self.stdout.write(f"   🎯 Выбранные сферы: {sphere_ids}")
            # Дополнительная информация для отладки
            if sphere_ids_raw != sphere_ids:
                self.stdout.write(self.style.WARNING(f"   ⚠️  Исходные данные были нормализованы: {sphere_ids_raw} -> {sphere_ids}"))
            
            # Если включен режим исправления и данные были нормализованы
            if fix_meta and sphere_ids_raw != sphere_ids:
                if not dry_run:
                    # Обновляем filter_meta с нормализованными данными
                    filter_meta["sphere"] = sphere_ids
                    campaign.filter_meta = filter_meta
                    campaign.save(update_fields=["filter_meta", "updated_at"])
                    self.stdout.write(self.style.SUCCESS("   ✅ filter_meta обновлен с нормализованными сферами"))
                else:
                    self.stdout.write(f"   (dry-run) filter_meta будет обновлен: {sphere_ids_raw} -> {sphere_ids}")

            # Получаем всех получателей кампании с компаниями
            recipients = CampaignRecipient.objects.filter(
                campaign=campaign,
                company_id__isnull=False
            ).select_related("campaign")

            recipients_count = recipients.count()
            self.stdout.write(f"   👥 Получателей с компаниями: {recipients_count}")

            if recipients_count == 0:
                self.stdout.write("")
                continue

            # Получаем ID всех компаний получателей
            company_ids = list(recipients.values_list("company_id", flat=True).distinct())
            
            # Получаем компании с их сферами (используем prefetch_related для оптимизации)
            companies_with_spheres = Company.objects.filter(
                id__in=company_ids
            ).prefetch_related("spheres")

            # Создаем словарь: company_id -> список ID сфер компании (как целые числа)
            company_spheres_map = {}
            for company in companies_with_spheres:
                # Получаем ID сфер как целые числа для корректного сравнения
                sphere_ids_list = list(company.spheres.values_list("id", flat=True))
                # Преобразуем UUID компании в строку для использования как ключ
                company_spheres_map[str(company.id)] = [int(sid) for sid in sphere_ids_list]

            # Проверяем каждого получателя
            recipients_to_remove = []
            checked_count = 0

            for recipient in recipients:
                checked_count += 1
                company_id_str = str(recipient.company_id) if recipient.company_id else None
                
                if not company_id_str:
                    # Получатель без компании - удаляем
                    recipients_to_remove.append(recipient)
                    continue
                
                if company_id_str not in company_spheres_map:
                    # Компания не найдена в результатах запроса - возможно, была удалена
                    # Или компания не имеет сфер (пустой список)
                    # Проверяем, есть ли компания в БД
                    try:
                        company = Company.objects.get(id=recipient.company_id)
                        company_sphere_ids = list(company.spheres.values_list("id", flat=True))
                        if not company_sphere_ids:
                            # Компания существует, но не имеет сфер - удаляем получателя
                            recipients_to_remove.append(recipient)
                            continue
                        # Если сферы есть, но их нет в map - обновляем map
                        company_spheres_map[company_id_str] = [int(sid) for sid in company_sphere_ids]
                    except Company.DoesNotExist:
                        # Компания удалена - удаляем получателя
                        recipients_to_remove.append(recipient)
                        continue

                company_sphere_ids = company_spheres_map[company_id_str]
                
                # Проверяем, есть ли хотя бы одна общая сфера (OR-логика)
                # Сравниваем как целые числа для надежности
                has_matching_sphere = any(int(sphere_id) in company_sphere_ids for sphere_id in sphere_ids)
                
                if not has_matching_sphere:
                    recipients_to_remove.append(recipient)

            total_checked += checked_count

            if recipients_to_remove:
                self.stdout.write(self.style.WARNING(f"   ⚠️  Найдено несоответствующих получателей: {len(recipients_to_remove)}"))
                
                if dry_run:
                    # В режиме dry-run показываем примеры
                    for i, recipient in enumerate(recipients_to_remove[:5]):
                        company_id_str = str(recipient.company_id) if recipient.company_id else None
                        company_sphere_ids = company_spheres_map.get(company_id_str, [])
                        # Показываем также выбранные сферы для сравнения
                        self.stdout.write(f"      - {recipient.email}")
                        self.stdout.write(f"        Компания: {recipient.company_id}")
                        self.stdout.write(f"        Сферы компании: {company_sphere_ids}")
                        self.stdout.write(f"        Выбранные сферы: {sphere_ids}")
                    if len(recipients_to_remove) > 5:
                        self.stdout.write(f"      ... и еще {len(recipients_to_remove) - 5} получателей")
                else:
                    # Удаляем получателей
                    removed_count = 0
                    for recipient in recipients_to_remove:
                        try:
                            recipient.delete()
                            removed_count += 1
                        except Exception as e:
                            logger.error(f"Error deleting recipient {recipient.id}: {e}")
                            total_errors += 1
                    
                    total_removed += removed_count
                    self.stdout.write(self.style.SUCCESS(f"   ✅ Удалено получателей: {removed_count}"))
            else:
                self.stdout.write(self.style.SUCCESS("   ✅ Все получатели соответствуют выбранным сферам"))

            self.stdout.write("")

        # Итоговая статистика
        self.stdout.write("=" * 60)
        self.stdout.write("📊 Итоговая статистика:")
        self.stdout.write(f"   Проверено кампаний: {total_campaigns}")
        self.stdout.write(f"   Проверено получателей: {total_checked}")
        
        if dry_run:
            self.stdout.write(self.style.WARNING(f"   ⚠️  Найдено несоответствующих получателей: {total_removed} (не удалено, т.к. dry-run)"))
            self.stdout.write(self.style.WARNING("   Запустите без --dry-run для удаления"))
        else:
            self.stdout.write(self.style.SUCCESS(f"   ✅ Удалено получателей: {total_removed}"))
        
        if total_errors > 0:
            self.stdout.write(self.style.ERROR(f"   ❌ Ошибок: {total_errors}"))

        self.stdout.write("=" * 60)
