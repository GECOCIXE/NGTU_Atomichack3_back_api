import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from scripts.db import get_db
from scripts.crud import (
    get_documents_for_user,
    get_user_by_login,
    list_versions_for_document,
)
from scripts.parse_report import parse_report
from routers.dependencies import get_current_user

router = APIRouter()

@router.get("/history")
def get_history(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Для нормоконтроллера возвращаем историю всех разработчиков
    if user.role == "norm_controller":
        # Получаем все документы всех пользователей-разработчиков
        from scripts.models import Document, User
        docs = db.query(Document).join(User).filter(User.role == "developer").all()
    else:
        # Для обычного пользователя возвращаем только его документы
        docs = get_documents_for_user(db, user.id)
    history = []

    for doc in docs:
        versions = list_versions_for_document(db, doc.id)

        latest = versions[0] if versions else None
        first_v = versions[-1]
        processing_status = "processing"
        file_status = ""   # по умолчанию пустой статус до появления отчёта
        
        # ---- ОТЧЁТ ПО ПЕРВОЙ ВЕРСИИ (замороженные error_points/error_counts) ----
        first_report_content = ""
        if getattr(first_v, "report_path", None) and os.path.exists(first_v.report_path):
            with open(first_v.report_path, "r", encoding="utf-8") as f:
                first_report_content = f.read()
        parsed_first = parse_report(first_report_content, doc_id=doc.id)
        frozen_error_points = parsed_first.get("error_points", []) or []
        frozen_error_counts = parsed_first.get("error_counts", {}) or {}
        frozen_total = int(parsed_first.get("total_violations", 0) or 0)

        if latest:
            # обработка / парсинг отчёта последней версии
            if getattr(latest, "report_path", None) and os.path.exists(latest.report_path):
                processing_status = "complete"
                with open(latest.report_path, "r", encoding="utf-8") as f:
                    report_content = f.read()
                parsed = parse_report(report_content, doc_id=doc.id)
                total_violations = int(parsed.get("total_violations", 0) or 0)
                

            # статус файла (approved/rejected/removed) — как в /result
            allowed = {"approved", "rejected", "removed"}
            if getattr(latest, "verdict_status", None) in allowed:
                file_status = latest.verdict_status
            else:
                if processing_status == "complete":
                    file_status = "approved" if total_violations == 0 else "rejected"
                else:
                    file_status = ""  # пустой статус, если анализ не завершен
            status_author = getattr(latest, "verdict_author_name", None) or "Цифровой помощник конструктора"

        # краткая сводка по всем версиям
        versions_summary = []
        for v in versions:
            v_processing = "complete" if (getattr(v, "report_path", None) and os.path.exists(v.report_path)) else "processing"
            versions_summary.append({
                "version_id": v.id,
                "version_number": getattr(v, "version_number", None),
                "upload_date": v.upload_date.isoformat() if v.upload_date else "",
                "status": getattr(v, "verdict_status", "processing"),
                "processing_status": v_processing,
            })

        # Получаем информацию о пользователе, который загрузил документ
        from scripts.models import User
        doc_user = db.query(User).filter(User.id == doc.user_id).first()
        user_full_name = doc_user.full_name or doc_user.login if doc_user else "Unknown"

        item = {
            "id": doc.id,
            "filename": doc.filename,
            "upload_date": doc.upload_date.isoformat() if doc.upload_date else "",
            "status": file_status,                 # ТОЛЬКО статус файла
            "status_author": status_author,
            "processing_status": processing_status, # тех.статус анализа
            "total_violations": frozen_total,   # по последней версии
            "error_points": frozen_error_points,           # по последней версии (уже отсортированы парсером)
            "error_counts": frozen_error_counts,           # по последней версии
            "versions": versions_summary,
        }

        # Если пользователь - norm_controller, добавляем информацию о разработчике
        if user.role == "norm_controller":
            item["developer_login"] = doc_user.login if doc_user else "Unknown"
            item["developer_full_name"] = user_full_name
            item["developer_id"] = doc.user_id

        history.append(item)

    return history
