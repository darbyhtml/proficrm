from __future__ import annotations

from django import template

from policy.engine import decide

register = template.Library()


@register.simple_tag(takes_context=True)
def policy_can(context, resource: str, resource_type: str = "page") -> bool:
    """
    Использование в шаблонах:
      {% load policy_tags %}
      {% if policy_can 'ui:analytics' %} ... {% endif %}
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    try:
        d = decide(user=user, resource_type=resource_type, resource=resource, context={"template": True})
        return bool(d.allowed)
    except Exception:
        return False

