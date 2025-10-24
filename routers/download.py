import os
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_document, get_user_by_login
from routers.dependencies import get_current_user

router = APIRouter()

@router.get("/download/{doc_id}")
def download_original(doc_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = get_document(db, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    file_path = os.path.join("data", "original", str(doc_id), doc.filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path, filename=doc.filename)

@router.get("/download_annotated/{doc_id}")
def download_annotated(doc_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    doc = get_document(db, doc_id)
    if not doc or doc.user_id != user.id:
        raise HTTPException(status_code=404, detail="Document not found")
    if not doc.ann_pdf_path:
        raise HTTPException(status_code=404, detail="Annotated file not available")
    file_path = doc.ann_pdf_path
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Annotated file not found")
    annotated_filename = os.path.basename(file_path)
    return FileResponse(file_path, filename=annotated_filename)

@router.get("/download_path")
def download_file_by_path(file_path: str, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Безопасность: проверяем, что путь начинается с разрешенных директорий
    # и не содержит паттернов, указывающих на выход из разрешенной области
    if '..' in file_path or file_path.startswith('/') or ':\\' in file_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    # Разрешаем только доступ к файлам в определенных директориях
    allowed_paths = ["data", "reports", "output"]  # список разрешенных корневых директорий
    if not any(file_path.startswith(allowed) for allowed in allowed_paths):
        raise HTTPException(status_code=403, detail="Access to this path is not allowed")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Проверяем, что файл принадлежит пользователю
    # Для этого нужно определить ID документа из пути и проверить доступ
    import re
    doc_id_match = re.search(r'data[/\\]original[/\\](\d+)', file_path)
    if doc_id_match:
        doc_id = int(doc_id_match.group(1))
        doc = get_document(db, doc_id)
        if not doc or doc.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access to this document is not allowed")
    else:
        # Если не можем извлечь doc_id из пути, проверить, что пользователь нормоконтроллер
        if user.role != "norm_controller":
            raise HTTPException(status_code=403, detail="Access denied")
    
    filename = os.path.basename(file_path)
    return FileResponse(file_path, filename=filename)


@router.get("/download_annotated_path")
def download_annotated_by_path(file_path: str, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Безопасность: проверяем, что путь начинается с разрешенных директорий
    # и не содержит паттернов, указывающих на выход из разрешенной области
    if '..' in file_path or file_path.startswith('/') or ':\\' in file_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    
    # Разрешаем только доступ к файлам в определенных директориях
    allowed_paths = ["data", "reports", "output"]  # список разрешенных корневых директорий
    if not any(file_path.startswith(allowed) for allowed in allowed_paths):
        raise HTTPException(status_code=403, detail="Access to this path is not allowed")
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    
    # Проверяем, что файл принадлежит пользователю
    # Для этого нужно определить ID документа из пути и проверить доступ
    import re
    doc_id_match = re.search(r'data[/\\]original[/\\](\d+)', file_path)
    if doc_id_match:
        doc_id = int(doc_id_match.group(1))
        doc = get_document(db, doc_id)
        if not doc or doc.user_id != user.id:
            raise HTTPException(status_code=403, detail="Access to this document is not allowed")
    else:
        # Если не можем извлечь doc_id из пути, проверить, что пользователь нормоконтроллер
        if user.role != "norm_controller":
            raise HTTPException(status_code=403, detail="Access denied")
    
    filename = os.path.basename(file_path)
    return FileResponse(file_path, filename=filename)