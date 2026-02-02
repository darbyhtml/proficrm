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
    Ежедневная задача очистки данных и переиндексации компаний в PostgreSQL.

    Порядок:
    1) normalize_companies_data — нормализация телефонов и email-ов (без падения задачи при ошибках).
    2) rebuild_company_search_index — полная переиндексация CompanySearchIndex (только для PostgreSQL).

    Запускать вне рабочих часов (например, 03:00).
    """
    from django.db import connection

    logger.info("reindex_companies_daily: старт")

    # 1) Ночная нормализация данных (ошибки не валят задачу целиком)
    try:
        call_command("normalize_companies_data", batch_size=500)
        logger.info("reindex_companies_daily: normalize_companies_data OK")
    except Exception as e:
        logger.exception("reindex_companies_daily: normalize_companies_data failed: %s", e)

    # 2) Переиндексация поиска (Postgres)
    if connection.vendor == "postgresql":
        try:
            call_command("rebuild_company_search_index", chunk=500)
            logger.info("reindex_companies_daily: Postgres CompanySearchIndex OK")
        except Exception as e:
            logger.exception("reindex_companies_daily: Postgres rebuild_company_search_index: %s", e)

    logger.info("reindex_companies_daily: завершено")
