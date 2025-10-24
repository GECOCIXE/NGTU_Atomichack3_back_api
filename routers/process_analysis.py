# routers/process_analysis.py
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from datetime import datetime, timezone
from typing import Optional, List, Dict

from scripts.db import get_db
from scripts.models import User
from scripts.crud import (
    get_user_by_login,
    get_user_by_id,
    get_documents_for_user,
    list_versions_for_document,
    list_decisions_for_version,
    list_all_documents,
)
from routers.dependencies import get_current_user
from utils.worktime_configurable import working_days_between, working_minutes_between, _is_workday_from_config

router = APIRouter()

# ---------------------- TIME HELPERS ----------------------
def _coerce_to_aware_utc(ts):
    if ts is None:
        return None
    if isinstance(ts, str):
        s = ts.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except Exception:
            return None
    elif isinstance(ts, datetime):
        dt = ts
    else:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

def _parse_range(start_date: Optional[str], end_date: Optional[str]):
    start_dt = _coerce_to_aware_utc(start_date) if start_date else None
    end_dt   = _coerce_to_aware_utc(end_date) if end_date else None
    return start_dt, end_dt

def _aware_to_naive_utc(dt_like):
    dt = _coerce_to_aware_utc(dt_like)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

# ---------------------- ENDPOINT ----------------------
@router.get("/process-analysis")
def process_analysis(
    start_date: Optional[str] = Query(default=None, description="ISO datetime, inclusive"),
    end_date: Optional[str] = Query(default=None, description="ISO datetime, inclusive"),
    include_sessions: bool = Query(default=False, description="Возвращать детальные сессии по каждому документу"),
    group_by: Optional[str] = Query(default=None, description="Поддерживается 'developer' для сводки по разработчикам"),
    developer_id: Optional[int] = Query(default=None, description="Фильтр по конкретному разработчику (для admin/norm_controller)"),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user),
):
    """
    Агрегаты по длительностям:
      - в рабочих днях (как раньше),
      - в рабочих часах и минутах (новые поля).
    """
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    start_dt, end_dt = _parse_range(start_date, end_date)
    role = getattr(user, "role", "developer")

    # Документы
    if role in ("admin", "norm_controller"):
        docs = list_all_documents(db)
        if developer_id is not None:
            docs = [d for d in docs if getattr(d, "user_id", None) == developer_id]
    else:
        docs = get_documents_for_user(db, user.id)

    # Агрегаторы для всего набора
    per_docs: List[Dict] = []
    all_fix_days: List[float] = []
    all_rev_days: List[float] = []
    all_fix_mins: List[int] = []
    all_rev_mins: List[int] = []
    all_iters: List[int] = []

    # Для группировки по разработчикам
    dev_agg: Dict[int, Dict] = {}

    def _in_range(ts_like) -> bool:
        if not (start_dt or end_dt):
            return True
        ts = _coerce_to_aware_utc(ts_like)
        if ts is None:
            return False
        if start_dt and ts < start_dt:
            return False
        if end_dt and ts > end_dt:
            return False
        return True

    for doc in docs:
        # Пропускаем документы, загруженные в нерабочее время (выходные/праздники)
        if doc.upload_date and not _is_workday_from_config(doc.upload_date.date()):
            continue
            
        versions = list_versions_for_document(db, doc.id)
        if not versions:
            continue

        vlist = list(reversed(versions))  # от старой к новой

        sessions: List[Dict] = []

        # По документу
        fix_days_list: List[float] = []
        rev_days_list: List[float] = []
        fix_mins_list: List[int] = []
        rev_mins_list: List[int] = []
        iterations = 0

        for i in range(1, len(vlist)):
            prev_v = vlist[i - 1]   # где обнаружили
            curr_v = vlist[i]       # куда загрузили исправления

            detect_at = getattr(prev_v, "analysis_completed_at", None)
            review_at = getattr(curr_v, "analysis_completed_at", None)

            dev_decisions = [
                d for d in list_decisions_for_version(db, curr_v.id)
                if getattr(d, "author_role", "") == "developer" and getattr(d, "status", "") == "fixed"
            ]
            sys_rej = [
                d for d in list_decisions_for_version(db, curr_v.id)
                if getattr(d, "author_role", "") == "norm_controller" and getattr(d, "status", "") == "rejected"
            ]

            # фильтр по диапазону: любые изменения в сессии
            if start_dt or end_dt:
                any_change = (
                    _in_range(detect_at)
                    or any(_in_range(getattr(d, "timestamp", None)) for d in dev_decisions + sys_rej)
                    or _in_range(review_at)
                )
                if not any_change:
                    continue

            if sys_rej:
                iterations += 1

            # Для каждой фиксации считаем дни + минуты (часы из минут)
            for dfix in dev_decisions:
                fix_at = getattr(dfix, "timestamp", None)

                # Наивный UTC для расчётов в календаре
                detect_naive = _aware_to_naive_utc(detect_at)
                fix_naive    = _aware_to_naive_utc(fix_at)
                review_naive = _aware_to_naive_utc(review_at)

                # ДНИ (как раньше)
                fix_days = working_days_between(detect_naive, fix_naive) if (detect_naive and fix_naive) else 0.0
                rev_days = working_days_between(fix_naive, review_naive) if (fix_naive and review_naive) else 0.0

                # МИНУТЫ (новое)
                fix_mins = working_minutes_between(detect_naive, fix_naive) if (detect_naive and fix_naive) else 0
                rev_mins = working_minutes_between(fix_naive, review_naive) if (fix_naive and review_naive) else 0

                if fix_days >= 0:
                    fix_days_list.append(fix_days)
                if rev_days >= 0:
                    rev_days_list.append(rev_days)
                if fix_mins >= 0:
                    fix_mins_list.append(fix_mins)
                if rev_mins >= 0:
                    rev_mins_list.append(rev_mins)

                if include_sessions:
                    # outcome: есть ли отказ по тому же occ_id/пункту в этой версии
                    occ = None
                    if getattr(dfix, "comment", None):
                        import re
                        m = re.search(r"\[occ:([0-9a-fA-F]{6,64})\]", dfix.comment)
                        if m:
                            occ = m.group(1)

                    def _matches_sys(sr) -> bool:
                        if occ:
                            import re
                            mm = re.search(r"\[occ:([0-9a-fA-F]{6,64})\]", sr.comment or "")
                            if mm and mm.group(1) == occ:
                                return True
                        if getattr(sr, "error_point", "") and getattr(dfix, "error_point", ""):
                            return sr.error_point == dfix.error_point
                        return False

                    rejected_here = any(_matches_sys(sr) for sr in sys_rej)
                    outcome = "rejected" if rejected_here else "accepted"

                    def _iso(dt_like):
                        dta = _coerce_to_aware_utc(dt_like)
                        return dta.isoformat() if dta else ""

                    sessions.append({
                        "prev_version_id": prev_v.id,
                        "curr_version_id": curr_v.id,
                        "detect_at": _iso(detect_at),
                        "fixed_at": _iso(fix_at),
                        "review_at": _iso(review_at),
                        # дни (как раньше)
                        "fix_duration": round(fix_days, 4),
                        "review_duration": round(rev_days, 4),
                        # новые поля
                        "fix_duration_minutes": fix_mins,
                        "review_duration_minutes": rev_mins,
                        "fix_duration_hours": round(fix_mins / 60.0, 4),
                        "review_duration_hours": round(rev_mins / 60.0, 4),
                        "error_point": getattr(dfix, "error_point", "") or "",
                        "occ_id": occ,
                        "outcome": outcome,
                    })

        # --- Агрегаты по документу ---
        def _avg(xs: List[float]) -> float:
            return round(sum(xs) / len(xs), 4) if xs else 0.0
        def _avg_i(xs: List[int]) -> float:
            return round(sum(xs) / len(xs), 4) if xs else 0.0

        doc_fix_avg_days = _avg(fix_days_list)
        doc_rev_avg_days = _avg(rev_days_list)
        doc_fix_avg_mins = _avg_i(fix_mins_list)
        doc_rev_avg_mins = _avg_i(rev_mins_list)

        # Получаем информацию о пользователе-владельце документа
        doc_user = db.query(User).filter(User.id == doc.user_id).first()
        
        doc_entry = {
            "doc_id": str(doc.id),
            "filename": doc.filename,
            "developer_login": doc_user.login if doc_user else "Unknown",
            "developer_full_name": doc_user.full_name if doc_user and doc_user.full_name else doc_user.login if doc_user else "Unknown",
            "developer_id": doc.user_id,
            "upload_date": (doc.upload_date.isoformat() if getattr(doc, "upload_date", None) else ""),
            # как раньше (дни):
            "fix_duration": doc_fix_avg_days,
            "review_duration": doc_rev_avg_days,
            # новые поля:
            "fix_duration_minutes": int(round(doc_fix_avg_mins)) if doc_fix_avg_mins else 0,
            "review_duration_minutes": int(round(doc_rev_avg_mins)) if doc_rev_avg_mins else 0,
            "fix_duration_hours": round(doc_fix_avg_mins / 60.0, 4) if doc_fix_avg_mins else 0.0,
            "review_duration_hours": round(doc_rev_avg_mins / 60.0, 4) if doc_rev_avg_mins else 0.0,
            "iterations": len([x for x in range(len(fix_days_list))]) if False else 0  # оставим ниже корректное значение
        }
        # корректный iterations считаем выше: по наличию rejected в версии
        doc_entry["iterations"] = iterations

        if include_sessions:
            doc_entry["sessions"] = sessions

        per_docs.append(doc_entry)

        all_fix_days.extend(fix_days_list)
        all_rev_days.extend(rev_days_list)
        all_fix_mins.extend(fix_mins_list)
        all_rev_mins.extend(rev_mins_list)
        all_iters.append(iterations)

        # --- Группировка по разработчикам ---
        owner_id = getattr(doc, "user_id", None)
        if owner_id is not None:
            agg = dev_agg.setdefault(owner_id, {
                "developer_id": owner_id,
                "login": None,
                "full_name": None,
                "fix_days": [],
                "rev_days": [],
                "fix_mins": [],
                "rev_mins": [],
                "iters": [],
                "documents": 0,
            })
            agg["documents"] += 1
            agg["fix_days"].extend(fix_days_list)
            agg["rev_days"].extend(rev_days_list)
            agg["fix_mins"].extend(fix_mins_list)
            agg["rev_mins"].extend(rev_mins_list)
            agg["iters"].append(iterations)

    # --- Итоги по всем документам ---
    def _avg_f(xs: List[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0
    def _avg_i(xs: List[int]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0
    def _min_f(xs: List[float]) -> float:
        return min(xs) if xs else 0.0
    def _max_f(xs: List[float]) -> float:
        return max(xs) if xs else 0.0
    def _min_i(xs: List[int]) -> int:
        return min(xs) if xs else 0
    def _max_i(xs: List[int]) -> int:
        return max(xs) if xs else 0

    avg_fix_mins = _avg_i(all_fix_mins)
    avg_rev_mins = _avg_i(all_rev_mins)
    
    # Собираем данные для графика итераций по времени (для фронтенда - эффективность разработчика)
    iterations_timeline = []
    for doc in docs:
        # Пропускаем документы, загруженные в нерабочее время (выходные/праздники)
        if doc.upload_date and not _is_workday_from_config(doc.upload_date.date()):
            continue
            
        versions = list_versions_for_document(db, doc.id)
        if not versions:
            continue

        vlist = list(reversed(versions))  # от старой к новой
        
        for i in range(1, len(vlist)):
            curr_v = vlist[i]       # текущая версия

            # Проверяем решения в текущей версии
            sys_rej = [
                d for d in list_decisions_for_version(db, curr_v.id)
                if getattr(d, "author_role", "") == "norm_controller" and getattr(d, "status", "") == "rejected"
            ]
            
            if sys_rej:
                # Добавляем точку для графика: время и количество итераций
                review_at = getattr(curr_v, "analysis_completed_at", None)
                if review_at:
                    iterations_timeline.append({
                        "timestamp": _iso(review_at),
                        "iterations_count": len(sys_rej)
                    })

    resp = {
        # как раньше — в ДНЯХ:
        "average_fix_duration": _avg_f(all_fix_days),
        "average_review_duration": _avg_f(all_rev_days),
        "max_fix_duration": _max_f(all_fix_days),
        "min_fix_duration": _min_f(all_fix_days),
        "max_review_duration": _max_f(all_rev_days),
        "min_review_duration": _min_f(all_rev_days),

        # НОВОЕ — в ЧАСАХ/МИНУТАХ (на основе рабочих минут):
        "average_fix_duration_minutes": int(round(avg_fix_mins)) if avg_fix_mins else 0,
        "average_review_duration_minutes": int(round(avg_rev_mins)) if avg_rev_mins else 0,
        "average_fix_duration_hours": round(avg_fix_mins / 60.0, 4) if avg_fix_mins else 0.0,
        "average_review_duration_hours": round(avg_rev_mins / 60.0, 4) if avg_rev_mins else 0.0,

        "max_fix_duration_minutes": _max_i(all_fix_mins),
        "min_fix_duration_minutes": _min_i(all_fix_mins),
        "max_review_duration_minutes": _max_i(all_rev_mins),
        "min_review_duration_minutes": _min_i(all_rev_mins),

        "max_fix_duration_hours": round((_max_i(all_fix_mins) / 60.0), 4) if all_fix_mins else 0.0,
        "min_fix_duration_hours": round((_min_i(all_fix_mins) / 60.0), 4) if all_fix_mins else 0.0,
        "max_review_duration_hours": round((_max_i(all_rev_mins) / 60.0), 4) if all_rev_mins else 0.0,
        "min_review_duration_hours": round((_min_i(all_rev_mins) / 60.0), 4) if all_rev_mins else 0.0,

        "average_iterations": _avg_f(all_iters),
        "max_iterations": _max_f(all_iters),
        "min_iterations": _min_f(all_iters),

        # Данные для графика итераций по времени
        "iterations_timeline": iterations_timeline,

        "total_documents": len(per_docs),
        "documents": per_docs,
    }

    # Сводка по разработчикам (по запросу)
    if group_by == "developer" and dev_agg:
        by_dev = []
        for dev_id, a in dev_agg.items():
            u = get_user_by_id(db, dev_id)
            avg_fix_m = _avg_i(a["fix_mins"])
            avg_rev_m = _avg_i(a["rev_mins"])
            
            # Собираем timeline для конкретного разработчика
            dev_iterations_timeline = []
            dev_docs_for_timeline = [d for d in docs if d.user_id == dev_id]
            for doc in dev_docs_for_timeline:
                # Пропускаем документы, загруженные в нерабочее время (выходные/праздники)
                if doc.upload_date and not _is_workday_from_config(doc.upload_date.date()):
                    continue
                    
                versions = list_versions_for_document(db, doc.id)
                if not versions:
                    continue

                vlist = list(reversed(versions))  # от старой к новой
                
                for i in range(1, len(vlist)):
                    curr_v = vlist[i]       # текущая версия

                    # Проверяем решения в текущей версии
                    sys_rej = [
                        d for d in list_decisions_for_version(db, curr_v.id)
                        if getattr(d, "author_role", "") == "norm_controller" and getattr(d, "status", "") == "rejected"
                    ]
                    
                    if sys_rej:
                        # Добавляем точку для графика: время и количество итераций
                        review_at = getattr(curr_v, "analysis_completed_at", None)
                        if review_at:
                            dev_iterations_timeline.append({
                                "timestamp": _iso(review_at),
                                "iterations_count": len(sys_rej)
                            })
            
            by_dev.append({
                "developer_id": dev_id,
                "login": getattr(u, "login", None),
                "full_name": getattr(u, "full_name", None) or getattr(u, "login", None),

                # дни
                "average_fix_duration": _avg_f(a["fix_days"]),
                "average_review_duration": _avg_f(a["rev_days"]),
                "max_fix_duration": _max_f(a["fix_days"]),
                "min_fix_duration": _min_f(a["fix_days"]),
                "max_review_duration": _max_f(a["rev_days"]),
                "min_review_duration": _min_f(a["rev_days"]),

                # минуты/часы
                "average_fix_duration_minutes": int(round(avg_fix_m)) if avg_fix_m else 0,
                "average_review_duration_minutes": int(round(avg_rev_m)) if avg_rev_m else 0,
                "average_fix_duration_hours": round(avg_fix_m / 60.0, 4) if avg_fix_m else 0.0,
                "average_review_duration_hours": round(avg_rev_m / 60.0, 4) if avg_rev_m else 0.0,

                "max_fix_duration_minutes": _max_i(a["fix_mins"]),
                "min_fix_duration_minutes": _min_i(a["fix_mins"]),
                "max_review_duration_minutes": _max_i(a["rev_mins"]),
                "min_review_duration_minutes": _min_i(a["rev_mins"]),

                "max_fix_duration_hours": round((_max_i(a["fix_mins"]) / 60.0), 4) if a["fix_mins"] else 0.0,
                "min_fix_duration_hours": round((_min_i(a["fix_mins"]) / 60.0), 4) if a["fix_mins"] else 0.0,
                "max_review_duration_hours": round((_max_i(a["rev_mins"]) / 60.0), 4) if a["rev_mins"] else 0.0,
                "min_review_duration_hours": round((_min_i(a["rev_mins"]) / 60.0), 4) if a["rev_mins"] else 0.0,

                "average_iterations": _avg_f(a["iters"]),
                "max_iterations": _max_f(a["iters"]),
                "min_iterations": _min_f(a["iters"]),
                "total_documents": a["documents"],
                
                # Данные для графика итераций по времени для конкретного разработчика
                "iterations_timeline": dev_iterations_timeline,
            })
        resp["by_developer"] = by_dev

    return resp