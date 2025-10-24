# routers/upload.py
from fastapi import APIRouter, UploadFile, File, Depends, BackgroundTasks, HTTPException, Query
from sqlalchemy.orm import Session
from scripts.db import get_db
from scripts.db import SessionLocal
from scripts.crud import update_version_analysis
from scripts.crud import (
    create_document,
    get_user_by_login,
    create_version,
    list_versions_for_document,
    get_document,
    add_decision,
)
from scripts.analysis.main import make_report_files, pipeline
from scripts.parse_report import parse_report
from datetime import datetime
import os, glob
import shutil

from routers.dependencies import get_current_user

router = APIRouter()

def _normalize_analysis_result(result, original_path: str):
    """
    Возвращает (ann_pdf_path, report_path) из результата make_report_files.
    Поддерживает tuple/list, dict с разными ключами и fallback-поиск рядом с файлом.
    """
    # tuple/list
    if isinstance(result, (list, tuple)) and len(result) >= 2:
        return str(result[0]), str(result[1])

    # dict
    if isinstance(result, dict):
        ann_keys = ("ann_pdf_path", "annotated_path", "annotated_pdf", "annotated")
        rep_keys = ("report_path", "report", "report_txt", "analysis_report", "txt")
        ann = next((result[k] for k in ann_keys if k in result), None)
        rep = next((result[k] for k in rep_keys if k in result), None)
        if ann and rep:
            return str(ann), str(rep)

    # fallback: ищем *.annotated.pdf и *.report.* в каталоге исходника
    base_dir = os.path.dirname(original_path)
    base_name = os.path.splitext(os.path.basename(original_path))[0]

    cand_ann = os.path.join(base_dir, f"{base_name}.annotated.pdf")
    if not os.path.exists(cand_ann):
        found = glob.glob(os.path.join(base_dir, "*.annotated.pdf"))
        cand_ann = found[0] if found else ""

    rep_glob = glob.glob(os.path.join(base_dir, f"{base_name}.report.*")) or \
               glob.glob(os.path.join(base_dir, "*.report.*")) or \
               glob.glob(os.path.join(base_dir, "*.txt"))
    cand_rep = rep_glob[0] if rep_glob else ""

    return cand_ann, cand_rep

def _run_analysis_and_update(version_id: int, original_path: str):
    """
    Фоновая задача: запускает анализ и сохраняет результаты в БД.
    ВАЖНО: открываем собственную сессию БД — фоновые задачи не получают Depends(get_db).
    """
    # 1) запускаем анализатор: сначала pipeline, затем make_report_files
    try:
        # Запускаем анализ
        pipeline_out = pipeline(original_path)
        # Затем создаем отчеты
        result = make_report_files(original_path, pipeline_out)
    except TypeError:
        # на случай, если анализатор ожидает (path, out_dir=None)
        pipeline_out = pipeline(original_path)
        result = make_report_files(original_path, pipeline_out, None)

    ann_pdf_path, report_path = _normalize_analysis_result(result, original_path)

    # 2) сохраняем в БД и перекладываем файлы в правильную папку v{version_number}
    db = SessionLocal()
    try:
        update_version_analysis(db, version_id, ann_pdf_path, report_path)
    finally:
        db.close()

def _version_dir(doc_id: int, ver_number: int) -> str:
    return f"data/original/{doc_id}/v{ver_number}"

def _collect_occ_map_for_validation(db: Session, doc_id: int) -> dict:
    """
    Берём свежую версию с отчётом и собираем словарь: occ_id -> point.
    """
    versions = list_versions_for_document(db, doc_id)
    for v in versions:  # от свежей к старой
        rp = getattr(v, "report_path", None)
        if rp and os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as f:
                report_content = f.read()
            parsed = parse_report(report_content, doc_id=doc_id)
            occs = parsed.get("occurrences", []) or []
            return {o["id"]: o["point"] for o in occs if o.get("id") and o.get("point")}
    return {}

@router.post("/upload")
async def upload_file(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    doc_id: int | None = Query(default=None),
    fixed_points: str | None = Query(default=None),  # старый режим (по критериям)
    fixed_ids: str | None = Query(default=None),     # НОВОЕ: по конкретным ошибкам (occ_id)
    db: Session = Depends(get_db),
    current_user: str = Depends(get_current_user)
):
    user = get_user_by_login(db, current_user)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    upload_date = datetime.now()

    if doc_id is None:
        # создаём новый документ и первую версию
        doc = create_document(db, user.id, file.filename, upload_date)
        from scripts.crud import list_versions_for_document
        ver = list_versions_for_document(db, doc.id)[0]

        # если кто-то попытается прислать fixed_ids на самом первом аплоаде — некуда валидировать
        if fixed_ids:
            raise HTTPException(
                status_code=400,
                detail="fixed_ids допускаются только при добавлении новой версии к существующему документу (укажите doc_id)."
            )
    else:
        # создаём новую версию существующего документа
        doc = get_document(db, doc_id)
        if not doc or doc.user_id != user.id:
            raise HTTPException(status_code=404, detail="Document not found")
        ver = create_version(db, doc.id, file.filename, upload_date)

    # === ВАЛИДАЦИЯ fixed_ids (если переданы) ===
    occ_map: dict[str, str] = {}  # держим в скоупе функции

    if fixed_ids:
        occ_map = _collect_occ_map_for_validation(db, doc.id)
        if not occ_map:
            raise HTTPException(
                status_code=400,
                detail="Не удалось валидировать fixed_ids: по документу нет ни одной ранее проанализированной версии."
            )
        provided = [s.strip() for s in fixed_ids.split(",") if s.strip()]
        unknown = [oid for oid in provided if oid not in occ_map]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail={"message": "Некоторые fixed_ids не найдены в последнем отчёте", "unknown": unknown}
            )

    # === Сохранение файла версии ===
    doc_dir = _version_dir(doc.id, ver.version_number)
    os.makedirs(doc_dir, exist_ok=True)

    file_path = f"{doc_dir}/{file.filename}"
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    # === Сохраняем "заявки" разработчика на исправления ===
    author_name = user.full_name or user.login

    # 1) по критериям (бек-совместимость)
    if fixed_points:
        for p in [p.strip() for p in fixed_points.split(",") if p.strip()]:
            add_decision(
                db,
                version_id=ver.id,
                error_point=p,
                status="fixed",
                author=author_name,
                author_role="developer",
                comment="Отмечено как исправлено разработчиком (по критерию)",
                timestamp=datetime.utcnow(),
            )

    # 2) по конкретным срабатываниям (occ_id) — записываем и критерий, и тег [occ:...]
    if fixed_ids:
        for oid in [s.strip() for s in fixed_ids.split(",") if s.strip()]:
            point = occ_map.get(oid, "")
            add_decision(
                db,
                version_id=ver.id,
                error_point=point,  # <-- критерия теперь не пустая
                status="fixed",
                author=author_name,
                author_role="developer",
                comment=f"[occ:{oid}] Отмечено как исправлено разработчиком (конкретная ошибка)",
                timestamp=datetime.utcnow(),
            )

    # === Запускаем анализ (analysis-скрипты НЕ трогаем) ===
    # Вызов остаётся совместимым с вашими скриптами: передаём doc.id
    background_tasks.add_task(_run_analysis_and_update, ver.id, file_path)

    return {
        "document_id": doc.id,
        "version_id": ver.id,
        "filename": file.filename,
        "upload_date": upload_date
    }
