from __future__ import annotations

import secrets
from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Count, Q, F, Min, Avg, ExpressionWrapper, DurationField
from django.db.models.functions import TruncDate
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone

from crm.utils import require_admin
from messenger.models import Conversation, Message, Inbox, RoutingRule, Channel, CannedResponse, Campaign, AutomationRule, Macro
from messenger.logging_utils import ui_logger
from messenger.utils import ensure_messenger_enabled_view

@login_required
def settings_messenger_overview(request: HttpRequest) -> HttpResponse:
    """
    Обзор Inbox'ов и быстрые действия.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    ensure_messenger_enabled_view()

    # Получаем все Inbox'ы (для админа можно показывать всё)
    inboxes = list(
        Inbox.objects.select_related("branch").annotate(
            open_conversations_count=Count(
                "conversations",
                filter=Q(conversations__status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING]),
            )
        ).order_by("name")
    )
    inbox_ids = [i.id for i in inboxes]
    # Тип канала по первому связанному Channel или из settings
    from messenger.models import Channel
    channel_types = dict(Channel.objects.filter(inbox_id__in=inbox_ids).values_list("inbox_id", "type"))
    has_conversations = set(
        Conversation.objects.filter(inbox_id__in=inbox_ids).values_list("inbox_id", flat=True).distinct()
    )
    for inbox in inboxes:
        inbox.channel_type = channel_types.get(inbox.id)
        if not inbox.channel_type and isinstance(inbox.settings, dict):
            inbox.channel_type = (inbox.settings.get("channel") or {}).get("type")
        if not inbox.channel_type:
            inbox.channel_type = Channel.Type.WEBSITE
        inbox.can_delete = inbox.id not in has_conversations

    total_inboxes = len(inboxes)
    active_inboxes = sum(1 for i in inboxes if i.is_active)
    # Inbox, для которого по умолчанию показываем код вставки (приоритет — активный сайт)
    snippet_inbox = None
    for i in inboxes:
        if i.is_active and i.channel_type == Channel.Type.WEBSITE:
            snippet_inbox = i
            break
    if snippet_inbox is None and inboxes:
        snippet_inbox = inboxes[0]

    # Получаем базовый URL для виджета
    from django.conf import settings
    base_url = getattr(settings, "PUBLIC_BASE_URL", request.build_absolute_uri("/").rstrip("/"))

    return render(
        request,
        "ui/settings/messenger_overview.html",
        {
            "inboxes": inboxes,
            "base_url": base_url,
            "total_inboxes": total_inboxes,
            "active_inboxes": active_inboxes,
            "snippet_inbox": snippet_inbox,
        },
    )


@login_required
def settings_messenger_source_choose(request: HttpRequest) -> HttpResponse:
    """
    Выбор типа источника перед созданием (шаг 1 в стиле Chatwoot).
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    ensure_messenger_enabled_view()
    return render(
        request,
        "ui/settings/messenger_source_choose.html",
        {},
    )


@login_required
def settings_messenger_inbox_ready(request: HttpRequest, inbox_id: int) -> HttpResponse:
    """
    Страница «Источник готов» после создания: код вставки, Копировать, Перейти к настройкам.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    ensure_messenger_enabled_view()
    try:
        inbox = Inbox.objects.get(id=inbox_id)
    except Inbox.DoesNotExist:
        messages.error(request, "Источник не найден.")
        return redirect("settings_messenger_overview")
    from django.conf import settings as django_settings
    base_url = getattr(django_settings, "PUBLIC_BASE_URL", request.build_absolute_uri("/").rstrip("/"))
    return render(
        request,
        "ui/settings/messenger_inbox_ready.html",
        {"inbox": inbox, "base_url": base_url},
    )


@login_required
def settings_messenger_health(request: HttpRequest) -> HttpResponse:
    """
    Страница диагностики Messenger: флаг, Redis, кол-во inbox.
    Доступна только админу. Не требует MESSENGER_ENABLED (чтобы проверить состояние при выключенном модуле).
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    from django.conf import settings
    from django.core.cache import cache

    messenger_enabled = getattr(settings, "MESSENGER_ENABLED", False)
    cache_backend = ""
    cache_location = ""
    try:
        caches = getattr(settings, "CACHES", {}) or {}
        default_cache = caches.get("default") or {}
        cache_backend = str(default_cache.get("BACKEND") or "")
        cache_location = str(default_cache.get("LOCATION") or "")
    except Exception:
        pass
    cache_is_redis = "redis" in cache_backend.lower()
    redis_url = getattr(settings, "REDIS_URL", "")

    redis_ok = False
    try:
        cache.set("messenger:health:ping", "1", timeout=10)
        redis_ok = cache.get("messenger:health:ping") == "1"
    except Exception:
        pass

    active_inboxes_count = 0
    if messenger_enabled:
        try:
            active_inboxes_count = Inbox.objects.filter(is_active=True).count()
        except Exception:
            pass

    return render(
        request,
        "ui/settings/messenger_health.html",
        {
            "messenger_enabled": messenger_enabled,
            "redis_ok": redis_ok,
            "active_inboxes_count": active_inboxes_count,
            "cache_backend": cache_backend,
            "cache_location": cache_location,
            "cache_is_redis": cache_is_redis,
            "redis_url": redis_url,
        },
    )


