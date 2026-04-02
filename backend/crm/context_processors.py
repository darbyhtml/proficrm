"""
Context processors для CRM.
"""


def csp_nonce(request):
    """Передаёт CSP nonce в шаблоны для inline-скриптов."""
    return {"csp_nonce": getattr(request, "csp_nonce", "")}
