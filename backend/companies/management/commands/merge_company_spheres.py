from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Model

from companies.models import Company, CompanySphere


def _norm_name(name: str) -> str:
    s = (name or "").strip().lower()
    s = s.replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    # убираем всё кроме букв/цифр/пробелов
    s = re.sub(r"[^0-9a-zа-я\s\-]", "", s)
    s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except Exception:
        return False


def _resolve_sphere(ref: str) -> CompanySphere:
    ref = (ref or "").strip()
    if not ref:
        raise CommandError("Пустая ссылка на сферу (ожидался id или name).")
    if _is_int(ref):
        obj = CompanySphere.objects.filter(id=int(ref)).first()
        if not obj:
            raise CommandError(f"Сфера id={ref} не найдена.")
        return obj
    obj = CompanySphere.objects.filter(name=ref).first()
    if not obj:
        # fallback: case-insensitive
        obj = CompanySphere.objects.filter(name__iexact=ref).first()
    if not obj:
        raise CommandError(f"Сфера name='{ref}' не найдена.")
    return obj


def _m2m_field_names(through: type[Model]) -> tuple[str, str]:
    company_fk = None
    sphere_fk = None
    for f in through._meta.fields:
        if not getattr(f, "is_relation", False):
            continue
        rel_model = getattr(f, "related_model", None)
        if rel_model is Company:
            company_fk = f.name
        if rel_model is CompanySphere:
            sphere_fk = f.name
    if not company_fk or not sphere_fk:
        raise CommandError("Не удалось определить поля m2m через-модели для Company.spheres.")
    return company_fk, sphere_fk


