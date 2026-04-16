# Backwards-compatibility shim: перенесён в core/timezone_utils.py
from core.timezone_utils import RUS_TZ_CHOICES, guess_ru_timezone_from_address  # noqa: F401

__all__ = ["RUS_TZ_CHOICES", "guess_ru_timezone_from_address"]
