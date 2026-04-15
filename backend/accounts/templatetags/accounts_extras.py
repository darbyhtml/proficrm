"""
Templatetags для работы с ролями пользователя в шаблонах.

Цель — убрать хардкод строковых литералов ролей ('admin', 'sales_head' и т.д.)
из .html-шаблонов. Если значение TextChoices когда-нибудь изменится — шаблоны
автоматически подхватят правку через User.Role.

Использование:
    {% load accounts_extras %}

    {# проверка доступа #}
    {% if user|has_role:"admin" %} ... {% endif %}
    {% if user|has_role:"sales_head,branch_director" %} ... {% endif %}

    {# человекочитаемая метка (из TextChoices.label) #}
    {{ user.role|role_label }}            → "Администратор"
    {{ 'sales_head'|role_label }}         → "Руководитель отдела продаж"

Замечание: роль `sales_head` в UI отображается как «РОП» —
    см. docs/decisions.md [2026-04-15]. Метка в TextChoices оставлена
    «Руководитель отдела продаж» для совместимости; при необходимости
    шаблон может напрямую выводить «РОП».
"""
from django import template

from accounts.models import User

register = template.Library()


@register.filter(name="has_role")
def has_role(user, roles: str) -> bool:
    """
    True, если `user.role` входит в список ролей (через запятую).

    Superuser и ADMIN всегда проходят проверку на "admin".
    Анонимные / None — всегда False.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return False

    wanted = {r.strip() for r in (roles or "").split(",") if r.strip()}
    if not wanted:
        return False

    user_role = getattr(user, "role", None)

    # Суперюзер эквивалентен админу для всех проверок
    if getattr(user, "is_superuser", False) and User.Role.ADMIN in wanted:
        return True

    return user_role in wanted


@register.filter(name="role_label")
def role_label(role_value: str) -> str:
    """Вернуть человекочитаемую метку роли из TextChoices.labels."""
    if not role_value:
        return ""
    try:
        return User.Role(role_value).label
    except ValueError:
        return str(role_value)
