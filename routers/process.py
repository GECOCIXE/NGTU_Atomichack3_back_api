from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_documents_for_user, get_user_by_login
from routers.dependencies import get_current_user
from datetime import datetime
import os
from statistics import mean

router = APIRouter()

@router.get("/api/process-analysis")
def process_analysis(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    docs = get_documents_for_user(db, user.id)

    items = []
    fix_durations = []
    review_durations = []
    iterations_list = []

    for doc in docs:
        upload_dt = doc.upload_date or datetime.now()
        # Heuristics:
        # - If annotated PDF exists -> consider "review" done; review_duration based on file mtime - upload
        # - Use 1 iteration by default; if multiple annotated PDFs per doc in future, could increase
        ann_present = bool(doc.ann_pdf_path and os.path.exists(doc.ann_pdf_path))
        review_duration = 0.0
        if ann_present:
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(doc.ann_pdf_path))
                review_duration = max((mtime - upload_dt).total_seconds() / 86400.0, 0.0)
            except Exception:
                review_duration = 0.5
        # Fix duration: if status approved -> shorter, if rejected -> longer; otherwise heuristic
        status = getattr(doc, "status", "processing") or "processing"
        fix_duration = 0.5 if status == "approved" else (3.0 if status == "rejected" else 1.5)
        iterations = 1 if status in ("processing", "approved") else 2

        items.append({
            "doc_id": str(doc.id),
            "filename": doc.filename,
            "upload_date": upload_dt.isoformat(),
            "fix_duration": round(fix_duration, 2),
            "review_duration": round(review_duration, 2),
            "iterations": iterations
        })
        fix_durations.append(fix_duration)
        review_durations.append(review_duration)
        iterations_list.append(iterations)

    def safe_stats(values):
        if not values:
            return (0.0, 0.0, 0.0)
        return (mean(values), max(values), min(values))

    avg_fix, max_fix, min_fix = safe_stats(fix_durations)
    avg_rev, max_rev, min_rev = safe_stats(review_durations)

    return {
        "average_fix_duration": round(avg_fix, 2),
        "average_review_duration": round(avg_rev, 2),
        "max_fix_duration": round(max_fix, 2),
        "min_fix_duration": round(min_fix, 2),
        "max_review_duration": round(max_rev, 2),
        "min_review_duration": round(min_rev, 2),
        "average_iterations": round(mean(iterations_list), 2) if iterations_list else 0,
        "max_iterations": max(iterations_list) if iterations_list else 0,
        "min_iterations": min(iterations_list) if iterations_list else 0,
        "total_documents": len(items),
        "documents": items
    }