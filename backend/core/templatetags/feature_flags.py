"""
Template tags для feature flags — Wave 0.3.

Позволяют использовать в Django-шаблонах:

    {% load feature_flags %}

    {# Как булевая переменная в if #}
    {% feature_flag "UI_V3B_DEFAULT" as ui_v3b %}
    {% if ui_v3b %}
        <div class="v3b-layout">...</div>
    {% else %}
        <div class="classic-layout">...</div>
    {% endif %}

    {# Блочный тег (синтаксический сахар над if+feature_flag) #}
    {% feature_enabled "EMAIL_BOUNCE_HANDLING" %}
        <a href="{% url 'mailer_bounce_report' %}">Отчёт о bounce</a>
    {% endfeature_enabled %}

Под капотом вызывает ``core.feature_flags.is_enabled`` с текущим ``request.user``.
"""

from __future__ import annotations

from django import template

from core.feature_flags import is_enabled

register = template.Library()


@register.simple_tag(takes_context=True)
def feature_flag(context, flag_name: str) -> bool:
    """Проверить флаг, вернуть bool для ``{% if %}``.

    Usage:
        {% feature_flag "FLAG_NAME" as var %}
        {% if var %}...{% endif %}
    """
    request = context.get("request")
    user = getattr(request, "user", None) if request is not None else None
    # Если user — AnonymousUser, передаём None, иначе авторизованного
    if user is not None and not getattr(user, "is_authenticated", False):
        user = None
    return is_enabled(flag_name, user=user, request=request)


class FeatureEnabledNode(template.Node):
    """Блок `{% feature_enabled "FLAG" %}...{% endfeature_enabled %}`."""

    def __init__(self, flag_name_var: template.Variable, nodelist: template.NodeList):
        self.flag_name_var = flag_name_var
        self.nodelist = nodelist

    def render(self, context) -> str:
        try:
            flag_name = self.flag_name_var.resolve(context)
        except template.VariableDoesNotExist:
            flag_name = str(self.flag_name_var).strip("\"'")
        request = context.get("request")
        user = getattr(request, "user", None) if request is not None else None
        if user is not None and not getattr(user, "is_authenticated", False):
            user = None
        if is_enabled(str(flag_name), user=user, request=request):
            return self.nodelist.render(context)
        return ""


@register.tag(name="feature_enabled")
def do_feature_enabled(parser, token) -> FeatureEnabledNode:
    """Блочный тег. Рендерит содержимое только если флаг активен.

    Пример:
        {% feature_enabled "UI_V3B_DEFAULT" %}
            <script src="{% static 'ui/v3b-bundle.js' %}"></script>
        {% endfeature_enabled %}
    """
    try:
        _tag_name, flag_arg = token.split_contents()
    except ValueError as exc:
        raise template.TemplateSyntaxError(
            '{%% feature_enabled "FLAG_NAME" %%}...{%% endfeature_enabled %%}'
        ) from exc
    nodelist = parser.parse(("endfeature_enabled",))
    parser.delete_first_token()
    return FeatureEnabledNode(template.Variable(flag_arg), nodelist)
