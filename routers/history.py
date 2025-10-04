import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_documents_for_user, get_user_by_login
from routers.dependencies import get_current_user
from scripts.parse_report import parse_report

router = APIRouter()

@router.get("/history")
def get_history(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    docs = get_documents_for_user(db, user.id)
    
    history = []
    for d in docs:
        item = {
            "id": user.id,
            "doc_id": d.id,
            "filename": d.filename,
            "upload_date": d.upload_date,
        }
        
        if d.description:
            report_path = d.description
            if os.path.exists(report_path):
                with open(report_path, "r", encoding="utf-8") as f:
                    report_content = f.read()
                parsed_data = parse_report(report_content)
                item["error_points"] = parsed_data["error_points"]
                item["error_counts"] = parsed_data["error_counts"]
                item["total_violations"] = parsed_data["total_violations"]
            else:
                item["status"] = "report missing"
        else:
            item["status"] = "processing"
        
        history.append(item)
    
    return history