from datetime import datetime, time, timedelta, date
import os
import json

def load_worktime_config():
    """Загружает настройки рабочего времени из файла"""
    config_file = "worktime_config.json"
    if os.path.exists(config_file):
        with open(config_file, "r", encoding="utf-8") as f:
            return json.load(f)
    else:
        # Значения по умолчанию
        return {
            "holidays": "",
            "schedule": {
                "monday": {"start": "05:45", "end": "17:00"},
                "tuesday": {"start": "05:45", "end": "17:00"},
                "wednesday": {"start": "05:45", "end": "17:00"},
                "thursday": {"start": "05:45", "end": "17:00"},
                "friday": {"start": "05:45", "end": "15:45"},
                "saturday": None,  # выходной
                "sunday": None     # выходной
            }
        }

def get_holidays_from_config():
    """Получает праздничные дни из конфигурации"""
    config = load_worktime_config()
    raw = config["holidays"].strip()
    if not raw:
        return set()
    out = set()
    for token in raw.split(","):
        token = token.strip()
        if token:
            try:
                out.add(datetime.strptime(token, "%Y-%m-%d").date())
            except Exception:
                pass
    return out

def get_schedule_from_config():
    """Получает рабочий график из конфигурации"""
    config = load_worktime_config()
    schedule_config = config["schedule"]
    
    # Преобразуем имена дней в числовые индексы (0=Пн ... 6=Вс)
    day_names = {
        "monday": 0,
        "tuesday": 1,
        "wednesday": 2,
        "thursday": 3,
        "friday": 4,
        "saturday": 5,
        "sunday": 6
    }
    
    schedule = {}
    for day_name, day_config in schedule_config.items():
        day_idx = day_names[day_name]
        if day_config is None:
            schedule[day_idx] = None  # выходной день
        else:
            start_time = datetime.strptime(day_config["start"], "%H:%M").time()
            end_time = datetime.strptime(day_config["end"], "%H:%M").time()
            schedule[day_idx] = (start_time, end_time)
    
    return schedule

def _is_workday_from_config(d: date) -> bool:
    """Проверяет, рабочий ли день на основе конфигурации"""
    holidays = get_holidays_from_config()
    if d in holidays:
        return False
    
    schedule = get_schedule_from_config()
    wd = d.weekday()
    return schedule.get(wd) is not None

def _work_interval_for_config(d: date):
    """Получает рабочий интервал для конкретного дня"""
    schedule = get_schedule_from_config()
    wd = d.weekday()
    return schedule.get(wd)

def working_hours_between(start: datetime, end: datetime) -> float:
    """
    Рассчитывает рабочие часы между start и end по графику из конфигурации
    """
    if end <= start:
        return 0.0

    cur = start.replace(second=0, microsecond=0)
    end = end.replace(second=0, microsecond=0)
    
    total = 0.0
    day = date(cur.year, cur.month, cur.day)
    last_day = date(end.year, end.month, end.day)

    while day <= last_day:
        # Проверяем, рабочий ли это день
        if not _is_workday_from_config(day):
            day += timedelta(days=1)
            continue
        
        # Получаем рабочие часы для этого дня
        day_schedule = _work_interval_for_config(day)
        if day_schedule is None:
            day += timedelta(days=1)
            continue
        
        work_start, work_end = day_schedule
        
        # Определяем интервал для этого дня
        day_start = datetime.combine(day, work_start)
        day_end = datetime.combine(day, work_end)
        
        seg_start = max(day_start, start) if day == start.date() else day_start
        seg_end = min(day_end, end) if day == end.date() else day_end
        
        if seg_end > seg_start:
            total += (seg_end - seg_start).total_seconds() / 3600.0
            
        day += timedelta(days=1)

    return max(0.0, total)

def working_days_between(start: datetime, end: datetime, base_day_hours=8.0) -> float:
    """
    Возвращает рабочие дни между start и end через base_day_hours (по умолчанию 8 ч)
    """
    hours = working_hours_between(start, end)
    return hours / base_day_hours if base_day_hours > 0 else 0.0

def working_minutes_between(start: datetime, end: datetime) -> int:
    """
    Рассчитывает рабочие минуты между start и end по графику из конфигурации
    """
    if not start or not end:
        return 0
    if end <= start:
        return 0

    total_minutes = 0
    cur_day = start.date()
    last_day = end.date()

    while cur_day <= last_day:
        # Проверяем, рабочий ли это день
        if not _is_workday_from_config(cur_day):
            cur_day += timedelta(days=1)
            continue
        
        day_schedule = _work_interval_for_config(cur_day)
        if day_schedule is None:
            cur_day += timedelta(days=1)
            continue
        
        work_start, work_end = day_schedule
        day_start = datetime.combine(cur_day, work_start)
        day_end = datetime.combine(cur_day, work_end)
        
        # Пересечение с [start, end]
        seg_start = max(day_start, start) if cur_day == start.date() else day_start
        seg_end = min(day_end, end) if cur_day == end.date() else day_end
        
        if seg_end > seg_start:
            total_minutes += int((seg_end - seg_start).total_seconds() // 60)
            
        cur_day += timedelta(days=1)

    return max(total_minutes, 0)

def minutes_to_hours(minutes: int) -> float:
    """Конвертер минут в часы"""
    return round(minutes / 60.0, 4)