import os
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.crud import get_document, get_user_by_login, list_versions_for_document, list_decisions_for_version, set_verdict, add_decision, update_decision, get_decision_by_id, get_decision_by_occ_id, get_decisions_by_version_and_point
from routers.dependencies import get_current_user
from scripts.parse_report import parse_report
from .result_models import DetailedResult, ErrorPoint
from typing import List, Dict, Optional

router = APIRouter()

@router.get("/result/{doc_id}", response_model=DetailedResult)
def get_result(doc_id: int, db: Session = Depends(get_db), current_user: str = Depends(get_current_user)):
    import os, re
    from datetime import datetime
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Проверяем доступ: нормоконтроллер может видеть документы разработчиков, 
    # обычный пользователь - только свои
    if user.role == "norm_controller":
        from scripts.models import User
        doc_user = db.query(User).filter(User.id == doc.user_id).first()
        if not doc_user or doc_user.role != "developer":
            raise HTTPException(status_code=403, detail="Norm controller can only access developer documents")
    else:
        if doc.user_id != user.id:
            raise HTTPException(status_code=404, detail="Document not found")

    versions = list_versions_for_document(db, doc.id)
    if not versions:
        return {
            "id": str(doc.id),
            "filename": doc.filename,
            "file_url": None,
            "file_url_annotated": None,
            "status": "rejected",
            "status_author": "Цифровой помощник конструктора",
            "processing_status": "processing",
            "upload_date": "",
            "total_violations": 0,
            "error_points": [],
            "error_counts": {},
            "full_report": "",
            "decisions": [],
        }

    # ---- последняя и первая версии ----
    latest = versions[0]        # ожидается desc по version_number/дате
    first_v = versions[-1]      # самая первая

    # ---- upload_date: самая ранняя дата из версий / документа ----
    def _iso(d):
        try:
            return d.isoformat()
        except Exception:
            return ""
    # возьмём минимум среди версий
    min_upload_dt = None
    for v in versions:
        if getattr(v, "upload_date", None):
            min_upload_dt = v.upload_date if min_upload_dt is None else min(min_upload_dt, v.upload_date)
    if not min_upload_dt and getattr(doc, "upload_date", None):
        min_upload_dt = doc.upload_date
    upload_date = _iso(min_upload_dt) if min_upload_dt else ""

    # ---- пути ПЕРВОЙ версии (для file_url/annotated) ----
    first_base = f"data/original/{doc.id}/v{first_v.version_number}"
    # file_url должен указывать на исходный PDF файл, а не на отчет
    file_url = f"{first_base}/{first_v.filename or ''}" if first_v else ""
    first_ann_name = os.path.splitext(first_v.filename or "document")[0] + ".annotated.pdf"
    file_url_annotated = first_v.ann_pdf_path if first_v and first_v.ann_pdf_path else ""

    # ---- helper: парсер отчёта версии -> (occurrences, counts, full_report, error_points) ----
    def _parse_version(v):
        txt = ""
        if getattr(v, "report_path", None) and os.path.exists(v.report_path):
            with open(v.report_path, "r", encoding="utf-8") as f:
                txt = f.read()
        parsed = parse_report(txt, doc_id=doc.id)
        occs = parsed.get("occurrences", []) or []
        counts = parsed.get("error_counts", {}) or {}
        full = parsed.get("full_report", txt or "")
        error_points = parsed.get("error_points", []) or []
        return occs, counts, full, error_points

    # ---- базовые (frozen) из ПЕРВОЙ версии ----
    first_occs, first_counts, _, first_error_points = _parse_version(first_v)
    # frozen строим по occurrences, чтобы иметь occ_id
    frozen_error_points = []
    frozen_error_counts = {}
    seen_occ_ids = set()

    for error_point in first_error_points:
        oid = error_point.get("occ_id") or ""
        pt  = (error_point.get("point") or "").strip()
        desc = error_point.get("description") or ""
        error_num = error_point.get("error_num")  # Номер ошибки из отчета
        if not pt:
            continue
        # элемент списка
        # Формируем путь к специфичному PDF файлу для этой ошибки
        specific_pdf_url = ""
        if first_v and first_v.ann_pdf_path and error_num:
            # Получаем базовую директорию
            base_dir = os.path.dirname(first_v.ann_pdf_path)
            # Получаем имя файла без расширения
            base_name = os.path.splitext(os.path.basename(first_v.ann_pdf_path))[0]
            # Убираем суффикс ".annotated" из base_name, чтобы получить корректное имя файла
            if base_name.endswith('.annotated'):
                base_name = base_name[:-len('.annotated')]
            # Формируем имя специфичного PDF файла с номером ошибки
            specific_pdf_name = f"{base_name}.error_{error_num}.pdf"
            specific_pdf_url = os.path.join(base_dir, specific_pdf_name)
            # Проверяем, существует ли такой файл
            if not os.path.exists(specific_pdf_url):
                # Если специфичный файл не существует, используем общий аннотированный PDF
                specific_pdf_url = first_v.ann_pdf_path
        elif first_v and first_v.ann_pdf_path:
            # Если номер ошибки не доступен, используем общий аннотированный PDF
            specific_pdf_url = first_v.ann_pdf_path
        else:
            specific_pdf_url = ""
            
        frozen_error_points.append({
            "point": pt,
            "description": desc,
            "pdf_url": specific_pdf_url,
            "occ_id": oid,
            "final_pdf_url": _get_final_pdf_for_criterion(db, doc.id, pt)  # Финальный PDF для критерия
        })
        seen_occ_ids.add(oid)
        # счётчики
        frozen_error_counts[pt] = frozen_error_counts.get(pt, 0) + 1

    # если в первой версии parse_report не отдаёт occurrences, fallback на counts
    if not frozen_error_points and first_counts:
        for pt, cnt in first_counts.items():
            frozen_error_counts[pt] = int(cnt or 0)

    # ---- ДОПОЛНЯЕМ frozen НОВЫМИ ошибками из всех последующих версий ----
    # (объединение по occ_id; если новый occ_id у существующего пункта — просто увеличиваем счётчик и добавляем запись)
    for v in reversed(versions[:-1]):  # от ранних к поздним, но без самой первой (она уже учтена)
        _, _, _, error_points = _parse_version(v)
        for error_point in error_points:
            oid = error_point.get("occ_id") or ""
            if not oid or oid in seen_occ_ids:
                continue
            pt = (error_point.get("point") or "").strip()
            if not pt:
                continue
            desc = error_point.get("description") or ""
            error_num = error_point.get("error_num")  # Номер ошибки из отчета
            # добавим запись в список (чтобы фронт видел «ещё одну ошибку по этому пункту»)
            # Формируем путь к специфичному PDF файлу для этой ошибки
            specific_pdf_url = ""
            if v and v.ann_pdf_path and error_num:
                # Получаем базовую директорию
                base_dir = os.path.dirname(v.ann_pdf_path)
                # Получаем имя файла без расширения
                base_name = os.path.splitext(os.path.basename(v.ann_pdf_path))[0]
                # Убираем суффикс ".annotated" из base_name, чтобы получить корректное имя файла
                if base_name.endswith('.annotated'):
                    base_name = base_name[:-len('.annotated')]
                # Формируем имя специфичного PDF файла с номером ошибки
                specific_pdf_name = f"{base_name}.error_{error_num}.pdf"
                specific_pdf_url = os.path.join(base_dir, specific_pdf_name)
                # Проверяем, существует ли такой файл
                if not os.path.exists(specific_pdf_url):
                    # Если специфичный файл не существует, используем общий аннотированный PDF
                    specific_pdf_url = v.ann_pdf_path
            elif v and v.ann_pdf_path:
                # Если номер ошибки не доступен, используем общий аннотированный PDF
                specific_pdf_url = v.ann_pdf_path
            else:
                specific_pdf_url = ""
                
            frozen_error_points.append({
                "point": pt,
                "description": desc,
                "pdf_url": specific_pdf_url,
                "occ_id": oid,
                "final_pdf_url": _get_final_pdf_for_criterion(db, doc.id, pt)  # Финальный PDF для критерия
            })
            seen_occ_ids.add(oid)
            # обновим счётчик для пункта
            frozen_error_counts[pt] = frozen_error_counts.get(pt, 0) + 1

    # ---- ДОПОЛНИТЕЛЬНАЯ ПРОВЕРКА УНИКАЛЬНОСТИ frozen_error_points ----
    # Убедимся, что нет дублирующихся occ_id в frozen_error_points
    unique_occ_ids = set()
    unique_error_points = []
    for error_point in frozen_error_points:
        occ_id = error_point.get("occ_id", "")
        if occ_id and occ_id not in unique_occ_ids:
            unique_error_points.append(error_point)
            unique_occ_ids.add(occ_id)
    
    frozen_error_points = unique_error_points

    frozen_total = sum(int(v or 0) for v in frozen_error_counts.values())

    # ---- live (последняя версия) — нужен для статусов и full_report ----
    latest_occs, latest_counts, latest_full, _ = _parse_version(latest)
    processing_status = "complete" if latest_occs or (getattr(latest, "report_path", None) and os.path.exists(latest.report_path)) else "processing"

    # ---- статус файла (approved / rejected / removed) ----
    allowed = {"approved", "rejected", "removed"}
    if getattr(latest, "verdict_status", None) in allowed:
        file_status = latest.verdict_status
    else:
        file_status = "approved" if (processing_status == "complete" and sum(latest_counts.values()) == 0) else "rejected"

    status_author = getattr(latest, "verdict_author_name", None) or "Цифровой помощник конструктора"

    # ---- decisions (вся история) ----
    def _extract_occ_id(comment: str | None) -> str | None:
        if not comment:
            return None
        m = re.search(r"\[occ:([0-9a-fA-F]{6,64})\]|\(occ:([0-9a-fA-F]{6,64})\)", comment)
        return (m.group(1) or m.group(2)) if m else None

    def _occ_point_map_for_version(v) -> dict:
        mp = {}
        occs, _, _, _ = _parse_version(v)
        for o in occs:
            if o.get("id") and o.get("point"):
                mp[o["id"]] = o["point"]
        return mp

    all_decisions = []
    seen_decision_ids = set()
    for ver in versions:
        rows = list_decisions_for_version(db, ver.id)
        occ_point_map = _occ_point_map_for_version(ver)

        base_dir = f"data/original/{doc.id}/v{ver.version_number}"
        # original_path должен указывать на исходный PDF файл, а не на отчет
        original_path = f"{base_dir}/{ver.filename or ''}" if ver else ""
        annotated_path = ver.ann_pdf_path if ver and ver.ann_pdf_path else ""

        def _ts_key(d):
            try:
                return d.timestamp.isoformat() if hasattr(d.timestamp, "isoformat") else (d.timestamp or "")
            except Exception:
                return ""

        for d in sorted(rows, key=lambda x: _ts_key(x)):
            if d.id in seen_decision_ids:
                continue
            seen_decision_ids.add(d.id)

            occ_id = _extract_occ_id(d.comment)
            ep = d.error_point or (occ_point_map.get(occ_id, "") if occ_id else "")

            # Получаем номер ошибки из решения, если он есть
            # Для этого нужно получить информацию об ошибке из результата парсинга
            error_num = None
            # Получаем все ошибки для текущей версии, чтобы найти error_num для текущего occ_id
            _, _, _, version_error_points = _parse_version(ver)
            for error_point in version_error_points:
                if error_point.get("occ_id") == occ_id:
                    error_num = error_point.get("error_num")
                    break

            # Формируем путь к специфичному PDF файлу для этой ошибки
            specific_pdf_url = ""
            if ver and ver.ann_pdf_path and error_num:
                # Получаем базовую директорию
                base_dir = os.path.dirname(ver.ann_pdf_path)
                # Получаем имя файла без расширения
                base_name = os.path.splitext(os.path.basename(ver.ann_pdf_path))[0]
                # Убираем суффикс ".annotated" из base_name, чтобы получить корректное имя файла
                if base_name.endswith('.annotated'):
                    base_name = base_name[:-len('.annotated')]
                # Формируем имя специфичного PDF файла с номером ошибки
                specific_pdf_name = f"{base_name}.error_{error_num}.pdf"
                specific_pdf_url = os.path.join(base_dir, specific_pdf_name)
                # Проверяем, существует ли такой файл
                if not os.path.exists(specific_pdf_url):
                    # Если специфичный файл не существует, используем общий аннотированный PDF
                    specific_pdf_url = ver.ann_pdf_path
            elif ver and ver.ann_pdf_path:
                # Если номер ошибки не доступен, используем общий аннотированный PDF
                specific_pdf_url = ver.ann_pdf_path
            else:
                specific_pdf_url = ""

            is_dev = (d.author_role == "developer")
            file_fix_url = original_path if is_dev else ""
            file_fix_url_annotated = specific_pdf_url if not is_dev else ""

            all_decisions.append({
                "id": str(d.id),
                "error_point": ep,
                "status": d.status,
                "author": d.author,
                "author_role": d.author_role,
                "comment": d.comment or "",
                "timestamp": d.timestamp.isoformat() if hasattr(d.timestamp, "isoformat") and d.timestamp else (d.timestamp or ""),
                "occ_id": occ_id,
                "version_id": ver.id,
                "version_number": getattr(ver, "version_number", None),
                "file_fix_url": file_fix_url,
                "file_fix_url_annotated": file_fix_url_annotated,
            })

    # ---- итоговый ответ ----
    return {
        "id": str(doc.id),
        "filename": doc.filename,
        # корневые файлы ПЕРВОЙ версии
        "file_url": file_url,
        "file_url_annotated": file_url_annotated,
        # статусы
        "status": file_status,
        "status_author": status_author,
        "processing_status": processing_status,
        # дата первой загрузки (не будет null)
        "upload_date": upload_date,
        # FROZEN: первая версия + все новые найденные ошибки из следующих версий
        "total_violations": int(frozen_total),
        "error_points": frozen_error_points,
        "error_counts": frozen_error_counts,
        # отчёт последней версии
        "full_report": latest_full,
        # вся история решений
        "decisions": all_decisions,
        # Финальные PDF файлы
        "final_approved_pdf": _get_final_approved_pdf(db, doc.id),
    }


