"""
Backfill: разбирает поле 309609 «Список телефонов (Скайнет)» из raw_fields и создаёт CompanyPhone
с comment, начинающимся с SKYNET (например "SKYNET; временно не доступен").

Для компаний, у которых в raw_fields есть custom_fields_values с field_id=309609, но телефоны
ещё не разнесены в CompanyPhone (например, импорт был до появления логики Скайнет).
"""
from django.core.management.base import BaseCommand
from django.db.models import Max

from companies.models import Company, CompanyPhone
from ui.forms import _normalize_phone

# Поле «Список телефонов (Скайнет)» в AmoCRM
SKYNET_FIELD_ID = 309609


def _get_custom_values_from_raw_fields(raw_fields, field_id: int) -> list:
    """Извлекает values (список dict с 'value') для field_id из raw_fields."""
    if not raw_fields or not isinstance(raw_fields, dict):
        return []
    for key in ("amo_api_last", "amo"):
        node = raw_fields.get(key)
        if not isinstance(node, dict):
            continue
        cfv = node.get("custom_fields_values") or []
        if not isinstance(cfv, list):
            cfv = []
        for cf in cfv:
            if isinstance(cf, dict) and int(cf.get("field_id") or 0) == field_id:
                vals = cf.get("values") or []
                return vals if isinstance(vals, list) else []
    return []


def _texts_from_values(values: list) -> list[str]:
    """Из values (list of {value: ...}) получает список непустых строк."""
    out = []
    for v in values:
        if isinstance(v, dict):
            s = str(v.get("value") or "").strip()
        else:
            s = str(v).strip()
        if s:
            out.append(s)
    return out


class Command(BaseCommand):
    help = (
        "Backfill: парсит поле 309609 (Скайнет) из raw_fields и создаёт CompanyPhone с comment, "
        "начинающимся с SKYNET (например 'SKYNET; временно не доступен'). "
        "Используйте --dry-run для проверки."
    )

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Только показать, без записи в БД")
        parser.add_argument("--limit", type=int, default=0, help="Макс. компаний (0 = все)")

    def handle(self, *args, **options):
        from amocrm.migrate import parse_skynet_phones

        dry_run = options["dry_run"]
        limit = int(options.get("limit") or 0)

        self.stdout.write("Backfill Skynet (309609) -> CompanyPhone (comment=SKYNET)")
        if dry_run:
            self.stdout.write(self.style.WARNING("Режим --dry-run: изменения не сохраняются"))

        # Компании, у которых в raw_fields может быть 309609 (грубая выборка по подстроке)
        qs = Company.objects.filter(raw_fields__icontains='"field_id": 309609')
        if limit > 0:
            qs = qs[:limit]
        total = qs.count()
        self.stdout.write(f"Компаний с возможным полем 309609: {total}")

        added = 0
        skipped_dup = 0
        rejected = 0
        no_value = 0

        for comp in qs:
            vals = _get_custom_values_from_raw_fields(comp.raw_fields, SKYNET_FIELD_ID)
            texts = _texts_from_values(vals)
            if not texts:
                no_value += 1
                continue

            # all_phones: список структур от parse_skynet_phones: {"value": E.164, "comment": "..."}
            all_phones: list[dict] = []
            for t in texts:
                phs, rej, _ = parse_skynet_phones(t)
                all_phones.extend(phs)
                rejected += rej
            
            # дедуп по E164
            seen = set()
            uniq = []
            for item in all_phones:
                v = (item.get("value") or "").strip()
                if not v:
                    continue
                if v not in seen:
                    seen.add(v)
                    uniq.append(item)

            if not uniq:
                continue

            main_norm = _normalize_phone(comp.phone) if (comp.phone or "").strip() else ""
            existing = set(
                _normalize_phone(v) or v
                for v in CompanyPhone.objects.filter(company=comp).values_list("value", flat=True)
            )

            max_order = CompanyPhone.objects.filter(company=comp).aggregate(m=Max("order")).get("m")
            next_order = int(max_order) + 1 if max_order is not None else 0

            for item in uniq:
                raw_value = (item.get("value") or "").strip()
                if not raw_value:
                    continue
                n = _normalize_phone(raw_value) or raw_value
                if main_norm and main_norm == n:
                    skipped_dup += 1
                    continue
                if n in existing:
                    skipped_dup += 1
                    continue
                # Собираем комментарий: источник (SKYNET) + текст вокруг номера, если есть
                extra_comment = (item.get("comment") or "").strip()
                if extra_comment:
                    comment = f"SKYNET; {extra_comment}"
                else:
                    comment = "SKYNET"
                if not dry_run:
                    CompanyPhone.objects.create(company=comp, value=n, order=next_order, comment=comment)
                    existing.add(n)
                    next_order += 1
                added += 1
                self.stdout.write(
                    f"  {comp.name} (inn={comp.inn or '-'}): +1 CompanyPhone SKYNET {n}"
                    f"{' | ' + extra_comment if extra_comment else ''}"
                )

        self.stdout.write(self.style.SUCCESS(
            f"Итого: added={added}, skipped_duplicate={skipped_dup}, rejected={rejected}, no_value={no_value}"
        ))
