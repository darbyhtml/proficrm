"""
Backfill: заполняет Company.region из raw_fields amoCRM по указанному field_id.

Используется, если регионы уже были импортированы из amoCRM (raw_fields.amo/custom_fields_values),
но поле region в Company ещё не заполнено.
"""
from django.core.management.base import BaseCommand
from django.db import transaction

from companies.models import Company, Region
from ui.models import AmoApiConfig


def _get_custom_values_from_raw_fields(raw_fields, field_id: int) -> list:
    """
    Извлекает values (list of dicts с 'value') для указанного field_id из raw_fields.amo/custom_fields_values.
    """
    if not raw_fields or not isinstance(raw_fields, dict):
        return []
    for key in ("amo_api_last", "amo"):
        node = raw_fields.get(key)
        if not isinstance(node, dict):
            continue
        cfv = node.get("custom_fields_values") or []
        if not isinstance(cfv, list):
            continue
        for cf in cfv:
            if not isinstance(cf, dict):
                continue
            try:
                fid = int(cf.get("field_id") or 0)
            except (TypeError, ValueError):
                continue
            if fid != field_id:
                continue
            vals = cf.get("values") or []
            return vals if isinstance(vals, list) else []
    return []


def _first_text_value(values: list) -> str | None:
    for v in values:
        if isinstance(v, dict):
            s = str(v.get("value") or "").strip()
        else:
            s = str(v or "").strip()
        if s:
            return s
    return None


class Command(BaseCommand):
    help = (
        "Backfill: заполняет Company.region на основе raw_fields из amoCRM. "
        "Использует field_id из AmoApiConfig.region_custom_field_id, либо --field-id."
    )

    def add_arguments(self, parser):
        parser.add_argument("--field-id", type=int, default=0, help="ID кастомного поля региона в amoCRM (по умолчанию из настроек).")
        parser.add_argument("--limit", type=int, default=0, help="Максимум компаний (0 = все).")
        parser.add_argument("--dry-run", action="store_true", help="Только показать, без изменений в БД.")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        field_id = int(options.get("field_id") or 0)
        limit = int(options.get("limit") or 0)

        if not field_id:
            cfg = AmoApiConfig.load()
            field_id = int(getattr(cfg, "region_custom_field_id", 0) or 0)

        if not field_id:
            self.stdout.write(self.style.ERROR("Не указан field_id и не настроен region_custom_field_id в AmoApiConfig."))
            return

        self.stdout.write(f"Backfill Company.region из amoCRM (field_id={field_id})")
        if dry_run:
            self.stdout.write(self.style.WARNING("Режим --dry-run: изменения НЕ будут сохранены"))

        qs = Company.objects.filter(region__isnull=True, raw_fields__icontains=f'"field_id": {field_id}')
        total = qs.count()
        if limit > 0:
            qs = qs[:limit]
        self.stdout.write(f"Найдено компаний-кандидатов: {total} (обрабатываем: {qs.count()})")

        updated = 0
        skipped_unknown = 0

        with transaction.atomic():
            for comp in qs.iterator():
                vals = _get_custom_values_from_raw_fields(comp.raw_fields, field_id=field_id)
                label = _first_text_value(vals)
                if not label:
                    continue

                region = Region.objects.filter(name__iexact=label).first()
                if not region:
                    skipped_unknown += 1
                    continue

                self.stdout.write(f"  {comp.name} (inn={comp.inn or '-'}) -> {region.name}")
                if not dry_run:
                    comp.region = region
                    comp.save(update_fields=["region", "updated_at"])
                updated += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Заполнено region для компаний: {updated}, пропущено из-за неизвестного региона: {skipped_unknown}."
            )
        )

