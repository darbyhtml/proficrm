# Backends поиска компаний: Postgres (CompanySearchService) и Typesense (TypesenseSearchBackend).
# Выбор backend — через SEARCH_ENGINE_BACKEND в settings.

from .typesense_backend import TypesenseSearchBackend

__all__ = ["TypesenseSearchBackend"]
