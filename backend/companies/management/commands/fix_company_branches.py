"""
Команда для проверки и исправления несоответствий филиалов компаний.

Проверяет все компании, у которых есть ответственный, и если филиал компании
не совпадает с филиалом ответственного, заменяет филиал компании на филиал ответственного.
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from companies.models import Company
from accounts.models import User, Branch


class Command(BaseCommand):
    help = (
        "Проверка и исправление несоответствий филиалов компаний.\n"
        "Для каждой компании проверяет, совпадает ли филиал компании с филиалом ответственного.\n"
        "Если не совпадает, заменяет филиал компании на филиал ответственного."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Только показать, что будет исправлено, без выполнения изменений",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="Ограничить количество проверяемых компаний (для тестирования, 0 = все)",
        )
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Показать детальную информацию о каждой исправляемой компании",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        limit = options.get("limit", 0)
        verbose = options.get("verbose", False)

        self.stdout.write(self.style.SUCCESS("=" * 80))
        self.stdout.write(self.style.SUCCESS("Проверка несоответствий филиалов компаний"))
        self.stdout.write(self.style.SUCCESS("=" * 80))
        
        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  РЕЖИМ ПРОВЕРКИ (dry-run) - изменения не будут сохранены\n"))
        else:
            self.stdout.write(self.style.WARNING("\n⚠️  РЕЖИМ ИСПРАВЛЕНИЯ - изменения будут сохранены в БД\n"))

        # Получаем все компании с ответственным
        companies_qs = Company.objects.filter(responsible__isnull=False).select_related("responsible", "branch", "responsible__branch")
        
        total_count = companies_qs.count()
        self.stdout.write(f"\nВсего компаний с ответственным: {total_count}")
        
        if limit > 0:
            companies_qs = companies_qs[:limit]
            self.stdout.write(self.style.WARNING(f"Ограничение: проверяем только первые {limit} компаний"))

        # Собираем компании с несоответствиями
        mismatches = []
        checked = 0
        
        for company in companies_qs:
            checked += 1
            
            if company.responsible is None:
                continue
            
            company_branch = company.branch
            responsible_branch = company.responsible.branch
            
            # Пропускаем, если у ответственного нет филиала
            if responsible_branch is None:
                if verbose:
                    self.stdout.write(
                        self.style.WARNING(
                            f"  [{checked}/{total_count}] {company.name[:50]:<50} | "
                            f"Ответственный: {company.responsible} (без филиала) | Пропущено"
                        )
                    )
                continue
            
            # Проверяем несоответствие
            if company_branch != responsible_branch:
                mismatches.append({
                    "company": company,
                    "old_branch": company_branch,
                    "new_branch": responsible_branch,
                    "responsible": company.responsible,
                })
                
                if verbose:
                    old_branch_name = company_branch.name if company_branch else "—"
                    self.stdout.write(
                        self.style.ERROR(
                            f"  [{checked}/{total_count}] {company.name[:50]:<50} | "
                            f"Филиал компании: {old_branch_name} → Филиал ответственного: {responsible_branch.name} | "
                            f"Ответственный: {company.responsible}"
                        )
                    )
            elif verbose and checked % 100 == 0:
                # Показываем прогресс каждые 100 компаний
                self.stdout.write(f"  Проверено: {checked}/{total_count}...")

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS(f"Проверка завершена. Проверено компаний: {checked}"))
        self.stdout.write(self.style.ERROR(f"Найдено несоответствий: {len(mismatches)}"))
        self.stdout.write("=" * 80)

        if len(mismatches) == 0:
            self.stdout.write(self.style.SUCCESS("\n✅ Все филиалы соответствуют филиалам ответственных. Исправления не требуются."))
            return

        # Показываем статистику
        self.stdout.write("\nСтатистика по филиалам:")
        branch_stats = {}
        for item in mismatches:
            old_branch_name = item["old_branch"].name if item["old_branch"] else "—"
            new_branch_name = item["new_branch"].name
            key = f"{old_branch_name} → {new_branch_name}"
            branch_stats[key] = branch_stats.get(key, 0) + 1
        
        for change, count in sorted(branch_stats.items(), key=lambda x: -x[1]):
            self.stdout.write(f"  {change}: {count} компаний")

        if dry_run:
            self.stdout.write(self.style.WARNING("\n⚠️  Это был режим проверки. Для применения изменений запустите команду без --dry-run"))
            return

        # Применяем исправления
        self.stdout.write(self.style.WARNING("\nПрименение исправлений..."))
        
        updated_count = 0
        with transaction.atomic():
            for item in mismatches:
                company = item["company"]
                old_branch = company.branch
                new_branch = item["new_branch"]
                
                company.branch = new_branch
                company.save(update_fields=["branch", "updated_at"])
                updated_count += 1
                
                if verbose:
                    old_branch_name = old_branch.name if old_branch else "—"
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  ✓ Исправлено: {company.name[:50]:<50} | "
                            f"{old_branch_name} → {new_branch.name}"
                        )
                    )

        self.stdout.write("\n" + "=" * 80)
        self.stdout.write(self.style.SUCCESS(f"✅ Исправлено компаний: {updated_count}"))
        self.stdout.write("=" * 80)
