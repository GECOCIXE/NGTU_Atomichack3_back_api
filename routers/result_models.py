from pydantic import BaseModel
from typing import List, Dict, Optional

class ErrorPoint(BaseModel):
    point: str
    description: str
    pdf_url: str
    occ_id: Optional[str] = None

class Decision(BaseModel):
    id: str
    error_point: str
    status: str
    author: str
    author_role: str
    comment: str
    timestamp: str
    occ_id: Optional[str] = None
    version_id: Optional[int] = None
    version_number: Optional[int] = None
    file_fix_url: Optional[str] = None
    file_fix_url_annotated: Optional[str] = None

class DetailedResult(BaseModel):
    id: str
    filename: str
    file_url: Optional[str] = None
    file_url_annotated: Optional[str] = None
    status: str                                  # approved | rejected | removed
    status_author: Optional[str] = None
    processing_status: str                       # processing | complete
    upload_date: Optional[str] = None      # НОВОЕ: ISO-дата/время первой версии
    total_violations: int
    error_points: List[ErrorPoint]
    error_counts: Dict[str, int]
    full_report: str
    decisions: List[Decision]
    final_approved_pdf: Optional[str] = None

