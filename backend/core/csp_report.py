"""W2.3 Phase 1 — CSP violation report receiver.

Endpoint `/csp-report/` принимает CSP violation reports от браузеров когда
shadow `Content-Security-Policy-Report-Only` directive violated.

Design:
- `csrf_exempt` — браузеры не отправляют CSRF-token (это не form submission).
- `require_POST` — только POST метод принимается.
- 10KB body cap — защита от report flooding.
- Логирует через Python logger `crm.csp` (уровень WARNING) — не создаёт
  ActivityEvent записей чтобы не раздувать БД (CSP violation volume
  может быть высоким если extension'ы у users).
- Возвращает 204 No Content — минимальный response.

Monitoring:
- `docker compose logs web | grep "CSP violation"` — see recent reports.
- В будущих фазах можно forwardить в GlitchTip или aggregator.
"""

from __future__ import annotations

import json
import logging

from django.http import HttpResponse, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

logger = logging.getLogger("crm.csp")

# Максимальный размер CSP report (browser реально шлёт ~1-5KB, 10KB — с запасом).
MAX_REPORT_BYTES = 10_000


@csrf_exempt
@require_POST
def csp_report_view(request):
    """Receive CSP violation reports (POST `/csp-report/`).

    Browsers send violation as JSON {"csp-report": {...}} when CSP directive
    violated in Report-Only header.
    """
    body = request.body
    if len(body) > MAX_REPORT_BYTES:
        return HttpResponseBadRequest("Report too large")

    try:
        report = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return HttpResponseBadRequest("Invalid JSON")

    # Top-level payload должен быть dict (W3C spec). Отклоняем lists/scalars.
    if not isinstance(report, dict):
        return HttpResponseBadRequest("Invalid report shape")

    # Modern browsers wrap payload в 'csp-report' key per W3C spec.
    csp_report = report.get("csp-report", report)
    if not isinstance(csp_report, dict):
        return HttpResponseBadRequest("Invalid report shape")

    violated_directive = csp_report.get("violated-directive", "unknown")
    blocked_uri = csp_report.get("blocked-uri", "unknown")
    document_uri = csp_report.get("document-uri", "unknown")
    line_number = csp_report.get("line-number", 0)
    source_file = csp_report.get("source-file", "unknown")

    logger.warning(
        "CSP violation: directive=%s blocked=%s document=%s source=%s:%s",
        violated_directive,
        blocked_uri,
        document_uri,
        source_file,
        line_number,
        extra={"csp_report": csp_report},
    )

    return HttpResponse(status=204)
