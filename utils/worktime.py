# utils/worktime.py
from datetime import datetime, time, timedelta, date
import os

# Производственный календарь (праздники) через ENV: PROD_HOLIDAYS="2025-01-01,2025-01-02"
def _load_holidays():
    raw = os.getenv("PROD_HOLIDAYS", "").strip()
    if not raw:
        return set()
    out = set()
    for token in raw.split(","):
        token = token.strip()
        try:
            out.add(datetime.strptime(token, "%Y-%m-%d").date())
        except Exception:
            pass
    return out

HOLIDAYS = _load_holidays()

# Рабочие интервалы по дням недели (0=Пн ... 6=Вс)
# Пн–Чт: 07:45–17:00 (9ч15м = 9.25h), Пт: 07:45–15:45 (8h), Сб/Вс: выходной
SCHEDULE = {
    0: (time(5, 45), time(17, 0)),
    1: (time(5, 45), time(17, 0)),
    2: (time(5, 45), time(17, 0)),
    3: (time(5, 45), time(17, 0)),
    4: (time(5, 45), time(15, 45)),
    5: None,  # Saturday
    6: None,  # Sunday
}

BASE_DAY_HOURS = 8.0  # эквивалент рабочих «дней» считаем через 8 часов

def _is_workday(d: date) -> bool:
    if d in HOLIDAYS:
        return False
    wd = d.weekday()
    return SCHEDULE.get(wd) is not None

def _work_interval_for(d: date):
    return SCHEDULE.get(d.weekday())

def _work_interval_on(d: date) -> tuple[datetime, datetime] | None:
    """Рабочее окно конкретного дня в наивном UTC (без tzinfo)."""
    wd = d.weekday()
    if wd not in SCHEDULE or d in HOLIDAYS:
        return None
    start_t, end_t = SCHEDULE[wd]
    start_dt = datetime.combine(d, start_t)
    end_dt = datetime.combine(d, end_t)
    return (start_dt, end_dt)

def working_hours_between(start: datetime, end: datetime) -> float:
    """Считает рабочие часы между start и end по графику + праздникам."""
    if end <= start:
        return 0.0

    cur = start
    total = 0.0

    # нормализуем к минутной сетке
    start = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)

    # итерируемся по дням
    day = date(start.year, start.month, start.day)
    last_day = date(end.year, end.month, end.day)

    while day <= last_day:
        interval = _work_interval_for(day)
        if interval and _is_workday(day):
            wstart, wend = interval
            ws = datetime.combine(day, wstart)
            we = datetime.combine(day, wend)
            # пересечение [ws,we] с [start,end]
            seg_start = max(ws, start)
            seg_end = min(we, end)
            if seg_end > seg_start:
                total += (seg_end - seg_start).total_seconds() / 3600.0
        day += timedelta(days=1)

    return max(0.0, total)

def working_days_between(start: datetime, end: datetime) -> float:
    """Возвращает «эквивалент рабочих дней» через BASE_DAY_HOURS (по умолчанию 8 ч)."""
    return working_hours_between(start, end) / BASE_DAY_HOURS

def working_minutes_between(start: datetime | None, end: datetime | None) -> int:
    """
    Кол-во рабочих минут между start и end с учётом производственного календаря.
    ОЖИДАЕТ наивные datetime (tzinfo=None). Если пришли aware, просто отбрось tz заранее.
    """
    if not start or not end:
        return 0
    if end <= start:
        return 0

    total_minutes = 0
    cur_day = start.date()
    last_day = end.date()

    while cur_day <= last_day:
        win = _work_interval_on(cur_day)
        if win:
            ws, we = win  # окна на ВЕСЬ день
            # пересечение с [start, end]
            seg_start = max(ws, start) if cur_day == start.date() else ws
            seg_end   = min(we, end)   if cur_day == end.date()   else we
            if seg_end > seg_start:
                total_minutes += int((seg_end - seg_start).total_seconds() // 60)
        cur_day += timedelta(days=1)

    return max(total_minutes, 0)

# Если у тебя уже есть working_days_between(start, end), оставляй его как есть.
# Для полноты — можно добавить удобный конвертер:
def minutes_to_hours(minutes: int) -> float:
    return round(minutes / 60.0, 4)