@login_required
def settings_messenger_analytics(request: HttpRequest) -> HttpResponse:
    """
    Аналитика Messenger (admin only): простые метрики по диалогам.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    ensure_messenger_enabled_view()

    try:
        days = int(request.GET.get("days", "7"))
    except (TypeError, ValueError):
        days = 7
    if days not in (7, 30, 90):
        days = 7

    since_dt = timezone.now() - timedelta(days=days)

    qs = Conversation.objects.select_related("assignee", "branch").filter(created_at__gte=since_dt)

    first_out_at = Min("messages__created_at", filter=Q(messages__direction=Message.Direction.OUT))
    qs_annotated = qs.annotate(first_out_at=first_out_at).annotate(
        first_response_delta=ExpressionWrapper(
            F("first_out_at") - F("created_at"),
            output_field=DurationField(),
        )
    )

    totals = {
        "total": qs.count(),
        "open_pending": qs.filter(status__in=[Conversation.Status.OPEN, Conversation.Status.PENDING]).count(),
    }

    avg_delta = qs_annotated.filter(first_out_at__isnull=False).aggregate(avg=Avg("first_response_delta")).get("avg")
    avg_first_response = None
    if avg_delta is not None:
        total_seconds = int(avg_delta.total_seconds())
        mins = total_seconds // 60
        secs = total_seconds % 60
        avg_first_response = f"{mins}м {secs:02d}с"

    by_day = list(
        qs.annotate(day=TruncDate("created_at"))
        .values("day")
        .annotate(count=Count("id"))
        .order_by("day")
    )

    def _fmt_avg(td):
        if td is None:
            return None
        s = int(td.total_seconds())
        m = s // 60
        return f"{m}м"

    # Детальная аналитика по операторам и филиалам считаем в Python,
    # чтобы избежать ошибок вложенных агрегатов в Django.

    conv_rows = qs_annotated.filter(first_out_at__isnull=False).values(
        "assignee__id",
        "assignee__first_name",
        "assignee__last_name",
        "assignee__username",
        "branch__id",
        "branch__name",
        "first_response_delta",
    )

    operator_stats = {}
    branch_stats = {}

    for row in conv_rows:
        delta = row.get("first_response_delta")
        if delta is None:
            continue

        assignee_id = row.get("assignee__id")
        if assignee_id:
            op = operator_stats.setdefault(
                assignee_id,
                {
                    "first_name": row.get("assignee__first_name") or "",
                    "last_name": row.get("assignee__last_name") or "",
                    "username": row.get("assignee__username") or "",
                    "count": 0,
                    "sum_delta": timedelta(0),
                },
            )
            op["count"] += 1
            op["sum_delta"] += delta

        branch_id = row.get("branch__id")
        if branch_id:
            br = branch_stats.setdefault(
                branch_id,
                {
                    "name": row.get("branch__name") or f"#{branch_id}",
                    "count": 0,
                    "sum_delta": timedelta(0),
                },
            )
            br["count"] += 1
            br["sum_delta"] += delta

    by_operator = []
    for op in sorted(operator_stats.values(), key=lambda x: x["count"], reverse=True)[:20]:
        name = (f"{op['first_name']} {op['last_name']}".strip() or op["username"] or "Без имени").strip()
        avg_td = op["sum_delta"] / op["count"] if op["count"] else None
        by_operator.append(
            {
                "name": name,
                "count": op["count"],
                "avg_first_response": _fmt_avg(avg_td),
            }
        )

    by_branch = []
    for br in sorted(branch_stats.values(), key=lambda x: x["count"], reverse=True):
        avg_td = br["sum_delta"] / br["count"] if br["count"] else None
        by_branch.append(
            {
                "name": br["name"],
                "count": br["count"],
                "avg_first_response": _fmt_avg(avg_td),
            }
        )

    return render(
        request,
        "ui/settings/messenger_analytics.html",
        {
            "days": days,
            "totals": totals,
            "avg_first_response": avg_first_response,
            "by_day": by_day,
            "by_operator": by_operator,
            "by_branch": by_branch,
        },
    )


@login_required
def settings_messenger_inbox_edit(request: HttpRequest, inbox_id: int = None) -> HttpResponse:
    """
    Создание/редактирование Inbox.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    ensure_messenger_enabled_view()

    inbox = None
    if inbox_id:
        try:
            inbox = Inbox.objects.get(id=inbox_id)
        except Inbox.DoesNotExist:
            messages.error(request, "Inbox не найден.")
            return redirect("settings_messenger_overview")

    if request.method == "POST":
        action = request.POST.get("action", "").strip()
        
        if action == "regenerate_token" and inbox:
            # Регенерация widget_token
            try:
                old_token = inbox.widget_token
                inbox.widget_token = secrets.token_urlsafe(32)
                inbox.save()
                ui_logger.info(
                    "Widget token regenerated",
                    extra={"inbox_id": inbox.id, "old_token": old_token[:4] + "..."},
                )
                messages.warning(
                    request,
                    f"Токен виджета обновлён. Старый токен больше не работает. "
                    "Обновите код вставки на всех сайтах."
                )
                return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id)
            except Exception as e:
                ui_logger.error(
                    "Failed to regenerate widget token",
                    extra={"inbox_id": inbox.id if inbox else None},
                    exc_info=True,
                )
                messages.error(request, "Ошибка при обновлении токена. Попробуйте ещё раз.")
                return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id)
        
        if action == "delete_inbox" and inbox:
            # Безопасное удаление Inbox: не даём удалить, если есть связанные диалоги.
            from django.db.models import Exists, OuterRef

            has_conversations = Conversation.objects.filter(inbox_id=inbox.id).exists()
            if has_conversations:
                messages.error(
                    request,
                    "Нельзя удалить Inbox: к нему привязаны диалоги. "
                    "Сначала закройте или перенесите диалоги в другие Inbox'ы.",
                )
                return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id)

            try:
                inbox_id_for_log = inbox.id
                inbox_name_for_log = inbox.name
                inbox.delete()
                ui_logger.info(
                    "Inbox deleted",
                    extra={"inbox_id": inbox_id_for_log, "inbox_name": inbox_name_for_log},
                )
                messages.success(request, "Inbox удалён.")
                return redirect("settings_messenger_overview")
            except Exception:
                ui_logger.error(
                    "Failed to delete inbox",
                    extra={"inbox_id": inbox.id if inbox else None},
                    exc_info=True,
                )
                messages.error(request, "Ошибка при удалении Inbox. Попробуйте ещё раз.")
                return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id)
        
        # Обработка формы редактирования
        try:
            name = request.POST.get("name", "").strip()
            if not name:
                messages.error(request, "Название Inbox обязательно.")
                return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id if inbox else None)
            
            is_active = request.POST.get("is_active") == "on"
            # Тип канала и домен сайта (для website‑канала)
            channel_type = (request.POST.get("channel_type") or Channel.Type.WEBSITE).strip() or Channel.Type.WEBSITE
            valid_channel_values = {choice[0] for choice in Channel.Type.choices}
            if channel_type not in valid_channel_values:
                channel_type = Channel.Type.WEBSITE
            website_domain = (request.POST.get("website_domain") or "").strip().lower()
            
            # Настройки виджета из JSON (при редактировании сохраняем существующие ключи)
            widget_title = request.POST.get("widget_title", "").strip()
            widget_greeting = request.POST.get("widget_greeting", "").strip()
            widget_color = request.POST.get("widget_color", "").strip()
            widget_show_email = request.POST.get("widget_show_email") == "on"
            widget_show_phone = request.POST.get("widget_show_phone") == "on"
            
            if inbox and isinstance(inbox.settings, dict):
                settings_dict = dict(inbox.settings)
            else:
                settings_dict = {}
            if widget_title:
                settings_dict["title"] = widget_title
            if widget_greeting:
                settings_dict["greeting"] = widget_greeting
            if widget_color:
                settings_dict["color"] = widget_color
            settings_dict["show_email"] = widget_show_email
            settings_dict["show_phone"] = widget_show_phone

            # Согласие на обработку персональных данных и ссылка на политику
            privacy_url = (request.POST.get("privacy_url") or "").strip()
            privacy_text = (request.POST.get("privacy_text") or "").strip()
            if not privacy_text:
                privacy_text = "Даю согласие на обработку моих персональных данных для обработки заявки и получения обратной связи. Подробная информация — в Политике конфиденциальности."
            settings_dict["privacy"] = {"url": privacy_url, "text": privacy_text}

            # Рабочие часы
            working_hours_enabled = request.POST.get("working_hours_enabled") == "on"
            working_hours_tz = request.POST.get("working_hours_tz", "").strip() or "Europe/Moscow"
            schedule = {}
            for day in range(1, 8):
                start_val = request.POST.get(f"working_hours_{day}_start", "").strip()
                end_val = request.POST.get(f"working_hours_{day}_end", "").strip()
                if start_val and end_val:
                    schedule[str(day)] = [start_val, end_val]
            settings_dict["working_hours"] = {
                "enabled": working_hours_enabled,
                "tz": working_hours_tz,
                "schedule": schedule,
            }

            # Офлайн-режим
            offline_enabled = request.POST.get("offline_enabled") == "on"
            offline_message = (request.POST.get("offline_message") or "").strip()
            settings_dict["offline"] = {
                "enabled": offline_enabled,
                "message": offline_message or "Сейчас никого нет. Оставьте заявку — мы ответим в рабочее время.",
            }

            # Оценка диалога
            rating_enabled = request.POST.get("rating_enabled") == "on"
            rating_type = (request.POST.get("rating_type") or "stars").strip() or "stars"
            rating_max_score = 5
            if rating_type == "nps":
                rating_max_score = 10
            else:
                try:
                    rating_max_score = int(request.POST.get("rating_max_score", 5))
                    if rating_max_score < 1 or rating_max_score > 5:
                        rating_max_score = 5
                except (ValueError, TypeError):
                    rating_max_score = 5
            settings_dict["rating"] = {
                "enabled": rating_enabled,
                "type": rating_type,
                "max_score": rating_max_score,
            }

            # Вложения
            attachments_enabled = request.POST.get("attachments_enabled") == "on"
            try:
                attachments_max_mb = int(request.POST.get("attachments_max_mb", 5))
                if attachments_max_mb < 1:
                    attachments_max_mb = 5
                elif attachments_max_mb > 50:
                    attachments_max_mb = 50
            except (ValueError, TypeError):
                attachments_max_mb = 5
            settings_dict["attachments"] = {
                "enabled": attachments_enabled,
                "max_file_size_mb": attachments_max_mb,
                "allowed_content_types": [
                    "image/jpeg",
                    "image/png",
                    "image/gif",
                    "image/webp",
                    "application/pdf",
                ],
            }

            # Безопасность: allowlist доменов
            raw_domains = (request.POST.get("security_allowed_domains") or "").strip()
            domains: list[str] = []
            for line in raw_domains.replace(",", "\n").splitlines():
                d = line.strip().lower()
                if d:
                    domains.append(d)
            # Если список доменов пуст, но указан основной домен сайта — добавим его в allowlist
            if not domains and website_domain:
                domains.append(website_domain)
            settings_dict["security"] = {"allowed_domains": domains}

            # Конфигурация канала и сайта
            channel_cfg = settings_dict.get("channel") if isinstance(settings_dict.get("channel"), dict) else {}
            channel_cfg["type"] = channel_type
            settings_dict["channel"] = channel_cfg

            website_cfg = settings_dict.get("website") if isinstance(settings_dict.get("website"), dict) else {}
            if website_domain:
                website_cfg["primary_domain"] = website_domain
            # Дублируем список доменов для удобства просмотра в настройках сайта
            website_cfg["allowed_domains"] = domains
            settings_dict["website"] = website_cfg

            # Интеграции: webhook
            integrations_cfg = settings_dict.get("integrations") if isinstance(settings_dict.get("integrations"), dict) else {}
            webhook_url = (request.POST.get("webhook_url") or "").strip()
            webhook_enabled = request.POST.get("webhook_enabled") == "on"
            webhook_secret = (request.POST.get("webhook_secret") or "").strip()
            webhook_events: list[str] = []
            if request.POST.get("webhook_event_conversation_created") == "on":
                webhook_events.append("conversation.created")
            if request.POST.get("webhook_event_conversation_closed") == "on":
                webhook_events.append("conversation.closed")
            if request.POST.get("webhook_event_message_in") == "on":
                webhook_events.append("message.in")
            if request.POST.get("webhook_event_message_out") == "on":
                webhook_events.append("message.out")
            integrations_cfg["webhook"] = {
                "enabled": bool(webhook_enabled and webhook_url),
                "url": webhook_url,
                "secret": webhook_secret,
                "events": webhook_events,
            }
            settings_dict["integrations"] = integrations_cfg

            # Автоматизация: автоответ на первый входящий месседж
            automation_cfg = settings_dict.get("automation") if isinstance(settings_dict.get("automation"), dict) else {}
            auto_reply_enabled = request.POST.get("auto_reply_enabled") == "on"
            auto_reply_body = (request.POST.get("auto_reply_body") or "").strip()
            automation_cfg["auto_reply"] = {
                "enabled": auto_reply_enabled,
                "body": auto_reply_body,
            }
            settings_dict["automation"] = automation_cfg

            # Feature flags (по inbox)
            features_cfg = settings_dict.get("features") if isinstance(settings_dict.get("features"), dict) else {}
            features_cfg["sse"] = request.POST.get("features_sse_enabled") == "on"
            settings_dict["features"] = features_cfg

            if inbox:
                # Редактирование существующего
                inbox.name = name
                inbox.is_active = is_active
                inbox.settings = settings_dict
                inbox.save()
                ui_logger.info(
                    "Inbox updated",
                    extra={"inbox_id": inbox.id, "inbox_name": name},
                )
                # Обновляем/создаём Channel для Inbox
                Channel.objects.update_or_create(
                    inbox=inbox,
                    defaults={
                        "type": channel_type,
                        "config": {"primary_domain": website_domain, "allowed_domains": domains},
                        "is_active": True,
                    },
                )
                messages.success(request, "Inbox обновлён.")
            else:
                # Создание нового
                branch_id = request.POST.get("branch", "").strip()
                branch = None
                if branch_id:
                    try:
                        from accounts.models import Branch
                        branch = Branch.objects.get(id=int(branch_id))
                    except (Branch.DoesNotExist, ValueError, TypeError) as e:
                        ui_logger.warning(
                            "Failed to create inbox: invalid branch",
                            extra={"branch_id": branch_id},
                            exc_info=True,
                        )
                        messages.error(request, "Неверный филиал.")
                        return redirect("settings_messenger_overview")
                # branch=None — глобальный inbox (маршрутизация по GeoIP и правилам)
                
                inbox = Inbox.objects.create(
                    name=name,
                    branch=branch,
                    is_active=is_active,
                    settings=settings_dict,
                )
                Channel.objects.update_or_create(
                    inbox=inbox,
                    defaults={
                        "type": channel_type,
                        "config": {"primary_domain": website_domain, "allowed_domains": domains},
                        "is_active": True,
                    },
                )
                ui_logger.info(
                    "Inbox created",
                    extra={"inbox_id": inbox.id, "inbox_name": name, "branch_id": branch.id if branch else None},
                )
                messages.success(request, "Источник создан.")
                return redirect("settings_messenger_inbox_ready", inbox_id=inbox.id)
            
            return redirect("settings_messenger_overview")
        
        except Exception as e:
            ui_logger.error(
                "Failed to save inbox",
                extra={"inbox_id": inbox.id if inbox else None},
                exc_info=True,
            )
            messages.error(request, "Ошибка при сохранении. Попробуйте ещё раз.")
            return redirect("settings_messenger_inbox_edit", inbox_id=inbox.id if inbox else None)

    # GET запрос - показать форму
    from accounts.models import Branch
    branches = Branch.objects.order_by("name")
    
    from django.conf import settings
    base_url = getattr(settings, "PUBLIC_BASE_URL", request.build_absolute_uri("/").rstrip("/"))

    # Рабочие часы / офлайн / оценка / вложения для формы
    wh = (getattr(inbox, "settings", None) or {}).get("working_hours") or {}
    schedule = wh.get("schedule") or {}
    day_labels = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    working_hours_days = []
    for i in range(1, 8):
        slot = schedule.get(str(i), [])
        is_off = not (isinstance(slot, (list, tuple)) and len(slot) >= 2)
        working_hours_days.append({
            "num": i,
            "label": day_labels[i - 1],
            "start": (slot[0] if len(slot) > 0 else ""),
            "end": (slot[1] if len(slot) > 1 else ""),
            "off": is_off,
        })
    working_hours = {
        "enabled": wh.get("enabled", False),
        "tz": wh.get("tz", "Europe/Moscow"),
    }

    offline_cfg = (getattr(inbox, "settings", None) or {}).get("offline") or {}
    offline_settings = {
        "enabled": offline_cfg.get("enabled", False),
        "message": offline_cfg.get("message", "Сейчас никого нет. Оставьте заявку — мы ответим в рабочее время."),
    }

    rating_cfg = (getattr(inbox, "settings", None) or {}).get("rating") or {}
    rating_settings = {
        "enabled": rating_cfg.get("enabled", False),
        "type": rating_cfg.get("type", "stars"),
        "max_score": rating_cfg.get("max_score", 5),
    }

    att_cfg = (getattr(inbox, "settings", None) or {}).get("attachments") or {}
    attachment_settings = {
        "enabled": att_cfg.get("enabled", True),
        "max_file_size_mb": att_cfg.get("max_file_size_mb", 5),
    }

    settings_obj = getattr(inbox, "settings", None) or {}
    sec_cfg = settings_obj.get("security") or {}
    security_settings = {
        "allowed_domains": sec_cfg.get("allowed_domains") or [],
    }
    website_cfg = settings_obj.get("website") or {}
    channel_cfg = settings_obj.get("channel") or {}
    channel_type = channel_cfg.get("type", Channel.Type.WEBSITE)
    website_settings = {
        "domain": website_cfg.get("primary_domain", ""),
        "channel_type": channel_type,
    }
    integrations_cfg = settings_obj.get("integrations") or {}
    webhook_cfg = integrations_cfg.get("webhook") or {}
    integration_settings = {
        "webhook_enabled": webhook_cfg.get("enabled", False),
        "webhook_url": webhook_cfg.get("url", ""),
        "webhook_secret": webhook_cfg.get("secret", ""),
        "webhook_events": webhook_cfg.get("events") or [],
    }
    automation_cfg = (getattr(inbox, "settings", None) or {}).get("automation") or {}
    auto_reply_cfg = automation_cfg.get("auto_reply") or {}
    automation_settings = {
        "auto_reply_enabled": auto_reply_cfg.get("enabled", False),
        "auto_reply_body": auto_reply_cfg.get("body", ""),
    }
    features_cfg = (getattr(inbox, "settings", None) or {}).get("features") or {}
    feature_settings = {
        "sse": features_cfg.get("sse", True),
    }

    settings_obj_for_widget = getattr(inbox, "settings", None) or {}
    privacy_cfg = settings_obj_for_widget.get("privacy") or {}
    wh_cfg = settings_obj_for_widget.get("working_hours") or {}
    schedule = wh_cfg.get("schedule") or {}
    from messenger.utils import compact_working_hours_display
    working_hours_display = compact_working_hours_display(wh_cfg.get("enabled"), schedule)
    widget_display = {
        "title": settings_obj_for_widget.get("title", "Чат с поддержкой"),
        "greeting": settings_obj_for_widget.get("greeting", ""),
        "color": settings_obj_for_widget.get("color", "#01948E"),
        "show_email": settings_obj_for_widget.get("show_email", False),
        "show_phone": settings_obj_for_widget.get("show_phone", False),
        "privacy_url": privacy_cfg.get("url", ""),
        "privacy_text": privacy_cfg.get("text", "Даю согласие на обработку моих персональных данных для обработки заявки и получения обратной связи. Подробная информация — в Политике конфиденциальности."),
        "working_hours_display": working_hours_display,
    }

    return render(
        request,
        "ui/settings/messenger_inbox_form.html",
        {
            "inbox": inbox,
            "branches": branches,
            "base_url": base_url,
            "channel_type": channel_type,
            "channel_types": Channel.Type.choices,
            "website_settings": website_settings,
            "working_hours_days": working_hours_days,
            "working_hours": working_hours,
            "offline_settings": offline_settings,
            "rating_settings": rating_settings,
            "attachment_settings": attachment_settings,
            "security_settings": security_settings,
            "integration_settings": integration_settings,
            "automation_settings": automation_settings,
            "feature_settings": feature_settings,
            "widget_display": widget_display,
        },
    )


