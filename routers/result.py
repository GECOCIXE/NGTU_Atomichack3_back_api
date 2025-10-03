# routers/result.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_document, get_user_by_login
from ..app import get_current_user

router = APIRouter()

@router.get("/result/{doc_id}")
def get_result(doc_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = get_document(db, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return {
        "id": doc.id,
        "filename": doc.filename,
        "upload_date": doc.upload_date,
        "percent": doc.analysis_percent,
        "description": doc.description
    }