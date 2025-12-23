from __future__ import annotations

from django.http import HttpRequest, HttpResponse
from django.shortcuts import render


def handler404(request: HttpRequest, exception) -> HttpResponse:
    """
    Кастомная 404-страница. Django использует её при DEBUG=0.
    """
    return render(
        request,
        "404.html",
        {
            "path": getattr(request, "path", ""),
        },
        status=404,
    )


