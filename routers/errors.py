# routers/errors.py
import os
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_user_by_login, get_document, list_versions_for_document
from routers.dependencies import get_current_user
from scripts.parse_report import parse_report

router = APIRouter()

@router.get("/api/error-occurrences/{doc_id}")
def list_error_occurrences(
    doc_id: int,
    version_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    doc = get_document(db, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")

    versions = list_versions_for_document(db, doc.id)
    if not versions:
        return {"occurrences": []}

    latest = versions[0] if version_id is None else next((v for v in versions if v.id == version_id), None)
    if not latest:
        raise HTTPException(status_code=404, detail="Version not found")

    report_path = latest.report_path or doc.description
    if not report_path or not os.path.exists(report_path):
        return {"occurrences": []}

    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()

    parsed = parse_report(report_content, doc_id=doc.id)
    # вернём только ID/point/description для таргетинга
    return {"occurrences": parsed.get("occurrences", [])}
