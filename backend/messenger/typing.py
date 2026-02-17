"""
Индикатор «печатает»: хранение в Redis с коротким TTL.

Ключи:
- messenger:typing:{conversation_id}:operator — оператор печатает (TTL 8 с)
- messenger:typing:{conversation_id}:contact — контакт (виджет) печатает (TTL 8 с)
"""

from django.core.cache import cache

TYPING_TTL = 8  # секунд


def _key_operator(conversation_id: int) -> str:
    return f"messenger:typing:{conversation_id}:operator"


def _key_contact(conversation_id: int) -> str:
    return f"messenger:typing:{conversation_id}:contact"


def set_operator_typing(conversation_id: int) -> None:
    cache.set(_key_operator(conversation_id), 1, timeout=TYPING_TTL)


def set_contact_typing(conversation_id: int) -> None:
    cache.set(_key_contact(conversation_id), 1, timeout=TYPING_TTL)


def get_typing_status(conversation_id: int) -> dict:
    return {
        "operator_typing": bool(cache.get(_key_operator(conversation_id))),
        "contact_typing": bool(cache.get(_key_contact(conversation_id))),
    }
