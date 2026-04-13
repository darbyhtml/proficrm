"""Загрузка справочника BranchRegion из fixture JSON."""

import json
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from accounts.models import Branch, BranchRegion


class Command(BaseCommand):
    help = "Загрузка справочника BranchRegion из fixture (Положение 2025-2026)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--file",
            default="accounts/fixtures/branch_regions_2025_2026.json",
            help="Путь к fixture-файлу (относительно BASE_DIR или абсолютный)",
        )
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Очистить существующие BranchRegion перед загрузкой",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = Path(options["file"])
        if not path.is_absolute():
            path = Path(settings.BASE_DIR) / path

        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        if options["flush"]:
            BranchRegion.objects.all().delete()
            self.stdout.write("Очищены существующие регионы.")

        branches = {b.code: b for b in Branch.objects.all()}
        created = 0
        skipped = 0

        for idx, item in enumerate(data):
            code = item["branch_code"]
            region_name = item["region_name"]
            is_pool = item.get("is_common_pool", False)

            if code == "common":
                # общий пул — запись для каждого реального филиала
                for branch in branches.values():
                    obj, was_created = BranchRegion.objects.get_or_create(
                        branch=branch,
                        region_name=region_name,
                        defaults={"is_common_pool": True, "ordering": idx},
                    )
                    if was_created:
                        created += 1
                    else:
                        skipped += 1
            else:
                branch = branches.get(code)
                if branch is None:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Филиал '{code}' не найден, пропущен регион '{region_name}'"
                        )
                    )
                    skipped += 1
                    continue
                obj, was_created = BranchRegion.objects.get_or_create(
                    branch=branch,
                    region_name=region_name,
                    defaults={"is_common_pool": is_pool, "ordering": idx},
                )
                if was_created:
                    created += 1
                else:
                    skipped += 1

        self.stdout.write(
            self.style.SUCCESS(f"Готово. Создано: {created}, пропущено: {skipped}")
        )
