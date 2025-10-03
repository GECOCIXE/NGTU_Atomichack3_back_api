# routers/upload.py
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import create_document, get_user_by_login
from scripts.analysis import analyze_pdf
from datetime import datetime
import os
import shutil
from ..app import get_current_user  # Import from app.py

router = APIRouter()

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    upload_date = datetime.now()
    doc = create_document(db, user.id, file.filename, upload_date)
    
    os.makedirs("data/original", exist_ok=True)
    file_path = f"data/original/{doc.id}.pdf"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    # Run analysis in background
    background_tasks.add_task(analyze_pdf, doc.id)
    
    return {"id": doc.id, "filename": file.filename, "upload_date": upload_date}