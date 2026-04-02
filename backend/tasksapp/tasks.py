"""
Celery-задачи для tasksapp.

generate_recurring_tasks — генерирует экземпляры повторяющихся задач
на HORIZON_DAYS дней вперёд. Запускается ежедневно.

Алгоритм:
  1. Найти все задачи-шаблоны (recurrence_rrule != "", parent_recurring_task=NULL).
  2. Для каждой: вычислить все вхождения RRULE в окно
     (recurrence_next_generate_after, now + HORIZON_DAYS].
     dtstart всегда = template.due_at (оригинальное начало правила),
     чтобы COUNT/UNTIL считались от исходной точки, а не скользили.
  3. Создать экземпляр-задачу для каждого вхождения (без recurrence_rrule,
     с parent_recurring_task → шаблон).
  4. Обновить recurrence_next_generate_after = последнее вхождение.
"""
import logging
from datetime import timedelta

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# Насколько вперёд генерировать экземпляры (дней).
HORIZON_DAYS = 30


def _parse_rrule_occurrences(rrule_str: str, dtstart, after, until):
    """
    Возвращает список datetime-вхождений правила rrule_str (с dtstart как
    оригинальной точкой отсчёта) строго в диапазоне (after, until].

    dtstart — оригинальное начало правила (template.due_at).
    after   — исключительная нижняя граница (уже сгенерированные пропускаем).
    until   — включительная верхняя граница (горизонт).

    Поддерживает FREQ=DAILY/WEEKLY/MONTHLY/YEARLY + INTERVAL, BYDAY,
    BYMONTHDAY, COUNT, UNTIL.

    Возвращает [] при любой ошибке парсинга.
    """
    try:
        from dateutil.rrule import rrulestr
        rule = rrulestr(rrule_str, dtstart=dtstart, ignoretz=False)
        result = []
        for d in rule:
            if d > until:
                break
            if d > after:
                result.append(d)
        return result
    except Exception as exc:
        logger.warning("recurrence: не удалось распарсить RRULE %r: %s", rrule_str, exc)
        return []


@shared_task(name="tasksapp.tasks.generate_recurring_tasks", max_retries=0)
def generate_recurring_tasks():
    """
    Генерирует экземпляры повторяющихся задач на HORIZON_DAYS дней вперёд.
    Запускается ежедневно (CELERY_BEAT_SCHEDULE).
    """
    from tasksapp.models import Task

    now = timezone.now()
    horizon = now + timedelta(days=HORIZON_DAYS)

    # Только задачи-шаблоны (не сгенерированные экземпляры)
    templates = Task.objects.filter(
        recurrence_rrule__gt="",
        parent_recurring_task__isnull=True,
    ).select_related("created_by", "assigned_to", "company", "type")

    total_created = 0
    total_templates = templates.count()

    for template in templates:
        # dtstart = оригинальная точка отсчёта правила (для корректного COUNT/UNTIL)
        dtstart = template.due_at or now

        # after = исключительная нижняя граница: что уже сгенерировано
        if template.recurrence_next_generate_after:
            after = template.recurrence_next_generate_after
        else:
            # Первый запуск: генерируем начиная с dtstart - 1s (включаем первое вхождение)
            after = dtstart - timedelta(seconds=1)

        if after >= horizon:
            continue  # уже всё сгенерировано вперёд

        occurrences = _parse_rrule_occurrences(
            template.recurrence_rrule,
            dtstart=dtstart,
            after=after,
            until=horizon,
        )

        if not occurrences:
            continue

        created_count = 0
        last_occurrence = None

        for occ_dt in occurrences:
            # Защита от дублирования: проверяем по parent + due_at
            already_exists = Task.objects.filter(
                parent_recurring_task=template,
                due_at=occ_dt,
            ).exists()
            if already_exists:
                continue

            Task.objects.create(
                title=template.title,
                description=template.description,
                status=Task.Status.NEW,
                created_by=template.created_by,
                assigned_to=template.assigned_to,
                company=template.company,
                type=template.type,
                due_at=occ_dt,
                is_urgent=template.is_urgent,
                parent_recurring_task=template,
                # recurrence_rrule намеренно пустой — это экземпляр, не шаблон
            )
            created_count += 1
            last_occurrence = occ_dt

        if last_occurrence is not None:
            Task.objects.filter(pk=template.pk).update(
                recurrence_next_generate_after=last_occurrence,
            )

        if created_count:
            logger.info(
                "recurrence: шаблон %s (%r) → %s экземпляров до %s",
                template.pk, template.title, created_count,
                horizon.date(),
            )
        total_created += created_count

    logger.info(
        "generate_recurring_tasks: обработано %s шаблонов, создано %s экземпляров",
        total_templates, total_created,
    )
    return {"templates": total_templates, "created": total_created}
