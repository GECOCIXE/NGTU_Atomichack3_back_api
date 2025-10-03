# routers/history.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_documents_for_user, get_user_by_login
from ..app import get_current_user

router = APIRouter()

@router.get("/history")
def get_history(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    docs = get_documents_for_user(db, user.id)
    return [
        {
            "filename": d.filename,
            "upload_date": d.upload_date,
            "percent": d.analysis_percent
        } for d in docs
    ]