@router.post("/result/{doc_id}/status")
def update_file_status(
    doc_id: int, 
    status: str, 
    comment: str = None, 
    db: Session = Depends(get_db), 
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Только norm_controller может менять статус
    if user.role != "norm_controller":
        raise HTTPException(status_code=403, detail="Only norm_controller can update file status")
    
    # Получаем документ
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Проверяем, что это документ разработчика
    from scripts.models import User
    doc_user = db.query(User).filter(User.id == doc.user_id).first()
    if not doc_user or doc_user.role != "developer":
        raise HTTPException(status_code=403, detail="Can only update developer documents")
    
    # Получаем последнюю версию
    versions = list_versions_for_document(db, doc.id)
    if not versions:
        raise HTTPException(status_code=404, detail="Document has no versions")
    
    latest_version = versions[0]  # самая последняя версия
    
    # Если комментарий не предоставлен, сохраняем предыдущий комментарий
    if comment is None:
        comment = latest_version.verdict_comment
    
    # Обновляем статус версии
    updated_version = set_verdict(
        db, 
        latest_version.id, 
        status, 
        comment, 
        author_name=user.full_name or user.login, 
        author_role=user.role
    )
    
    if not updated_version:
        raise HTTPException(status_code=400, detail="Failed to update status")
    
    return {
        "success": True,
        "message": f"Status updated to {status}",
        "document_id": doc.id,
        "version_id": latest_version.id,
        "new_status": status,
        "status_author": user.full_name or user.login
    }


@router.post("/result/{doc_id}/criterion-status")
def update_criterion_status(
    doc_id: int,
    occ_id: str,
    error_point: str,
    status: str,
    comment: str = "",
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Только norm_controller может менять статус критерия
    if user.role != "norm_controller":
        raise HTTPException(status_code=403, detail="Only norm_controller can update criterion status")
    
    # Получаем документ
    doc = get_document(db, doc_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    
    # Проверяем, что это документ разработчика
    from scripts.models import User
    doc_user = db.query(User).filter(User.id == doc.user_id).first()
    if not doc_user or doc_user.role != "developer":
        raise HTTPException(status_code=403, detail="Can only update developer documents")
    
    # Получаем последнюю версию
    versions = list_versions_for_document(db, doc.id)
    if not versions:
        raise HTTPException(status_code=404, detail="Document has no versions")
    
    latest_version = versions[0]  # самая последняя версия
    
    # Ищем существующее решение для этой конкретной ошибки (occ_id)
    from scripts.crud import get_decision_by_occ_id
    decision = get_decision_by_occ_id(db, latest_version.id, error_point, occ_id)
    
    from datetime import datetime
    timestamp = datetime.utcnow()
    
    if decision:
        # Обновляем существующее решение
        from scripts.crud import update_decision
        updated_decision = update_decision(
            db,
            decision_id=decision.id,
            status=status,
            author=user.full_name or user.login,
            author_role=user.role,
            comment=decision.comment  # Сохраняем оригинальный комментарий
        )
        
        if not updated_decision:
            raise HTTPException(status_code=400, detail="Failed to update criterion decision")
        
        operation = "updated"
        decision_id = decision.id
        old_status = decision.status
    else:
        # Создаем новое решение для этой ошибки
        from scripts.crud import add_decision
        # Добавляем тег [occ:...] в комментарий, чтобы можно было идентифицировать это решение
        occ_tag = f"[occ:{occ_id}]"
        full_comment = f"{occ_tag} {comment}" if comment else f"{occ_tag}"
        
        new_decision = add_decision(
            db,
            version_id=latest_version.id,
            error_point=error_point,
            status=status,
            author=user.full_name or user.login,
            author_role=user.role,
            comment=full_comment,
            timestamp=timestamp
        )
        
        if not new_decision:
            raise HTTPException(status_code=400, detail="Failed to add criterion decision")
        
        operation = "created"
        decision_id = new_decision.id
        old_status = None
    

    
    return {
        "success": True,
        "message": f"Decision for occurrence {occ_id} {operation} to status {status}",
        "document_id": doc.id,
        "version_id": latest_version.id,
        "decision_id": decision_id,
        "old_status": old_status,
        "new_status": status,
        "error_point": error_point,
        "occ_id": occ_id,
        "operation": operation  # "created" или "updated"
    }


def _get_final_pdf_for_criterion(db: Session, doc_id: int, criterion_point: str) -> str:
    """
    Получает путь к финальному PDF для конкретного критерия ошибки
    Возвращает путь к PDF последней версии, где данный критерий был исправлен
    """
    try:
        # Получаем все версии документа
        versions = list_versions_for_document(db, doc_id)
        if not versions:
            return ""
        
        # Ищем последнюю версию, где есть решения для этого критерия
        latest_applicable_version = None
        latest_decision_date = None
        
        for version in versions:
            decisions = list_decisions_for_version(db, version.id)
            # Ищем решения для данного критерия с статусом 'fixed'
            applicable_decisions = [
                d for d in decisions 
                if d.error_point == criterion_point and d.status == 'fixed'
            ]
            
            if applicable_decisions:
                # Если нашли решения для этого критерия, проверяем дату
                for decision in applicable_decisions:
                    decision_time = decision.timestamp
                    if latest_decision_date is None or decision_time > latest_decision_date:
                        latest_decision_date = decision_time
                        latest_applicable_version = version
        
        # Если не нашли версию с исправлениями для этого критерия, используем последнюю
        if latest_applicable_version is None:
            latest_applicable_version = versions[0]  # берем последнюю версию
        
        # Возвращаем путь к аннотированному PDF этой версии
        if latest_applicable_version and latest_applicable_version.ann_pdf_path:
            return latest_applicable_version.ann_pdf_path
            
        return ""
    except Exception:
        return ""


def _get_final_approved_pdf(db: Session, doc_id: int) -> str:
    """
    Получает путь к финальному утвержденному PDF документа
    """
    try:
        doc = get_document(db, doc_id)
        if not doc or doc.status != "approved":
            return ""
        
        # Получаем последнюю версию
        versions = list_versions_for_document(db, doc_id)
        if not versions:
            return ""
        
        latest_version = versions[0]  # самая последняя версия
        
        # Используем путь из БД
        if latest_version and latest_version.ann_pdf_path:
            return latest_version.ann_pdf_path
            
        return ""
    except Exception:
        return ""
