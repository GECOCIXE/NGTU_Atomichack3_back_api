from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Text
from sqlalchemy.orm import relationship
from .db import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    login = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="developer")
    full_name = Column(String, nullable=True)  # НОВОЕ: ФИО (для developer/admin; для norm_controller можно не заполнять)

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    filename = Column(String)
    upload_date = Column(DateTime)
    status = Column(String, default="processing")
    ann_pdf_path = Column(String, nullable=True)
    description = Column(String, nullable=True)
    versions = relationship("DocumentVersion", backref="document")


class DocumentVersion(Base):
    __tablename__ = "document_versions"
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("documents.id"))
    filename = Column(String)
    upload_date = Column(DateTime)
    ann_pdf_path = Column(String, nullable=True)
    report_path = Column(String, nullable=True)
    verdict_status = Column(String, default="processing")
    verdict_comment = Column(Text, nullable=True)

    # НОВОЕ: кто поставил статус по версии
    verdict_author_name = Column(String, nullable=True)
    verdict_author_role = Column(String, nullable=True)

    decisions = relationship("Decision", backref="version")

    analysis_completed_at = Column(DateTime, nullable=True)

    version_number = Column(Integer, nullable=True, index=True)

class Decision(Base):
    __tablename__ = "decisions"
    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("document_versions.id"))
    error_point = Column(String)
    status = Column(String)            # 'fixed' | 'rejected'
    author = Column(String)            # теперь храним ФИО или "Цифровой помощник конструктора"
    author_role = Column(String)       # 'developer' | 'norm_controller' | 'admin' | 'system'
    comment = Column(Text)
    timestamp = Column(DateTime)
