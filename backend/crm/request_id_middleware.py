# Backwards-compatibility shim: перенесён в core/request_id.py
from core.request_id import RequestIdMiddleware, RequestIdLoggingFilter, get_request_id  # noqa: F401
