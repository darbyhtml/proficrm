# Backwards-compatibility shim: перенесён в core/request_id.py
from core.request_id import (
    RequestIdLoggingFilter,
    RequestIdMiddleware,
    get_request_id,
)
