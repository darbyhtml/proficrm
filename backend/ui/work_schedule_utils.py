import re
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

# 0 = Monday ... 6 = Sunday
_DAYS: List[Tuple[str, int]] = [("Пн", 0), ("Вт", 1), ("Ср", 2), ("Чт", 3), ("Пт", 4), ("Сб", 5), ("Вс", 6)]
_DAY_TO_IDX: Dict[str, int] = {k.lower(): v for k, v in _DAYS}


def _fmt_hhmm(t: time) -> str:
    return f"{t.hour:02d}:{t.minute:02d}"


_DASH_RE = re.compile(r"[‐‑‒–—−]")  # various dash-like chars -> '-'


def _normalize_text_for_parse(text: str) -> str:
    """
    Подготовка текста для парсинга:
    - приводит дни недели к сокращениям
    - нормализует тире/дефисы
    - унифицирует разделители
    """
    s = (text or "").strip().lower()
    if not s:
        return ""
    s = s.replace("\t", " ")

    # day names -> короткие
    s = (
        s.replace("понедельник", "пн")
        .replace("вторник", "вт")
        .replace("среда", "ср")
        .replace("четверг", "чт")
        .replace("пятница", "пт")
        .replace("суббота", "сб")
        .replace("воскресенье", "вс")
    )
    # "суббота и воскресенье" / "сб и вс" -> "сб, вс"
    s = re.sub(r"\b(сб|вс)\s+и\s+(сб|вс)\b", r"\1, \2", s)

    # dash variants -> '-'
    s = _DASH_RE.sub("-", s)

    # separators: pipe -> semicolon
    s = s.replace("|", ";")

    # split one-line comma-separated day blocks into separate chunks:
    # "... , пт: ..." / "... , сб-вс выходной" / "... , выходные: ..."
    s = re.sub(
        r",(?=\s*(?:пн|вт|ср|чт|пт|сб|вс|будни|выходн|ежедневно|каждый\s+день|без\s+выходных)\b)",
        ";",
        s,
    )
    return s.strip()


def _parse_time_token(s: str) -> Optional[time]:
    s = (s or "").strip()
    if not s:
        return None
    # 9, 09, 9:00, 09:00, 9.00, 9-00
    m = re.match(r"^(\d{1,2})(?:[:.\-](\d{2}))?$", s)
    if not m:
        return None
    h = int(m.group(1))
    mm = int(m.group(2) or "00")
    if not (0 <= h <= 23 and 0 <= mm <= 59):
        return None
    return time(hour=h, minute=mm)


def _expand_day_spec(day_spec: str) -> List[int]:
    """
    Принимает строку вроде: "Пн-Пт", "Пн, Ср, Пт", "Ежедневно" и возвращает индексы дней.
    """
    s = (day_spec or "").strip().lower()
    if not s:
        return []

    s = s.replace("понедельник", "пн").replace("вторник", "вт").replace("среда", "ср").replace("четверг", "чт")
    s = s.replace("пятница", "пт").replace("суббота", "сб").replace("воскресенье", "вс")
    s = s.replace("без выходных", "пн-вс")
    s = s.replace("ежедневно", "пн-вс").replace("каждый день", "пн-вс")
    s = s.replace("будни", "пн-пт").replace("рабочие дни", "пн-пт").replace("по будням", "пн-пт")
    s = s.replace("выходные", "сб-вс").replace("выходные дни", "сб-вс")

    # unify dashes (incl. non-breaking hyphen etc.)
    s = _DASH_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s)

    out: List[int] = []

    # Split by comma/space/semicolon but keep ranges like пн-пт
    parts = [p.strip() for p in re.split(r"[,;/]+", s) if p.strip()]
    if not parts:
        parts = [s]

    for p in parts:
        p = p.strip()
        if not p:
            continue
        # range: пн-пт
        m = re.match(r"^(пн|вт|ср|чт|пт|сб|вс)\s*-\s*(пн|вт|ср|чт|пт|сб|вс)$", p)
        if m:
            a = _DAY_TO_IDX[m.group(1)]
            b = _DAY_TO_IDX[m.group(2)]
            if a <= b:
                out.extend(list(range(a, b + 1)))
            else:
                # wrap: пт-пн
                out.extend(list(range(a, 7)) + list(range(0, b + 1)))
            continue
        # single token
        if p in _DAY_TO_IDX:
            out.append(_DAY_TO_IDX[p])
            continue
        # maybe "пн вт ср" etc
        toks = [t.strip() for t in re.split(r"\s+", p) if t.strip()]
        for t in toks:
            if t in _DAY_TO_IDX:
                out.append(_DAY_TO_IDX[t])

    # de-dup preserving order
    seen = set()
    uniq: List[int] = []
    for d in out:
        if d in seen:
            continue
        seen.add(d)
        uniq.append(d)
    return uniq


