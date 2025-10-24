from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from scripts.db import get_db
from scripts.crud import get_user_by_login
from scripts.models import Document, DocumentVersion, Decision
from scripts.parse_report import parse_report
from routers.dependencies import get_current_user
import os
from datetime import datetime
from typing import List, Dict, Any

router = APIRouter()

# Определение критериев
REQUIREMENTS = [
    "1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5", 
    "1.1.6", "1.1.7", "1.1.8", "1.1.9"
]

def _calculate_severity(total_violations: int, affected_documents: int) -> str:
    """Определение уровня важности на основе количества ошибок и затронутых документов"""
    if affected_documents == 0:
        return "low"
    
    violations_per_document = total_violations / affected_documents if affected_documents > 0 else 0
    
    # Если на один документ приходится много ошибок - высокий уровень
    if violations_per_document >= 5 or total_violations >= 20:
        return "high"
    # Если на один документ приходится несколько ошибок - средний уровень
    elif violations_per_document >= 2 or total_violations >= 5:
        return "medium"
    # Иначе - низкий уровень
    else:
        return "low"

@router.get("/requirements-stats")
def get_requirements_stats(db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Для нормоконтроллера возвращаем статистику по всем разработчикам
    if user.role == "norm_controller":
        # Получаем документы всех разработчиков
        from scripts.models import User
        user_docs = db.query(Document).join(User).filter(User.role == "developer").all()
    else:
        # Для обычного пользователя возвращаем только его документы
        user_docs = db.query(Document).filter(Document.user_id == user.id).all()
    
    # Словарь для хранения статистики по критериям
    req_stats: Dict[str, Dict[str, Any]] = {}
    violation_docs: List[Dict[str, Any]] = []
    
    # Инициализация статистики для каждого критерия
    for req in REQUIREMENTS:
        req_stats[req] = {
            "id": f"req-{req.replace('.', '-')}",
            "title": req,
            "totalViolations": 0,
            "affectedDocuments": set(),
            "severity": "low"  # будет пересчитан позже
        }
    
    # Обработка всех версий документов пользователя
    for doc in user_docs:
        versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).all()
        
        for version in versions:
            # Проверяем наличие отчета
            if version.report_path and os.path.exists(version.report_path):
                try:
                    with open(version.report_path, "r", encoding="utf-8") as f:
                        report_content = f.read()
                    
                    # Парсим отчет
                    parsed_report = parse_report(report_content, doc_id=doc.id)
                    error_points = parsed_report.get("error_points", [])
                    
                    # Обновляем статистику для каждого критерия
                    for error_point in error_points:
                        point = error_point.get("point")
                        description = error_point.get("description", "")
                        occ_id = error_point.get("occ_id")
                        error_num = error_point.get("error_num")  # Номер ошибки из отчета
                        
                        if point in req_stats:
                            req_stats[point]["totalViolations"] += 1
                            req_stats[point]["affectedDocuments"].add(doc.id)
                            
                            # Формируем путь к специфичному PDF файлу для этой ошибки
                            specific_pdf_url = version.ann_pdf_path if version.ann_pdf_path else f"data/original/{doc.id}/v{version.version_number}/{doc.filename}"  # Путь по умолчанию
                            
                            # Если есть информация о номере ошибки и аннотированный PDF-файл существует
                            if error_num and version.ann_pdf_path and os.path.exists(version.ann_pdf_path):
                                # Получаем имя базового файла без расширения
                                base_name = os.path.splitext(os.path.basename(version.ann_pdf_path))[0]
                                # Убираем суффикс ".annotated" из base_name, чтобы получить корректное имя файла
                                if base_name.endswith('.annotated'):
                                    base_name = base_name[:-len('.annotated')]
                                # Формируем имя специфичного PDF файла с номером ошибки
                                specific_pdf_name = f"{base_name}.error_{error_num}.pdf"
                                specific_pdf_path = os.path.join(os.path.dirname(version.ann_pdf_path), specific_pdf_name)
                                # Проверяем, существует ли такой файл
                                if os.path.exists(specific_pdf_path):
                                    # Используем путь к специфичному PDF файлу ошибки
                                    specific_pdf_url = specific_pdf_path
                                else:
                                    # Если специфичный файл не существует, используем общий аннотированный PDF
                                    specific_pdf_url = version.ann_pdf_path
                            
                            # Добавляем информацию о документе с нарушением
                            # Временно используем "low", т.к. severity будет обновлена позже
                            violation_docs.append({
                                "id": str(doc.id),
                                "fileName": doc.filename,
                                "fileType": "PDF",
                                "uploadDate": doc.upload_date.isoformat() if doc.upload_date else "",
                                "pdfUrl": f"/download/{doc.id}",
                                "violationDetails": {
                                    "requirementId": f"req-{point.replace('.', '-')}",
                                    "description": description,
                                    "severity": "low",  # будет обновлено позже
                                    "pdfAnnotationUrl": specific_pdf_url
                                }
                            })
                except Exception as e:
                    # Просто пропускаем, если не удалось прочитать отчет
                    continue
    
    # Преобразуем наборы документов в количество и вычисляем severity
    for req in REQUIREMENTS:
        req_stats[req]["affectedDocuments"] = len(req_stats[req]["affectedDocuments"])
        # Вычисляем severity на основе статистики
        req_stats[req]["severity"] = _calculate_severity(
            req_stats[req]["totalViolations"], 
            req_stats[req]["affectedDocuments"]
        )
    
    # Подготовка результата
    requirements_stats = []
    for req in REQUIREMENTS:
        requirements_stats.append({
            "id": req_stats[req]["id"],
            "title": req_stats[req]["title"],
            "totalViolations": req_stats[req]["totalViolations"],
            "affectedDocuments": req_stats[req]["affectedDocuments"],
            "severity": req_stats[req]["severity"]
        })
    
    # Обновляем severity в violation_docs
    for doc in violation_docs:
        req_id = doc["violationDetails"]["requirementId"].replace("req-", "").replace("-", ".")
        if req_id in req_stats:
            doc["violationDetails"]["severity"] = req_stats[req_id]["severity"]
    
    # Возвращаем результат в требуемом формате
    return {
        "requirementsStats": requirements_stats,
        "violationDocuments": violation_docs
    }


