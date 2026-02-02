"""
Исторический пакет backend'ов поиска компаний.

Ранее здесь размещался TypesenseSearchBackend; после миграции на PostgreSQL FTS
поиск компаний реализован исключительно через CompanySearchService
(`companies.search_service.CompanySearchService`), без внешних движков.

Модуль оставлен для обратной совместимости импорта, но не экспортирует backend'ы.
"""

__all__: list[str] = []