def parse_work_schedule(text: str) -> Dict[int, List[Tuple[time, time]]]:
    """
    Пытается распарсить свободный текст режима работы в карту:
    day_idx -> [(start, end), ...].
    """
    src = (text or "").strip()
    if not src:
        return {}

    s = _normalize_text_for_parse(src)
    if not s:
        return {}
    # split into chunks
    chunks: list[str] = []
    for part in re.split(r"[\n\r]+", s):
        part = part.strip()
        if not part:
            continue
        chunks.extend([p.strip() for p in re.split(r"[;]+", part) if p.strip()])

    schedule: Dict[int, List[Tuple[time, time]]] = {i: [] for i in range(7)}
    any_parsed = False
    any_interval = False

    # quick "24/7" / "круглосуточно"
    if re.search(r"\b24\s*/\s*7\b", s) or "круглосуточ" in s:
        for i in range(7):
            schedule[i] = [(time(0, 0), time(23, 59))]
        return schedule

    day_token_re = r"(пн|вт|ср|чт|пт|сб|вс)"
    # include 'ежедневно' / 'каждый день' / 'без выходных' at start
    day_spec_re = re.compile(
        rf"((?:{day_token_re}|будни|выходн(?:ые)?|ежедневно|каждый\s+день|без\s+выходных)"
        rf"(?:\s*-\s*{day_token_re})?"
        rf"(?:\s*[, ]\s*{day_token_re})*)"
    )

    for ch in chunks:
        if not ch:
            continue

        # detect "выходной/закрыто"
        is_off = bool(re.search(r"(закрыт|не\s*работ|выходн)", ch)) and not bool(re.search(r"без\s+выходн", ch))

        # split day part and rest robustly:
        # supports both "пн-пт: 09:00-18:00" and "пн-пт 8:30-17:00"
        day_part = ""
        rest = ""
        m = day_spec_re.match(ch)
        if m:
            day_part = m.group(1).strip()
            rest = ch[m.end():].lstrip(" :").strip()
        else:
            # fallback for phrases without explicit day prefix
            if re.search(r"\bпо\s+будням\b|\bбудни(?:е)?\b|\bбудние\s+дни\b", ch):
                day_part = "будни"
                rest = ch
            elif re.search(r"\bежедневно\b|\bкаждый\s+день\b|\bбез\s+выходных\b", ch):
                day_part = "ежедневно"
                rest = ch
            elif re.search(r"\bвыходн", ch) and not re.search(r"без\s+выходн", ch):
                day_part = "выходные"
                rest = ch

        days = _expand_day_spec(day_part) if day_part else []
        if not days:
            # Если день не указан — пропускаем (иначе много ложных срабатываний).
            continue

        if is_off:
            for d in days:
                schedule[d] = []
            any_parsed = True
            continue

        intervals: List[Tuple[time, time]] = []

        # time ranges like 9:00-18:00, 9-18, 09.00-18.00
        for m in re.finditer(r"\b(\d{1,2})(?:[:.\-](\d{2}))?\s*-\s*(\d{1,2})(?:[:.\-](\d{2}))?\b", rest):
            t1 = _parse_time_token(f"{m.group(1)}:{m.group(2) or '00'}")
            t2 = _parse_time_token(f"{m.group(3)}:{m.group(4) or '00'}")
            if t1 and t2:
                intervals.append((t1, t2))

        # "с 8:00 до 17:00"
        for m in re.finditer(r"\bс\s*(\d{1,2})(?:[:.\-](\d{2}))\s*до\s*(\d{1,2})(?:[:.\-](\d{2}))\b", rest):
            t1 = _parse_time_token(f"{m.group(1)}:{m.group(2) or '00'}")
            t2 = _parse_time_token(f"{m.group(3)}:{m.group(4) or '00'}")
            if t1 and t2:
                intervals.append((t1, t2))

        if not intervals:
            continue

        for d in days:
            schedule[d].extend(intervals)
        any_parsed = True
        any_interval = True

    if not any_parsed or not any_interval:
        return {}

    # normalize ordering per day
    for d in range(7):
        schedule[d] = sorted(schedule[d], key=lambda x: (x[0].hour, x[0].minute, x[1].hour, x[1].minute))
    return schedule


