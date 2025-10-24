from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import create_user, authenticate_user, create_access_token

router = APIRouter()

class Login(BaseModel):
    login: str
    password: str
    full_name: str | None = None  # НОВОЕ: ФИО (обязательно для developer/admin, для norm_controller можно не передавать)

@router.post("/reg")
def register(body: Login, db: Session = Depends(get_db)):
    # Роль всё равно не принимаем от клиента — всегда 'developer'
    user = create_user(db, body.login, body.password, full_name=body.full_name, role="developer")
    if not user:
        raise HTTPException(status_code=400, detail="Registration failed")
    return {"ok": True}

@router.post("/login")
def login(body: Login, db: Session = Depends(get_db)):
    user = authenticate_user(db, body.login, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid login or password")
    token = create_access_token({"sub": user.login})
    return {
        "access_token": token,
        "token_type": "bearer",
        "full_name": user.full_name or user.login,  # ФИО; для тех у кого не задано (например, нормоконтроллер) вернём логин
        "role": user.role  # Отправляем роль пользователя
    }
