"""Helper submodules for UI views (W1.1 extraction from _base.py).

Submodules:
- ``companies``: company access/edit/delete + notifications + cache.
- ``company_filters``: filter params + _apply_company_filters chain.
- ``search``: text/phone/email normalizers for FTS/search.
- ``tasks``: task access/edit/delete permission helpers.
- ``cold_call``: cold-call reports + month utilities.
- ``http``: generic request-processing helpers.

All functions previously lived in ``backend/ui/views/_base.py`` и остались
re-exported оттуда for backward compatibility. Prefer direct import из
submodule в new code.
"""
