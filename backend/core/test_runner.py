"""
Custom test runner для SQLite — при создании тестовой БД перехватывает ошибки,
возникающие от PostgreSQL-специфичного SQL в миграциях (GIN, IF EXISTS, Extensions, triggers,
ArrayField defaults).

ТОЛЬКО для фазы setup_databases — тесты по-прежнему работают со стандартной обработкой ошибок.
"""

import logging

from django.test.runner import DiscoverRunner

logger = logging.getLogger(__name__)


class SQLiteCompatibleTestRunner(DiscoverRunner):
    """
    При тестировании на SQLite игнорирует ошибки от PostgreSQL-специфичных миграций.
    На PostgreSQL работает как стандартный DiscoverRunner.

    Обрабатываемые случаи:
    1. SQL-ошибки от PG-специфичных операторов (CREATE EXTENSION, GIN-индексы и т.д.)
    2. ValueError из SQLite schema editor при add_field с PG-типами (ArrayField default=[])
    """

    def setup_databases(self, **kwargs):
        from django.db import connection

        if connection.vendor != "sqlite":
            return super().setup_databases(**kwargs)

        import sqlite3 as _sqlite3

        import django.db.backends.sqlite3.base as _sqlite_base
        import django.db.backends.sqlite3.schema as _sqlite_schema
        from django.db.utils import DatabaseError, OperationalError, ProgrammingError

        # --- Патч 1: перехват SQL-ошибок ---
        _orig_execute = _sqlite_base.SQLiteCursorWrapper.execute

        def _safe_execute(self_cursor, sql, params=None):
            try:
                return _orig_execute(self_cursor, sql, params)
            except (
                _sqlite3.OperationalError,
                _sqlite3.ProgrammingError,
                OperationalError,
                ProgrammingError,
                DatabaseError,
            ) as exc:
                sql_lower = (sql or "").lower().strip()
                # Разрешаем любую CREATE TABLE / INSERT / UPDATE / SELECT — не пропускаем их
                safe_to_skip_verbs = (
                    "create extension",
                    "create index",
                    "drop index",
                    "alter index",
                    "alter table",
                    "drop table",
                    "create trigger",
                    "drop trigger",
                    "create function",
                    "drop function",
                    "create or replace function",
                )
                if any(sql_lower.startswith(v) for v in safe_to_skip_verbs):
                    logger.debug(
                        "SQLiteCompatibleTestRunner: skipping PG-only SQL: %s... (%s)",
                        sql[:80],
                        exc,
                    )
                    return  # Пропускаем: PostgreSQL-специфичная операция
                raise  # Остальные ошибки пробрасываем

        # --- Патч 2: перехват ValueError из add_field при PG-типах (ArrayField и т.д.) ---
        _orig_add_field = _sqlite_schema.DatabaseSchemaEditor.add_field

        def _safe_add_field(self_editor, model, field):
            try:
                return _orig_add_field(self_editor, model, field)
            except (ValueError, TypeError) as exc:
                field_type = type(field).__name__
                logger.debug(
                    "SQLiteCompatibleTestRunner: skipping PG-only add_field (%s.%s: %s) — %s",
                    model.__name__,
                    field.name,
                    field_type,
                    exc,
                )
                return  # Пропускаем: PostgreSQL-специфичное поле (ArrayField и т.д.)

        _sqlite_base.SQLiteCursorWrapper.execute = _safe_execute
        _sqlite_schema.DatabaseSchemaEditor.add_field = _safe_add_field
        try:
            return super().setup_databases(**kwargs)
        finally:
            _sqlite_base.SQLiteCursorWrapper.execute = _orig_execute
            _sqlite_schema.DatabaseSchemaEditor.add_field = _orig_add_field