def normalize_work_schedule(text: str) -> str:
    """
    Приводит текст режима работы к читаемому каноническому виду (если получилось распарсить).
    В противном случае — только нормализует время HH:MM и пробелы.
    """
    raw = (text or "").strip()
    if not raw:
        return ""

    # First: normalize to improve parsing robustness.
    raw_for_parse = _normalize_text_for_parse(raw)

    # Always normalize time tokens like 9.00 -> 09:00
    def _fmt_time_tokens(s: str) -> str:
        return re.sub(
            r"\b(\d{1,2})[:.\-](\d{2})(?::\d{2})?\b",
            lambda m: f"{int(m.group(1)):02d}:{m.group(2)}",
            s,
        )

    schedule = parse_work_schedule(raw_for_parse)
    if not schedule:
        # best-effort for arbitrary text
        out = _fmt_time_tokens(raw)
        out = out.replace("\r\n", "\n").replace("\r", "\n").strip()
        return out

    # build per-day representation
    per_day: List[str] = []
    for i in range(7):
        intervals = schedule.get(i) or []
        if not intervals:
            per_day.append("выходной")
            continue
        parts = [f"{_fmt_hhmm(a)}–{_fmt_hhmm(b)}" for a, b in intervals]
        per_day.append(", ".join(parts))

    # If all days identical:
    if len(set(per_day)) == 1:
        v = per_day[0]
        return f"Ежедневно: {v}"

    # group consecutive days with same value
    lines: List[str] = []
    i = 0
    while i < 7:
        v = per_day[i]
        j = i
        while j + 1 < 7 and per_day[j + 1] == v:
            j += 1
        if i == j:
            day_lbl = _DAYS[i][0]
        else:
            day_lbl = f"{_DAYS[i][0]}–{_DAYS[j][0]}"
        lines.append(f"{day_lbl}: {v}")
        i = j + 1

    return "\n".join(lines).strip()


def get_worktime_status_from_schedule(
    schedule_text: str,
    *,
    now_tz: datetime,
) -> Tuple[str, Optional[int]]:
    """
    Возвращает (status, minutes_left).
    status: ok | warn_end | off | unknown
    minutes_left: осталось минут до конца текущего интервала (если ok/warn_end)
    """
    schedule = parse_work_schedule(schedule_text)
    if not schedule:
        return ("unknown", None)

    tz = now_tz.tzinfo
    if tz is None:
        return ("unknown", None)

    today: date = now_tz.date()
    dow = now_tz.weekday()
    prev_dow = (dow - 1) % 7

    # Build intervals for today + spillovers from yesterday
    intervals_dt: List[Tuple[datetime, datetime]] = []

    for start_t, end_t in schedule.get(dow, []) or []:
        start_dt = datetime.combine(today, start_t, tzinfo=tz)
        end_dt = datetime.combine(today, end_t, tzinfo=tz)
        if end_dt <= start_dt:
            end_dt = end_dt + timedelta(days=1)
        intervals_dt.append((start_dt, end_dt))

    # spillovers from yesterday (intervals crossing midnight)
    yesterday = today - timedelta(days=1)
    for start_t, end_t in schedule.get(prev_dow, []) or []:
        start_dt = datetime.combine(yesterday, start_t, tzinfo=tz)
        end_dt = datetime.combine(yesterday, end_t, tzinfo=tz)
        if end_dt <= start_dt:
            end_dt = end_dt + timedelta(days=1)
            intervals_dt.append((start_dt, end_dt))

    intervals_dt.sort(key=lambda x: x[0])

    for start_dt, end_dt in intervals_dt:
        if start_dt <= now_tz <= end_dt:
            minutes_left = int((end_dt - now_tz).total_seconds() // 60)
            if minutes_left <= 60:
                return ("warn_end", minutes_left)
            return ("ok", minutes_left)

    return ("off", None)

