from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from django.db import transaction

from accounts.models import User
from companies.models import Company, CompanySphere, CompanyStatus


def _clean_str(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    if s.lower() in ("nan", "none"):
        return ""
    return s


def _get(row: dict, *keys: str) -> str:
    for k in keys:
        if k in row:
            v = _clean_str(row.get(k))
            if v:
                return v
    return ""


def _digits_only(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _norm_name(s: str) -> str:
    s = _clean_str(s).lower()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _split_multi(s: str) -> list[str]:
    s = _clean_str(s)
    if not s:
        return []
    # Часто встречается ; или , как разделитель
    parts = re.split(r"[;,]+", s)
    return [p.strip() for p in parts if p.strip()]


@dataclass
class ImportResult:
    created_companies: int = 0
    updated_companies: int = 0
    company_rows: int = 0
    skipped_rows: int = 0
    preview_companies: list[dict] | None = None


def import_amo_csv(
    *,
    csv_path: str | Path,
    encoding: str = "utf-8-sig",
    dry_run: bool = False,
    companies_only: bool = True,
    limit_companies: int = 20,
) -> ImportResult:
    """
    Импорт из CSV в формате amo/base.csv.
    Для твоего запроса: companies_only=True и limit_companies=20.
    """
    csv_path = Path(csv_path)
    result = ImportResult(preview_companies=[])

    # Кеш пользователей
    user_by_name: dict[str, User] = {u.get_full_name().strip(): u for u in User.objects.all()}
    user_by_username: dict[str, User] = {u.username.strip(): u for u in User.objects.all()}

    # Кеш компаний для дедупа
    company_by_inn: dict[str, Company] = {c.inn: c for c in Company.objects.exclude(inn="")}
    company_by_name_addr: dict[tuple[str, str], Company] = {}

    @transaction.atomic
    def _run():
        with csv_path.open("r", encoding=encoding, newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise ValueError("CSV has no header row")

            for row in reader:
                row_id = _get(row, "ID")
                if not row_id:
                    result.skipped_rows += 1
                    continue

                t = _get(row, "Тип").lower()
                is_company_row = "компан" in t
                if companies_only and not is_company_row:
                    continue

                if is_company_row:
                    result.company_rows += 1

                    name = _get(row, "Название компании", "Компания", "Наименование")
                    legal_name = _get(row, "Юридическое название компании", "Юридическое название компании (компания)")
                    inn = _digits_only(_get(row, "ИНН", "ИНН (компания)"))
                    kpp = _digits_only(_get(row, "КПП", "КПП (компания)"))
                    address = _get(row, "Адрес", "Адрес (компания)")
                    website = _get(row, "Web", "Web (компания)")
                    status_name = _get(row, "Статус из Скайнет", "Статус из Скайнет (компания)")
                    spheres_raw = _get(row, "Сферы деятельности", "Сферы деятельности (компания)")

                    responsible_raw = _get(row, "Ответственный")
                    responsible = user_by_name.get(responsible_raw) or user_by_username.get(responsible_raw)

                    company = None
                    if inn:
                        company = company_by_inn.get(inn)

                    if company is None:
                        key = (_norm_name(name), _norm_name(address))
                        company = company_by_name_addr.get(key)
                        if company is None and key != ("", ""):
                            company = Company.objects.filter(name__iexact=name, address__iexact=address).first()

                    created = False
                    if company is None:
                        company = Company(
                            name=name or "(без названия)",
                            legal_name=legal_name,
                            inn=inn,
                            kpp=kpp,
                            address=address,
                            website=website,
                            responsible=responsible,
                            raw_fields={"source": "amo_import"},
                        )
                        result.created_companies += 1
                        created = True
                    else:
                        changed = False
                        for field, value in (
                            ("name", name),
                            ("legal_name", legal_name),
                            ("inn", inn),
                            ("kpp", kpp),
                            ("address", address),
                            ("website", website),
                        ):
                            if value and getattr(company, field) != value:
                                setattr(company, field, value)
                                changed = True
                        if responsible and company.responsible_id != responsible.id:
                            company.responsible = responsible
                            changed = True
                        if changed:
                            result.updated_companies += 1

                    # статус/сферы (создаём справочники при необходимости)
                    status_obj = None
                    if status_name:
                        status_obj, _ = CompanyStatus.objects.get_or_create(name=status_name)
                        if company.status_id != status_obj.id:
                            company.status = status_obj
                            if not created:
                                result.updated_companies += 0  # уже учли по changed; статус — доп.

                    if not dry_run:
                        company.save()

                        # spheres m2m — только после save
                        spheres = []
                        for sname in _split_multi(spheres_raw):
                            obj, _ = CompanySphere.objects.get_or_create(name=sname)
                            spheres.append(obj)
                        if spheres:
                            company.spheres.set(spheres)

                    # кеш для дедупа
                    if inn:
                        company_by_inn[inn] = company
                    company_by_name_addr[(_norm_name(company.name), _norm_name(company.address))] = company

                    # превью
                    if result.preview_companies is not None and len(result.preview_companies) < max(20, limit_companies):
                        result.preview_companies.append(
                            {"name": company.name, "inn": company.inn, "address": company.address, "status": status_name}
                        )

                    if companies_only and limit_companies and result.created_companies + result.updated_companies >= limit_companies:
                        # Быстрый выход: не читаем дальше файл
                        break

        if dry_run:
            transaction.set_rollback(True)

    _run()
    return result


