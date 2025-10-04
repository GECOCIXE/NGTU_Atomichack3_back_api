from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey
from .db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True)
    hashed_password = Column(String)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    filename = Column(String)
    upload_date = Column(DateTime)
    ann_pdf_path = Column(String, nullable=True)
    description = Column(String, nullable=True)