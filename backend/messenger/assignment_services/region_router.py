"""MultiBranchRouter — выбор целевого филиала для нового диалога по региону клиента."""

from typing import Optional

from django.core.cache import cache

from accounts.models import Branch
from accounts.models_region import BranchRegion
from messenger.models import Conversation


class MultiBranchRouter:
    """Маршрутизация входящих диалогов по региону клиента.

    Логика:
    1. Пустой регион → fallback (ЕКБ).
    2. Точное совпадение BranchRegion(is_common_pool=False) → этот филиал.
    3. Регион общего пула (is_common_pool=True) у нескольких филиалов →
       round-robin через Redis.
    4. Иначе → fallback.
    """

    COMMON_POOL_RR_KEY = "messenger:region_router:common_pool_rr"
    FALLBACK_BRANCH_CODE = "ekb"

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

    def _pick_common_pool_branch(self, branches):
        """Round-robin выбор филиала из общего пула через Redis-кэш."""
        if not branches:
            return self._fallback()
        branches_sorted = sorted(branches, key=lambda b: b.id)
        ids = [b.id for b in branches_sorted]
        last_idx = cache.get(self.COMMON_POOL_RR_KEY, -1)
        next_idx = (last_idx + 1) % len(ids)
        cache.set(self.COMMON_POOL_RR_KEY, next_idx, timeout=60 * 60 * 24 * 7)
        return branches_sorted[next_idx]

    def _fallback(self):
        """Fallback: филиал с code='ekb', иначе первый по id."""
        return (
            Branch.objects.filter(code=self.FALLBACK_BRANCH_CODE).first()
            or Branch.objects.order_by("id").first()
        )
