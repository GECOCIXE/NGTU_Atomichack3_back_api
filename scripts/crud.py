# scripts/crud.py
import os
from dotenv import load_dotenv
from jose import jwt, JWTError
from datetime import datetime, timedelta
from sqlalchemy.orm import Session
from .models import User, Document
import hashlib  # Simple hashing; consider bcrypt for production

load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "your_secret_key_here")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 30

def get_password_hash(password: str):
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str):
    return get_password_hash(plain_password) == hashed_password

def authenticate_user(db: Session, login: str, password: str):
    user = db.query(User).filter(User.login == login).first()
    if not user or not verify_password(password, user.hashed_password):
        return False
    return user

def create_access_token(data: dict, expires_delta: timedelta = None):
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def create_user(db: Session, login: str, password: str):
    hashed_password = get_password_hash(password)
    user = User(login=login, hashed_password=hashed_password)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def get_user_by_login(db: Session, login: str):
    return db.query(User).filter(User.login == login).first()

def create_document(db: Session, user_id: int, filename: str, upload_date: datetime):
    doc = Document(user_id=user_id, filename=filename, upload_date=upload_date)
    db.add(doc)
    db.commit()
    db.refresh(doc)
    return doc

def update_document_analysis(db: Session, doc_id: int, percent: float, description: str):
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if doc:
        doc.analysis_percent = percent
        doc.description = description
        db.commit()
        db.refresh(doc)
    return doc

def get_documents_for_user(db: Session, user_id: int):
    return db.query(Document).filter(Document.user_id == user_id).all()

def get_document(db: Session, doc_id: int):
    return db.query(Document).filter(Document.id == doc_id).first()