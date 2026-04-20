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
    # Жёсткий лимит количества вхождений, чтобы RRULE с COUNT=10_000_000
    # или FREQ=SECONDLY не подвесил celery-worker.
    MAX_OCCURRENCES = 1000
    MAX_ITERATIONS = 100_000
    try:
        from dateutil.rrule import rrulestr
        rule = rrulestr(rrule_str, dtstart=dtstart, ignoretz=False)
        result = []
        iterated = 0
        for d in rule:
            iterated += 1
            if iterated > MAX_ITERATIONS:
                logger.warning(
                    "recurrence: RRULE %r превысил MAX_ITERATIONS=%d, обрываем",
                    rrule_str, MAX_ITERATIONS,
                )
                break
            if d > until:
                break
            if d > after:
                result.append(d)
                if len(result) >= MAX_OCCURRENCES:
                    logger.warning(
                        "recurrence: RRULE %r достиг MAX_OCCURRENCES=%d, обрываем",
                        rrule_str, MAX_OCCURRENCES,
                    )
                    break
        return result
    except Exception as exc:
        logger.warning("recurrence: не удалось распарсить RRULE %r: %s", rrule_str, exc)
        return []


@shared_task(name="tasksapp.tasks.generate_recurring_tasks", max_retries=0)
def generate_recurring_tasks():
    """
    Генерирует экземпляры повторяющихся задач на HORIZON_DAYS дней вперёд.
    Запускается ежедневно (CELERY_BEAT_SCHEDULE).

    Защита от race: редис-лок на всю задачу — если celery-beat запустит
    два воркера одновременно (retry, двойной beat), второй просто выйдет.
    """
    from django.core.cache import cache

    LOCK_KEY = "lock:generate_recurring_tasks"
    lock_acquired = cache.add(LOCK_KEY, "1", timeout=15 * 60)
    if not lock_acquired:
        logger.info("generate_recurring_tasks: уже выполняется, пропуск")
        return

    try:
        # Возвращаем результат inner-функции, чтобы тесты и сборщики метрик
        # видели {"templates": N, "created": N}. Без этого return метод возвращал
        # None и все recurrence-тесты падали TypeError: 'NoneType' is not subscriptable.
        return _generate_recurring_tasks_inner()
    finally:
        try:
            cache.delete(LOCK_KEY)
        except Exception:
            pass


def _generate_recurring_tasks_inner():
    from django.db import transaction
    from tasksapp.models import Task

    now = timezone.now()
    horizon = now + timedelta(days=HORIZON_DAYS)

    template_ids = list(
        Task.objects.filter(
            recurrence_rrule__gt="",
            parent_recurring_task__isnull=True,
        ).values_list("pk", flat=True)
    )

    total_created = 0
    total_templates = len(template_ids)

    for template_id in template_ids:
        with transaction.atomic():
            # SELECT FOR UPDATE: блокируем шаблон, чтобы параллельный воркер
            # (если redis-lock обойдётся) не сгенерировал тот же экземпляр.
            #
            # `of=("self",)` важно: select_related по nullable FK
            # (created_by/assigned_to/company/type → все SET_NULL) даёт LEFT OUTER JOIN,
            # а PostgreSQL не разрешает FOR UPDATE на nullable-side JOIN:
            # `NotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`.
            # С `of=("self",)` блокируется только сам Task row, joined таблицы не лочатся.
            try:
                template = (
                    Task.objects.select_for_update(of=("self",))
                    .select_related("created_by", "assigned_to", "company", "type")
                    .get(pk=template_id)
                )
            except Task.DoesNotExist:
                continue
            total_created += _process_template(template, now, horizon)

    logger.info(
        "generate_recurring_tasks: обработано %s шаблонов, создано %s экземпляров",
        total_templates, total_created,
    )
    return {"templates": total_templates, "created": total_created}


def _process_template(template, now, horizon) -> int:
    """Обработка одного шаблона внутри открытой транзакции. Возвращает
    количество созданных экземпляров."""
    from django.db import IntegrityError, transaction
    from tasksapp.models import Task

    # dtstart = оригинальная точка отсчёта правила (для корректного COUNT/UNTIL)
    dtstart = template.due_at or now

    # after = исключительная нижняя граница: что уже сгенерировано
    if template.recurrence_next_generate_after:
        after = template.recurrence_next_generate_after
    else:
        after = dtstart - timedelta(seconds=1)

    if after >= horizon:
        return 0

    occurrences = _parse_rrule_occurrences(
        template.recurrence_rrule,
        dtstart=dtstart,
        after=after,
        until=horizon,
    )
    if not occurrences:
        return 0

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

        try:
            # savepoint: если параллельный воркер успел вставить тот же экземпляр,
            # UniqueConstraint бросит IntegrityError — ловим и пропускаем,
            # не ломая внешнюю транзакцию.
            with transaction.atomic():
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
                )
        except IntegrityError:
            logger.info(
                "recurrence: уникальный конфликт при создании экземпляра "
                "template=%s due_at=%s — пропускаем",
                template.pk, occ_dt,
            )
            continue
        created_count += 1
        last_occurrence = occ_dt

    if last_occurrence is not None:
        Task.objects.filter(pk=template.pk).update(
            recurrence_next_generate_after=last_occurrence,
        )

    if created_count:
        logger.info(
            "recurrence: шаблон %s (%r) → %s экземпляров до %s",
            template.pk, template.title, created_count, horizon.date(),
        )
    return created_count