@login_required
def settings_messenger_routing_list(request: HttpRequest) -> HttpResponse:
    """
    Список правил маршрутизации.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    ensure_messenger_enabled_view()

    rules = RoutingRule.objects.select_related("branch", "inbox").prefetch_related("regions").order_by("priority", "id")

    return render(
        request,
        "ui/settings/messenger_routing_list.html",
        {
            "rules": rules,
        },
    )


@login_required
def settings_messenger_routing_edit(request: HttpRequest, rule_id: int = None) -> HttpResponse:
    """
    Создание/редактирование правила маршрутизации.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    ensure_messenger_enabled_view()

    rule = None
    if rule_id:
        rule = get_object_or_404(RoutingRule.objects.prefetch_related("regions"), id=rule_id)

    if request.method == "POST":
        name = request.POST.get("name", "").strip()
        inbox_id = request.POST.get("inbox")
        branch_id = request.POST.get("branch")
        priority = request.POST.get("priority", "100").strip()
        is_fallback = request.POST.get("is_fallback") == "on"
        is_active = request.POST.get("is_active") == "on"
        region_ids = request.POST.getlist("regions")

        if not name or not inbox_id or not branch_id:
            messages.error(request, "Заполните все обязательные поля.")
        else:
            try:
                from accounts.models import Branch
                from companies.models import Region
                
                inbox = Inbox.objects.get(id=int(inbox_id))
                branch = Branch.objects.get(id=int(branch_id))
                priority_int = int(priority)
                
                if rule:
                    # Редактирование
                    rule.name = name
                    rule.inbox = inbox
                    rule.branch = branch
                    rule.priority = priority_int
                    rule.is_fallback = is_fallback
                    rule.is_active = is_active
                    rule.save()
                    rule.regions.clear()
                else:
                    # Создание
                    rule = RoutingRule.objects.create(
                        name=name,
                        inbox=inbox,
                        branch=branch,
                        priority=priority_int,
                        is_fallback=is_fallback,
                        is_active=is_active,
                    )
                
                # Добавить регионы
                if region_ids:
                    regions = Region.objects.filter(id__in=[int(rid) for rid in region_ids])
                    rule.regions.set(regions)
                
                messages.success(request, "Правило маршрутизации сохранено.")
                return redirect("settings_messenger_routing_list")
            except (Inbox.DoesNotExist, Branch.DoesNotExist, ValueError, TypeError) as e:
                messages.error(request, f"Ошибка: {str(e)}")

    # GET запрос - показать форму
    from accounts.models import Branch
    from companies.models import Region
    
    inboxes = Inbox.objects.filter(is_active=True).select_related("branch").order_by("name")
    branches = Branch.objects.order_by("name")
    regions = Region.objects.order_by("name")
    
    selected_region_ids = []
    if rule:
        selected_region_ids = list(rule.regions.values_list("id", flat=True))

    return render(
        request,
        "ui/settings/messenger_routing_form.html",
        {
            "rule": rule,
            "inboxes": inboxes,
            "branches": branches,
            "regions": regions,
            "selected_region_ids": selected_region_ids,
        },
    )


