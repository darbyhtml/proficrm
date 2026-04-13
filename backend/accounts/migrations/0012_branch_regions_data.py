"""Загрузка регионов из fixture при миграции."""

from django.db import migrations


def load_regions(apps, schema_editor):
    # Прямой вызов ORM через apps.get_model (безопасно при миграции)
    import json
    from pathlib import Path
    from django.conf import settings

    Branch = apps.get_model("accounts", "Branch")
    BranchRegion = apps.get_model("accounts", "BranchRegion")

    fixture = Path(settings.BASE_DIR) / "accounts" / "fixtures" / "branch_regions_2025_2026.json"
    if not fixture.exists():
        return  # если fixture нет — пропускаем (для чистых тестовых БД)

    with fixture.open(encoding="utf-8") as f:
        data = json.load(f)

    BranchRegion.objects.all().delete()
    branches = {b.code: b for b in Branch.objects.all()}

    for idx, item in enumerate(data):
        code = item["branch_code"]
        region_name = item["region_name"]
        is_pool = item.get("is_common_pool", False)

        if code == "common":
            for branch in branches.values():
                BranchRegion.objects.get_or_create(
                    branch=branch,
                    region_name=region_name,
                    defaults={"is_common_pool": True, "ordering": idx},
                )
        else:
            branch = branches.get(code)
            if branch is None:
                continue
            BranchRegion.objects.get_or_create(
                branch=branch,
                region_name=region_name,
                defaults={"is_common_pool": is_pool, "ordering": idx},
            )


def unload_regions(apps, schema_editor):
    BranchRegion = apps.get_model("accounts", "BranchRegion")
    BranchRegion.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0011_branch_region"),
    ]

    operations = [
        migrations.RunPython(load_regions, unload_regions),
    ]
