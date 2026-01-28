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


# Словарь алиасов для нормализации названий регионов из amoCRM
REGION_ALIASES = {
    "Республика Башкирия": "Республика Башкортостан",
    "Башкирия": "Республика Башкортостан",
    "Башкортостан": "Республика Башкортостан",
    # Можно добавить другие частые несовпадения по мере обнаружения
}


def _normalize_region_name(label: str) -> str:
    """
    Нормализует название региона из amoCRM к стандартному названию в БД.
    """
    label = label.strip()
    # Сначала проверяем точное совпадение (с учётом регистра)
    if label in REGION_ALIASES:
        return REGION_ALIASES[label]
    # Проверяем без учёта регистра
    for alias, canonical in REGION_ALIASES.items():
        if alias.lower() == label.lower():
            return canonical
    return label


def _find_region_by_name(label: str) -> Region | None:
    """
    Находит регион по названию с учётом нормализации и алиасов.
    """
    # Сначала пробуем точное совпадение (case-insensitive)
    region = Region.objects.filter(name__iexact=label).first()
    if region:
        return region
    
    # Пробуем нормализованное название
    normalized = _normalize_region_name(label)
    if normalized != label:
        region = Region.objects.filter(name__iexact=normalized).first()
        if region:
            return region
    
    # Пробуем частичное совпадение (если label содержит часть названия региона)
    # Например, "ХМАО" -> "Ханты-Мансийский автономный округ — Югра"
    if len(label) < 10:  # Короткие названия могут быть аббревиатурами
        regions = Region.objects.filter(name__icontains=label)
        if regions.count() == 1:
            return regions.first()
    
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

        # Используем более надёжный фильтр: убираем зависимость от форматирования JSON
        # Функция _get_custom_values_from_raw_fields сама корректно найдёт field_id внутри цикла
        qs = Company.objects.filter(region__isnull=True).exclude(raw_fields__isnull=True)
        total = qs.count()
        if limit > 0:
            qs = qs[:limit]
        self.stdout.write(f"Найдено компаний-кандидатов: {total} (обрабатываем: {qs.count()})")

        updated = 0
        skipped_no_label = 0
        skipped_unknown = 0

        with transaction.atomic():
            for comp in qs.iterator():
                vals = _get_custom_values_from_raw_fields(comp.raw_fields, field_id=field_id)
                label = _first_text_value(vals)
                if not label:
                    skipped_no_label += 1
                    continue

                region = _find_region_by_name(label)
                if not region:
                    skipped_unknown += 1
                    if dry_run:
                        self.stdout.write(f"  ⚠️  {comp.name} (inn={comp.inn or '-'}) -> регион '{label}' не найден в БД")
                    continue

                self.stdout.write(f"  ✓ {comp.name} (inn={comp.inn or '-'}) -> {region.name}")
                if not dry_run:
                    comp.region = region
                    comp.save(update_fields=["region", "updated_at"])
                updated += 1

            if dry_run:
                transaction.set_rollback(True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Готово. Заполнено region для компаний: {updated}, "
                f"пропущено из-за отсутствия label в raw_fields: {skipped_no_label}, "
                f"пропущено из-за неизвестного региона: {skipped_unknown}."
            )
        )