@router.get("/requirements-stats/developer/{developer_id}")
def get_requirements_stats_for_developer(
    developer_id: int,
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Только нормоконтроллер может получить статистику по конкретному разработчику
    if user.role != "norm_controller":
        raise HTTPException(status_code=403, detail="Only norm_controller can access developer requirements statistics")

    # Проверяем, что указанный пользователь - разработчик
    from scripts.models import User
    target_dev = db.query(User).filter(User.id == developer_id, User.role == "developer").first()
    if not target_dev:
        raise HTTPException(status_code=404, detail="Developer not found or not a developer")

    # Получаем документы указанного разработчика
    dev_docs = db.query(Document).filter(Document.user_id == developer_id).all()

    # Словарь для хранения статистики по критериям
    req_stats: Dict[str, Dict[str, Any]] = {}
    violation_docs: List[Dict[str, Any]] = []

    # Инициализация статистики для каждого критерия
    for req in REQUIREMENTS:
        req_stats[req] = {
            "id": f"req-{req.replace('.', '-')}",
            "title": req,
            "totalViolations": 0,
            "affectedDocuments": set(),
            "severity": "low"  # будет пересчитан позже
        }

    # Обработка всех версий документов разработчика
    for doc in dev_docs:
        versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).all()

        for version in versions:
            # Проверяем наличие отчета
            if version.report_path and os.path.exists(version.report_path):
                try:
                    with open(version.report_path, "r", encoding="utf-8") as f:
                        report_content = f.read()

                    # Парсим отчет
                    parsed_report = parse_report(report_content, doc_id=doc.id)
                    error_points = parsed_report.get("error_points", [])

                    # Обновляем статистику для каждого критерия
                    for error_point in error_points:
                        point = error_point.get("point")
                        description = error_point.get("description", "")
                        occ_id = error_point.get("occ_id")
                        error_num = error_point.get("error_num")  # Номер ошибки из отчета

                        if point in req_stats:
                            req_stats[point]["totalViolations"] += 1
                            req_stats[point]["affectedDocuments"].add(doc.id)

                            # Формируем путь к специфичному PDF файлу для этой ошибки
                            specific_pdf_url = version.ann_pdf_path if version.ann_pdf_path else f"data/original/{doc.id}/v{version.version_number}/{doc.filename}"  # Путь по умолчанию
                            
                            # Если есть информация о номере ошибки и аннотированный PDF-файл существует
                            if error_num and version.ann_pdf_path and os.path.exists(version.ann_pdf_path):
                                # Получаем имя базового файла без расширения
                                base_name = os.path.splitext(os.path.basename(version.ann_pdf_path))[0]
                                # Убираем суффикс ".annotated" из base_name, чтобы получить корректное имя файла
                                if base_name.endswith('.annotated'):
                                    base_name = base_name[:-len('.annotated')]
                                # Формируем имя специфичного PDF файла с номером ошибки
                                specific_pdf_name = f"{base_name}.error_{error_num}.pdf"
                                specific_pdf_path = os.path.join(os.path.dirname(version.ann_pdf_path), specific_pdf_name)
                                # Проверяем, существует ли такой файл
                                if os.path.exists(specific_pdf_path):
                                    # Используем путь к специфичному PDF файлу ошибки
                                    specific_pdf_url = specific_pdf_path
                                else:
                                    # Если специфичный файл не существует, используем общий аннотированный PDF
                                    specific_pdf_url = version.ann_pdf_path

                            # Добавляем информацию о документе с нарушением
                            # Временно используем "low", т.к. severity будет обновлена позже
                            violation_docs.append({
                                "id": str(doc.id),
                                "fileName": doc.filename,
                                "fileType": "PDF",
                                "uploadDate": doc.upload_date.isoformat() if doc.upload_date else "",
                                "pdfUrl": f"/download/{doc.id}",
                                "violationDetails": {
                                    "requirementId": f"req-{point.replace('.', '-')}",
                                    "description": description,
                                    "severity": "low",  # будет обновлено позже
                                    "pdfAnnotationUrl": specific_pdf_url
                                }
                            })
                except Exception as e:
                    # Просто пропускаем, если не удалось прочитать отчет
                    continue

    # Преобразуем наборы документов в количество и вычисляем severity
    for req in REQUIREMENTS:
        req_stats[req]["affectedDocuments"] = len(req_stats[req]["affectedDocuments"])
        # Вычисляем severity на основе статистики
        req_stats[req]["severity"] = _calculate_severity(
            req_stats[req]["totalViolations"],
            req_stats[req]["affectedDocuments"]
        )

    # Подготовка результата
    requirements_stats = []
    for req in REQUIREMENTS:
        requirements_stats.append({
            "id": req_stats[req]["id"],
            "title": req_stats[req]["title"],
            "totalViolations": req_stats[req]["totalViolations"],
            "affectedDocuments": req_stats[req]["affectedDocuments"],
            "severity": req_stats[req]["severity"]
        })

    # Обновляем severity в violation_docs
    for doc in violation_docs:
        req_id = doc["violationDetails"]["requirementId"].replace("req-", "").replace("-", ".")
        if req_id in req_stats:
            doc["violationDetails"]["severity"] = req_stats[req_id]["severity"]

    return {
        "developer_info": {
            "id": target_dev.id,
            "login": target_dev.login,
            "full_name": target_dev.full_name if target_dev.full_name else target_dev.login
        },
        "requirementsStats": requirements_stats,
        "violationDocuments": violation_docs
    }


    # Теперь пути к файлам возвращаются напрямую, специфичный эндпоинт для скачивания больше не нужен
    # Путь к специфичному PDF файлу теперь формируется в формате: data/original/{doc.id}/v{version_number}/{filename}.error_{error_num}.pdf