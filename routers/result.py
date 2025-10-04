import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_document, get_user_by_login
from routers.dependencies import get_current_user
from scripts.parse_report import parse_report

router = APIRouter()

@router.get("/result/{doc_id}")
def get_result(doc_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = get_document(db, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if doc.ann_pdf_path is None or doc.description is None:
        return {"id": doc.id, "status": "processing"}
    
    report_path = doc.description
    if not os.path.exists(report_path):
        raise HTTPException(status_code=404, detail="Report file not found")
    
    with open(report_path, "r", encoding="utf-8") as f:
        report_content = f.read()
    
    parsed_data = parse_report(report_content)
    
    return {
        "id": doc.id,
        "filename": doc.filename,
        "upload_date": doc.upload_date,
        "error_points": parsed_data["error_points"],
        "error_counts": parsed_data["error_counts"],
        "total_violations": parsed_data["total_violations"],
        "full_report": parsed_data["full_report"]
    }