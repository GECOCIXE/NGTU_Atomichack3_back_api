from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session
from typing import List, Dict, Any
import io
import csv

from scripts.db import get_db
from scripts.crud import (
    get_user_by_login,
    get_user_by_id,
    get_documents_for_user,
    list_versions_for_document,
    list_decisions_for_version,
    list_all_documents,
)
from routers.dependencies import get_current_user
from utils.worktime import working_days_between, working_minutes_between
from datetime import datetime, timezone

router = APIRouter()

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

def _aware_to_naive_utc(dt_like):
    dt = _coerce_to_aware_utc(dt_like)
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None)

def _parse_range(start_date, end_date):
    start_dt = _coerce_to_aware_utc(start_date) if start_date else None
    end_dt = _coerce_to_aware_utc(end_date) if end_date else None
    return start_dt, end_dt

def _iso(dt_like):
    dt = _coerce_to_aware_utc(dt_like)
    return dt.isoformat() if dt else ""

@router.get("/export-process-analysis-csv")
def export_process_analysis_csv(
    start_date: str = None,
    end_date: str = None,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    start_dt, end_dt = _parse_range(start_date, end_date)
    role = getattr(user, "role", "developer")

    # Документы
    if role in ("admin", "norm_controller"):
        docs = list_all_documents(db)
    else:
        docs = get_documents_for_user(db, user.id)

    # Подготовим данные для CSV
    csv_data = []
    
    for doc in docs:
        versions = list_versions_for_document(db, doc.id)
        if not versions:
            continue

        vlist = list(reversed(versions))  # от старой к новой

        for i in range(1, len(vlist)):
            prev_v = vlist[i - 1]   # где обнаружили
            curr_v = vlist[i]       # куда загрузили исправления

            detect_at = getattr(prev_v, "analysis_completed_at", None)
            review_at = getattr(curr_v, "analysis_completed_at", None)

            dev_decisions = [
                d for d in list_decisions_for_version(db, curr_v.id)
                if getattr(d, "author_role", "") == "developer" and getattr(d, "status", "") == "fixed"
            ]

            # Для каждой фиксации считаем дни + минуты (часы из минут)
            for dfix in dev_decisions:
                fix_at = getattr(dfix, "timestamp", None)

                # Наивный UTC для расчётов в календаре
                detect_naive = _aware_to_naive_utc(detect_at)
                fix_naive = _aware_to_naive_utc(fix_at)
                review_naive = _aware_to_naive_utc(review_at)

                # ДНИ
                fix_days = working_days_between(detect_naive, fix_naive) if (detect_naive and fix_naive) else 0.0
                rev_days = working_days_between(fix_naive, review_naive) if (fix_naive and review_naive) else 0.0

                # МИНУТЫ
                fix_mins = working_minutes_between(detect_naive, fix_naive) if (detect_naive and fix_naive) else 0
                rev_mins = working_minutes_between(fix_naive, review_naive) if (fix_naive and review_naive) else 0

                # Получаем информацию о пользователе-владельце документа
                from scripts.models import User
                doc_user = db.query(User).filter(User.id == doc.user_id).first()

                # Добавляем строку в CSV
                csv_row = {
                    "doc_id": doc.id,
                    "filename": doc.filename,
                    "developer_login": doc_user.login if doc_user else "Unknown",
                    "developer_full_name": doc_user.full_name if doc_user and doc_user.full_name else doc_user.login if doc_user else "Unknown",
                    "developer_id": doc.user_id,
                    "upload_date": (doc.upload_date.isoformat() if getattr(doc, "upload_date", None) else ""),
                    "detect_at": _iso(detect_at),
                    "fixed_at": _iso(fix_at),
                    "review_at": _iso(review_at),
                    "fix_duration_days": round(fix_days, 4),
                    "review_duration_days": round(rev_days, 4),
                    "fix_duration_minutes": fix_mins,
                    "review_duration_minutes": rev_mins,
                    "fix_duration_hours": round(fix_mins / 60.0, 4),
                    "review_duration_hours": round(rev_mins / 60.0, 4),
                    "error_point": getattr(dfix, "error_point", "") or "",
                    "status": getattr(dfix, "status", ""),
                    "author": getattr(dfix, "author", ""),
                    "author_role": getattr(dfix, "author_role", ""),
                }
                
                # Найти системные решения (отказы) для этого же occ_id или error_point
                sys_rej = [
                    d for d in list_decisions_for_version(db, curr_v.id)
                    if getattr(d, "author_role", "") == "norm_controller" and getattr(d, "status", "") == "rejected"
                ]
                
                # Проверим, есть ли отклонение для этой же ошибки
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
                csv_row["outcome"] = outcome
                
                csv_data.append(csv_row)

    # Создаем CSV
    if not csv_data:
        # Если данных нет, создаем файл с заголовками
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "doc_id", "filename", "developer_login", "developer_full_name", "developer_id",
            "upload_date", "detect_at", "fixed_at", "review_at",
            "fix_duration_days", "review_duration_days", "fix_duration_minutes", "review_duration_minutes",
            "fix_duration_hours", "review_duration_hours", "error_point", "status", "author", "author_role", "outcome"
        ])
        csv_content = output.getvalue()
        output.close()
    else:
        # Создаем CSV с данными
        output = io.StringIO()
        fieldnames = [
            "doc_id", "filename", "developer_login", "developer_full_name", "developer_id",
            "upload_date", "detect_at", "fixed_at", "review_at",
            "fix_duration_days", "review_duration_days", "fix_duration_minutes", "review_duration_minutes",
            "fix_duration_hours", "review_duration_hours", "error_point", "status", "author", "author_role", "outcome"
        ]
        
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        
        for row in csv_data:
            writer.writerow(row)
        
        csv_content = output.getvalue()
        output.close()

    # Возвращаем CSV файл для скачивания
    response = Response(content=csv_content)
    response.headers["Content-Disposition"] = f"attachment; filename=process_analysis_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    response.headers["Content-Type"] = "text/csv; charset=utf-8"
    
    return response