class Command(BaseCommand):
    help = "Слияние/объединение сфер компаний (CompanySphere) с переносом связей у компаний."

    def add_arguments(self, parser):
        parser.add_argument(
            "--report",
            action="store_true",
            help="Показать отчёт по дублям и похожим названиям (по умолчанию).",
        )
        parser.add_argument(
            "--apply-exact",
            action="store_true",
            help="Слить 'очевидные' дубли по нормализации (регистр/пробелы/ё/дефисы).",
        )
        parser.add_argument(
            "--map",
            type=str,
            default="",
            help="Путь к JSON-файлу с явной картой merge: {\"from\": \"to\", ...} (id или name).",
        )
        parser.add_argument(
            "--threshold",
            type=float,
            default=0.92,
            help="Порог похожести (0..1) для подсказок (SequenceMatcher). По умолчанию 0.92.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Ничего не менять (только вывод).",
        )

    def handle(self, *args, **opts):
        do_report = bool(opts.get("report")) or (not opts.get("apply_exact") and not opts.get("map"))
        apply_exact = bool(opts.get("apply_exact"))
        dry_run = bool(opts.get("dry_run"))
        map_path = (opts.get("map") or "").strip()
        threshold = float(opts.get("threshold") or 0.92)

        spheres = list(CompanySphere.objects.all().order_by("name").only("id", "name"))
        if not spheres:
            self.stdout.write("Сфер нет.")
            return

        # Группы "точных" дублей по нормализации
        groups: dict[str, list[CompanySphere]] = {}
        for s in spheres:
            groups.setdefault(_norm_name(s.name), []).append(s)
        exact_dups = {k: v for k, v in groups.items() if len(v) > 1 and k}

        if do_report:
            self.stdout.write("## Дубли по нормализации (точные/очевидные)\n")
            if not exact_dups:
                self.stdout.write("(не найдено)\n")
            else:
                for key, items in sorted(exact_dups.items(), key=lambda kv: (len(kv[1]) * -1, kv[0])):
                    self.stdout.write(f"- norm='{key}':")
                    for it in items:
                        self.stdout.write(f"  - id={it.id} name='{it.name}' companies={it.companies.count()}")
                    self.stdout.write("")

            # Подсказки по похожим названиям (опечатки типа Металлургия/Металургия)
            self.stdout.write("\n## Похожие названия (подсказки)\n")
            by_norm = [(s, _norm_name(s.name)) for s in spheres]
            # ограничиваем для скорости: сравнение только нормализованных строк
            pairs = []
            for i in range(len(by_norm)):
                a, an = by_norm[i]
                if not an:
                    continue
                for j in range(i + 1, len(by_norm)):
                    b, bn = by_norm[j]
                    if not bn or an == bn:
                        continue
                    r = SequenceMatcher(a=an, b=bn).ratio()
                    if r >= threshold:
                        pairs.append((r, a, b))
            pairs.sort(key=lambda x: x[0], reverse=True)
            if not pairs:
                self.stdout.write("(не найдено)\n")
            else:
                for r, a, b in pairs[:200]:
                    self.stdout.write(f"- {r:.2f}: id={a.id} '{a.name}'  <->  id={b.id} '{b.name}'")
                if len(pairs) > 200:
                    self.stdout.write(f"... ещё {len(pairs)-200} пар(ы) скрыто")

        # Явная карта слияния
        merges: list[tuple[CompanySphere, CompanySphere]] = []
        if map_path:
            p = Path(map_path)
            if not p.exists():
                raise CommandError(f"Файл не найден: {map_path}")
            raw = json.loads(p.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise CommandError("--map ожидает JSON-объект вида {\"from\": \"to\"}")
            for k, v in raw.items():
                src = _resolve_sphere(str(k))
                dst = _resolve_sphere(str(v))
                if src.id == dst.id:
                    continue
                merges.append((src, dst))

        # Авто-слияние по "точным" дублям
        if apply_exact:
            for _, items in exact_dups.items():
                # выбираем каноническую: с максимальным количеством компаний, иначе с "красивым" именем (короче)
                items_sorted = sorted(
                    items,
                    key=lambda s: (-s.companies.count(), len(s.name), s.name.lower()),
                )
                dst = items_sorted[0]
                for src in items_sorted[1:]:
                    merges.append((src, dst))

        # Удаляем дубликаты merge-операций
        uniq: dict[tuple[int, int], tuple[CompanySphere, CompanySphere]] = {}
        for src, dst in merges:
            uniq[(src.id, dst.id)] = (src, dst)
        merges = list(uniq.values())

        if not merges:
            if not do_report:
                self.stdout.write("Нет операций слияния (merge).")
            return

        self.stdout.write("\n## План слияния\n")
        for src, dst in merges:
            self.stdout.write(f"- {src.id} '{src.name}' -> {dst.id} '{dst.name}'")

        if dry_run:
            self.stdout.write("\nDRY-RUN: изменения не применены.")
            return

        through = Company.spheres.through
        company_fk, sphere_fk = _m2m_field_names(through)

        with transaction.atomic():
            total_links_moved = 0
            total_spheres_deleted = 0
            for src, dst in merges:
                if src.id == dst.id:
                    continue
                # компании, где есть src
                company_ids = list(
                    through.objects.filter(**{sphere_fk: src.id}).values_list(company_fk, flat=True)
                )
                if company_ids:
                    # добавляем связь на dst (ignore_conflicts, чтобы не падать на уникальности)
                    to_create = [through(**{company_fk: cid, sphere_fk: dst.id}) for cid in company_ids]
                    through.objects.bulk_create(to_create, ignore_conflicts=True)
                    # удаляем связи на src
                    deleted_links, _ = through.objects.filter(**{company_fk + "__in": company_ids, sphere_fk: src.id}).delete()
                    total_links_moved += int(deleted_links or 0)

                # обновляем фильтры кампаний mailer (snapshot filter_meta) — чтобы старые id не висели
                try:
                    from mailer.models import Campaign
                    for camp in Campaign.objects.filter(filter_meta__sphere__contains=[src.id]).only("id", "filter_meta").iterator():
                        meta = dict(camp.filter_meta or {})
                        sph = meta.get("sphere")
                        if isinstance(sph, list):
                            meta["sphere"] = [dst.id if int(x) == int(src.id) else x for x in sph if str(x).strip()]
                            camp.filter_meta = meta
                            camp.save(update_fields=["filter_meta", "updated_at"])
                except Exception:
                    # не критично, если mailer не установлен/временно недоступен
                    pass

                # удаляем сферу-источник (после переноса)
                src.delete()
                total_spheres_deleted += 1

            self.stdout.write(
                self.style.SUCCESS(
                    f"\nГотово: удалено сфер={total_spheres_deleted}, перенесено связей (company-sphere)={total_links_moved}."
                )
            )

