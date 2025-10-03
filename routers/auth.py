# routers/auth.py
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from scripts.crud import authenticate_user, create_access_token
from scripts.db import get_db
from datetime import timedelta

router = APIRouter()

class Login(BaseModel):
    login: str
    password: str

@router.post("/login")
def login_for_access_token(form_data: Login, db: Session = Depends(get_db)):
    user = authenticate_user(db, form_data.login, form_data.password)
    if not user:
        raise HTTPException(status_code=401, detail="Incorrect login or password")
    access_token_expires = timedelta(minutes=30)
    access_token = create_access_token(data={"sub": user.login}, expires_delta=access_token_expires)
    return {"access_token": access_token, "token_type": "bearer"}