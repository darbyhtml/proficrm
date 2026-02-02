"""
Celery-задачи для модуля companies (поиск, индексация).
"""
from __future__ import annotations

import logging

from celery import shared_task
from django.core.management import call_command

logger = logging.getLogger(__name__)


@shared_task(name="companies.tasks.reindex_companies_daily")
def reindex_companies_daily():
    """
    Ежедневная полная переиндексация компаний: Postgres (CompanySearchIndex)
    и при SEARCH_ENGINE_BACKEND=typesense — Typesense.
    Запускать вне рабочих часов (например, 03:00).
    """
    from django.conf import settings
    from django.db import connection

    logger.info("reindex_companies_daily: старт")

    if connection.vendor == "postgresql":
        try:
            call_command("rebuild_company_search_index", chunk=500)
            logger.info("reindex_companies_daily: Postgres CompanySearchIndex OK")
        except Exception as e:
            logger.exception("reindex_companies_daily: Postgres rebuild_company_search_index: %s", e)

    backend = (getattr(settings, "SEARCH_ENGINE_BACKEND", "postgres") or "postgres").strip().lower()
    if backend == "typesense":
        try:
            call_command("index_companies_typesense", chunk=300)
            logger.info("reindex_companies_daily: Typesense OK")
        except Exception as e:
            logger.exception("reindex_companies_daily: Typesense index_companies_typesense: %s", e)

    logger.info("reindex_companies_daily: завершено")
