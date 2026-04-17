"""MultiBranchRouter — выбор целевого филиала для нового диалога по региону клиента.

F5 (2026-04-18): добавлена понедельная ротация для общего пула регионов.
По требованию user (Q8 из roadmap-2026-spring.md):
- Общие регионы: Москва/МО, СПб/ЛО, Новгородская обл., Псковская обл.
- Неделя 1 → ЕКБ, неделя 2 → Краснодар, неделя 3 → Тюмень (циклично).

Раньше был round-robin на каждый ВИЗИТ (не на неделю) — любой клиент из
общего пула попадал к разным филиалам по очереди, что не соответствует
«понедельному» бизнес-правилу.

Новая логика:
- Ротация по ISO-номеру недели: `(week - 1) % len(pool_sorted)` — так
  неделя 1, 4, 7, 10… все достаются первому филиалу в порядке сортировки.
- Сортировка филиалов: по порядку из `BranchRotationOrder` настройке —
  fallback на order_by("id") если настройки нет.
"""

from datetime import date
from typing import Optional

from django.core.cache import cache
from django.utils import timezone

from accounts.models import Branch
from accounts.models_region import BranchRegion
from messenger.models import Conversation


class MultiBranchRouter:
    """Маршрутизация входящих диалогов по региону клиента.

    Логика:
    1. Пустой регион → fallback (ЕКБ).
    2. Точное совпадение BranchRegion(is_common_pool=False) → этот филиал.
    3. Регион общего пула (is_common_pool=True) у нескольких филиалов →
       ПОНЕДЕЛЬНАЯ ротация (по ISO-номеру недели, а не per-visit).
    4. Иначе → fallback.
    """

    # Устаревший ключ (оставлен для очистки в деплое — не используется)
    COMMON_POOL_RR_KEY = "messenger:region_router:common_pool_rr"
    FALLBACK_BRANCH_CODE = "ekb"

    # Предпочтительный порядок ротации филиалов в общем пуле (по коду).
    # Совпадает с порядком из Q8: ЕКБ → Краснодар → Тюмень.
    # Если код не найден — филиал попадает в конец списка по id.
    COMMON_POOL_ROTATION_ORDER = ("ekb", "krd", "tym")

    def route(self, conversation: Conversation) -> Optional[Branch]:
        region = (conversation.client_region or "").strip()
        if region:
            exact = (
                BranchRegion.objects
                .select_related("branch")
                .filter(region_name=region, is_common_pool=False)
                .first()
            )
            if exact:
                return exact.branch

            # Общий пул: через values_list, чтобы не зависеть от related_name.
            pool_branch_ids = (
                BranchRegion.objects
                .filter(region_name=region, is_common_pool=True)
                .values_list("branch_id", flat=True)
                .distinct()
            )
            pool_branches = list(Branch.objects.filter(id__in=list(pool_branch_ids)))
            if pool_branches:
                return self._pick_common_pool_branch(pool_branches)

        return self._fallback()

    def _pick_common_pool_branch(self, branches, today: Optional[date] = None):
        """Понедельная ротация: по ISO-неделе текущей даты.

        Порядок филиалов — через COMMON_POOL_ROTATION_ORDER (коды),
        остальные (неизвестные коды) — по возрастанию id в конце.

        Пример:
        - 2026-W15 (нед. 15) → index = (15-1) % 3 = 0 → ekb
        - 2026-W16 → index = 15 % 3 = 0 → ekb (same cycle)
        - 2026-W17 → index = 16 % 3 = 1 → krd
        - 2026-W18 → index = 17 % 3 = 2 → tym
        - 2026-W19 → index = 18 % 3 = 0 → ekb (wrap)

        Для тестов: можно передать `today` явно.
        """
        if not branches:
            return self._fallback()

        today = today or timezone.localdate()
        iso_week = today.isocalendar().week

        # Сортируем по COMMON_POOL_ROTATION_ORDER, остальное — по id.
        ordered_codes = list(self.COMMON_POOL_ROTATION_ORDER)

        def _sort_key(b: Branch):
            code = (b.code or "").lower()
            try:
                return (0, ordered_codes.index(code), b.id)
            except ValueError:
                return (1, 0, b.id)

        branches_sorted = sorted(branches, key=_sort_key)
        # (week-1) чтобы неделя 1 → index 0
        index = (iso_week - 1) % len(branches_sorted)
        return branches_sorted[index]

    def _fallback(self):
        """Fallback: филиал с code='ekb', иначе первый по id."""
        return (
            Branch.objects.filter(code=self.FALLBACK_BRANCH_CODE).first()
            or Branch.objects.order_by("id").first()
        )
