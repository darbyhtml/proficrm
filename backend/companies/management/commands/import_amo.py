from django.core.management.base import BaseCommand, CommandError

from companies.importer import import_amo_csv


class Command(BaseCommand):
    help = "Импорт компаний/контактов из экспорта amoCRM (CSV; XLSX лучше предварительно сохранить как CSV)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=True, help="Путь к CSV файлу экспорта")
        parser.add_argument("--encoding", default="utf-8-sig", help="Кодировка CSV (по умолчанию utf-8-sig)")
        parser.add_argument("--dry-run", action="store_true", help="Не писать в БД, только показать статистику")
        parser.add_argument("--companies-only", action="store_true", help="Импортировать только компании (без контактов)")
        parser.add_argument("--limit-companies", type=int, default=0, help="Ограничить импорт компаний (например 20)")

    def handle(self, *args, **options):
        csv_path = options["csv"]
        enc = options["encoding"]
        dry_run = bool(options["dry_run"])
        companies_only = bool(options["companies_only"])
        limit_companies = int(options["limit_companies"] or 0)
        if companies_only and limit_companies <= 0:
            limit_companies = 20

        try:
            res = import_amo_csv(
                csv_path=csv_path,
                encoding=enc,
                dry_run=dry_run,
                companies_only=companies_only,
                limit_companies=limit_companies,
            )
        except FileNotFoundError:
            raise CommandError(f"CSV not found: {csv_path}")

        self.stdout.write(
            self.style.SUCCESS(
                f"Done. companies +{res.created_companies} ~{res.updated_companies}, "
                f"company_rows={res.company_rows}, skipped_rows={res.skipped_rows}, "
                f"companies_only={companies_only}, limit_companies={limit_companies}, dry_run={dry_run}"
            )
        )


