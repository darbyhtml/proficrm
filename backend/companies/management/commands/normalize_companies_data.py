from __future__ import annotations

"""
Очистка и нормализация телефонных номеров и email-ов по компаниям.

Проходит по следующим полям (батчами):
- Company.phone
- CompanyPhone.value
- ContactPhone.value
- Company.email
- CompanyEmail.value
- ContactEmail.value

Правила:
- телефоны → companies.normalizers.normalize_phone
- email → lower().strip()

Сохраняем объект ТОЛЬКО если значение реально изменилось.
После успешной нормализации (без необработанных исключений) дополнительно
запускается rebuild_company_search_index (для PostgreSQL).
"""

from typing import Any, Callable, Dict, Tuple

from django.core.management import BaseCommand, call_command
from django.db import models

from companies.models import (
    Company,
    CompanyEmail,
    CompanyPhone,
    ContactEmail,
    ContactPhone,
)
from companies.normalizers import normalize_phone


EmailNormalizer = Callable[[Any], Any]
PhoneNormalizer = Callable[[Any], Any]


def _normalize_email(value: Any) -> Any:
    """Приводит email к lower().strip(); безопасно для None."""
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return s
    return s.lower()


class Command(BaseCommand):
    help = "Нормализует телефоны и email-ы компаний/контактов и переиндексирует поиск."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--batch-size",
            type=int,
            default=500,
            help="Размер батча при сохранении (по умолчанию 500).",
        )

    def handle(self, *args, **options) -> None:
        batch_size = int(options.get("batch_size") or 500)
        if batch_size <= 0:
            batch_size = 500

        self.stdout.write(f"normalize_companies_data: старт (batch_size={batch_size})")

        total_fixed: Dict[str, int] = {}

        # Телефоны
        fixed, scanned = self._normalize_queryset_field(
            qs=Company.objects.all(),
            field_name="phone",
            normalizer=normalize_phone,
            batch_size=batch_size,
        )
        total_fixed["Company.phone"] = fixed
        self.stdout.write(f"  Company.phone: просмотрено={scanned}, исправлено={fixed}")

        fixed, scanned = self._normalize_queryset_field(
            qs=CompanyPhone.objects.all(),
            field_name="value",
            normalizer=normalize_phone,
            batch_size=batch_size,
        )
        total_fixed["CompanyPhone.value"] = fixed
        self.stdout.write(f"  CompanyPhone.value: просмотрено={scanned}, исправлено={fixed}")

        fixed, scanned = self._normalize_queryset_field(
            qs=ContactPhone.objects.all(),
            field_name="value",
            normalizer=normalize_phone,
            batch_size=batch_size,
        )
        total_fixed["ContactPhone.value"] = fixed
        self.stdout.write(f"  ContactPhone.value: просмотрено={scanned}, исправлено={fixed}")

        # Email-ы
        fixed, scanned = self._normalize_queryset_field(
            qs=Company.objects.all(),
            field_name="email",
            normalizer=_normalize_email,
            batch_size=batch_size,
        )
        total_fixed["Company.email"] = fixed
        self.stdout.write(f"  Company.email: просмотрено={scanned}, исправлено={fixed}")

        fixed, scanned = self._normalize_queryset_field(
            qs=CompanyEmail.objects.all(),
            field_name="value",
            normalizer=_normalize_email,
            batch_size=batch_size,
        )
        total_fixed["CompanyEmail.value"] = fixed
        self.stdout.write(f"  CompanyEmail.value: просмотрено={scanned}, исправлено={fixed}")

        fixed, scanned = self._normalize_queryset_field(
            qs=ContactEmail.objects.all(),
            field_name="value",
            normalizer=_normalize_email,
            batch_size=batch_size,
        )
        total_fixed["ContactEmail.value"] = fixed
        self.stdout.write(f"  ContactEmail.value: просмотрено={scanned}, исправлено={fixed}")

        total_changes = sum(total_fixed.values())
        self.stdout.write(
            self.style.SUCCESS(f"normalize_companies_data: всего исправлено значений: {total_changes}")
        )

        # После успешной нормализации — перестроение индекса (только для PostgreSQL).
        from django.db import connection

        if connection.vendor == "postgresql":
            self.stdout.write("normalize_companies_data: запускаем rebuild_company_search_index...")
            call_command("rebuild_company_search_index", chunk=batch_size)
            self.stdout.write(
                self.style.SUCCESS("normalize_companies_data: rebuild_company_search_index завершён.")
            )
        else:
            self.stdout.write(
                "normalize_companies_data: не PostgreSQL (connection.vendor "
                f"= {connection.vendor!r}) — перестроение CompanySearchIndex пропущено."
            )

    def _normalize_queryset_field(
        self,
        *,
        qs: models.QuerySet,
        field_name: str,
        normalizer: Callable[[Any], Any],
        batch_size: int,
    ) -> Tuple[int, int]:
        """
        Нормализует одно поле в queryset'е, сохраняя только реально изменившиеся записи.

        Возвращает (кол-во_исправленных, кол-во_просмотренных).
        """
        model = qs.model
        if not isinstance(model, type) or not issubclass(model, models.Model):
            return 0, 0

        updated_count = 0
        scanned_count = 0
        buffer: list[models.Model] = []

        it = qs.only("pk", field_name).iterator(chunk_size=batch_size)
        for obj in it:
            scanned_count += 1
            old_value = getattr(obj, field_name)
            new_value = normalizer(old_value)
            if new_value == old_value:
                continue
            setattr(obj, field_name, new_value)
            buffer.append(obj)
            if len(buffer) >= batch_size:
                model.objects.bulk_update(buffer, [field_name])
                updated_count += len(buffer)
                buffer = []

        if buffer:
            model.objects.bulk_update(buffer, [field_name])
            updated_count += len(buffer)

        return updated_count, scanned_count