@login_required
def settings_messenger_routing_delete(request: HttpRequest, rule_id: int) -> HttpResponse:
    """
    Удаление правила маршрутизации.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    
    ensure_messenger_enabled_view()

    rule = get_object_or_404(RoutingRule, id=rule_id)
    
    if request.method == "POST":
        rule.delete()
        messages.success(request, "Правило маршрутизации удалено.")
        return redirect("settings_messenger_routing_list")

    return redirect("settings_messenger_routing_list")


@login_required
def settings_messenger_canned_list(request: HttpRequest) -> HttpResponse:
    """
    Список шаблонных ответов.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    ensure_messenger_enabled_view()

    responses = (
        CannedResponse.objects
        .select_related("branch", "created_by")
        .order_by("-created_at")
    )

    return render(
        request,
        "ui/settings/messenger_canned_list.html",
        {
            "responses": responses,
        },
    )


@login_required
def settings_messenger_canned_edit(request: HttpRequest, response_id: int = None) -> HttpResponse:
    """
    Создание/редактирование шаблонного ответа.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    ensure_messenger_enabled_view()

    canned = None
    if response_id:
        canned = get_object_or_404(CannedResponse, id=response_id)

    if request.method == "POST":
        title = request.POST.get("title", "").strip()
        body = request.POST.get("body", "").strip()
        branch_id = request.POST.get("branch", "").strip()

        if not title or not body:
            messages.error(request, "Заполните название и текст ответа.")
        else:
            try:
                from accounts.models import Branch

                branch = None
                if branch_id:
                    branch = Branch.objects.get(id=int(branch_id))

                if canned:
                    canned.title = title
                    canned.body = body
                    canned.branch = branch
                    canned.save()
                else:
                    canned = CannedResponse.objects.create(
                        title=title,
                        body=body,
                        branch=branch,
                        created_by=request.user,
                    )

                messages.success(request, "Шаблонный ответ сохранён.")
                return redirect("settings_messenger_canned_list")
            except (Branch.DoesNotExist, ValueError, TypeError) as e:
                messages.error(request, f"Ошибка: {str(e)}")

    from accounts.models import Branch

    branches = Branch.objects.order_by("name")

    return render(
        request,
        "ui/settings/messenger_canned_form.html",
        {
            "canned": canned,
            "branches": branches,
        },
    )


@login_required
def settings_messenger_canned_delete(request: HttpRequest, response_id: int) -> HttpResponse:
    """
    Удаление шаблонного ответа.
    """
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")

    ensure_messenger_enabled_view()

    canned = get_object_or_404(CannedResponse, id=response_id)

    if request.method == "POST":
        canned.delete()
        messages.success(request, "Шаблонный ответ удалён.")
        return redirect("settings_messenger_canned_list")

    return redirect("settings_messenger_canned_list")


# ─── Campaigns ───────────────────────────────────────────────────────────

@login_required
def settings_messenger_campaigns(request: HttpRequest) -> HttpResponse:
    """CRUD для кампаний (список + создание/редактирование inline)."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    ensure_messenger_enabled_view()

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            inbox_id = request.POST.get("inbox")
            Campaign.objects.create(
                inbox_id=inbox_id,
                title=request.POST.get("title", "").strip(),
                message=request.POST.get("message", "").strip(),
                url_pattern=request.POST.get("url_pattern", "*").strip() or "*",
                time_on_page=int(request.POST.get("time_on_page", 10) or 10),
                status=request.POST.get("status", "active"),
            )
            messages.success(request, "Кампания создана.")
        elif action == "delete":
            Campaign.objects.filter(id=request.POST.get("campaign_id")).delete()
            messages.success(request, "Кампания удалена.")
        elif action == "toggle":
            c = Campaign.objects.filter(id=request.POST.get("campaign_id")).first()
            if c:
                c.status = "disabled" if c.status == "active" else "active"
                c.save(update_fields=["status"])
        return redirect("settings_messenger_campaigns")

    campaigns = Campaign.objects.select_related("inbox").order_by("-created_at")
    inboxes = Inbox.objects.filter(is_active=True).order_by("name")
    return render(request, "ui/settings/messenger_campaigns.html", {
        "campaigns": campaigns,
        "inboxes": inboxes,
    })


# ─── Automation Rules ────────────────────────────────────────────────────

@login_required
def settings_messenger_automation(request: HttpRequest) -> HttpResponse:
    """CRUD для правил автоматизации + макросов."""
    if not require_admin(request.user):
        messages.error(request, "Доступ запрещён.")
        return redirect("dashboard")
    ensure_messenger_enabled_view()

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "delete_rule":
            AutomationRule.objects.filter(id=request.POST.get("rule_id")).delete()
            messages.success(request, "Правило удалено.")
        elif action == "toggle_rule":
            r = AutomationRule.objects.filter(id=request.POST.get("rule_id")).first()
            if r:
                r.is_active = not r.is_active
                r.save(update_fields=["is_active"])
        elif action == "delete_macro":
            Macro.objects.filter(id=request.POST.get("macro_id")).delete()
            messages.success(request, "Макрос удалён.")
        return redirect("settings_messenger_automation")

    rules = AutomationRule.objects.select_related("inbox").order_by("-created_at")
    macros = Macro.objects.select_related("user").order_by("name")
    inboxes = Inbox.objects.filter(is_active=True).order_by("name")
    return render(request, "ui/settings/messenger_automation.html", {
        "rules": rules,
        "macros": macros,
        "inboxes": inboxes,
    })
