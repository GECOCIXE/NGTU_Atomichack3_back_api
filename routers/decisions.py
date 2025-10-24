from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from datetime import datetime
from scripts.db import get_db
from scripts.crud import get_user_by_login, get_version, add_decision, set_verdict
from routers.dependencies import get_current_user, RoleGuard

router = APIRouter()

class DecisionIn(BaseModel):
    version_id: int
    error_point: str
    status: str          # 'fixed' | 'rejected'
    comment: str = ""

@router.post("/api/decisions/add", dependencies=[Depends(RoleGuard("norm_controller","admin"))])
def add_point_decision(body: DecisionIn, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not get_version(db, body.version_id):
        raise HTTPException(status_code=404, detail="Version not found")
    author_name = user.full_name or user.login
    dec = add_decision(
        db,
        version_id=body.version_id,
        error_point=body.error_point,
        status=body.status,
        author=author_name,                   # <-- ФИО или login
        author_role=getattr(user, "role", "norm_controller"),
        comment=body.comment,
        timestamp=datetime.utcnow()
    )
    return {"id": dec.id}

class VerdictIn(BaseModel):
    version_id: int
    status: str           # 'approved' | 'rejected' | 'removed' | 'processing'
    comment: str = ""

@router.post("/api/verdict", dependencies=[Depends(RoleGuard("norm_controller","admin"))])
def set_version_verdict(body: VerdictIn, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.status not in ("approved","rejected","removed","processing"):
        raise HTTPException(status_code=400, detail="Invalid status")
    author_name = user.full_name or user.login   # <-- ФИО админа/нормоконтроллера
    ver = set_verdict(db, body.version_id, body.status, body.comment,
                      author_name=author_name, author_role=getattr(user, "role", None))
    if not ver:
        raise HTTPException(status_code=404, detail="Version not found")
    return {"version_id": ver.id, "document_id": ver.document_id, "status": ver.verdict_status}
