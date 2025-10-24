import os, re, shutil
from dotenv import load_dotenv
from jose import jwt
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from .models import User, Document, DocumentVersion, Decision
import hashlib
from sqlalchemy import func
import shutil
from .parse_report import parse_report

load_dotenv()
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 9999

def get_password_hash(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return get_password_hash(plain_password) == hashed_password

def get_user_by_login(db: Session, login: str):
    return db.query(User).filter(User.login == login).first()

def get_user_by_id(db: Session, user_id: int):
    from .models import User
    return db.query(User).filter(User.id == user_id).first()

# НОВОЕ: регистрация с ФИО (роль всё равно всегда 'developer')
def create_user(db: Session, login: str, password: str, full_name: str | None = None, role: str = "developer"):
    existing = get_user_by_login(db, login)
    if existing:
        # если хотим обновлять ФИО при повторной регистрации: раскомментируйте следующие 2 строки
        # if full_name and not existing.full_name:
        #     existing.full_name = full_name; db.commit()
        return existing
    user = User(
        login=login,
        hashed_password=get_password_hash(password),
        role=role,
        full_name=full_name
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user

def authenticate_user(db: Session, login: str, password: str):
    user = get_user_by_login(db, login)
    if not user:
        return None
    if not verify_password(password, user.hashed_password):
        return None
    return user

def create_access_token(data: dict, expires_delta: timedelta | None = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_document(db: Session, user_id: int, filename: str, upload_date: datetime):
    doc = Document(user_id=user_id, filename=filename, upload_date=upload_date, status="processing")
    db.add(doc); db.commit(); db.refresh(doc)
    create_version(db, doc.id, filename, upload_date)
    return doc

def create_version(db: Session, document_id: int, filename: str, upload_date: datetime):
    # присвоим локный номер версии = (текущее макс по документу) + 1
    from .models import DocumentVersion
    last_num = db.query(DocumentVersion) \
                 .filter(DocumentVersion.document_id == document_id) \
                 .with_entities(func.max(DocumentVersion.version_number)) \
                 .scalar()
    next_num = (last_num or 0) + 1

    ver = DocumentVersion(
        document_id=document_id,
        filename=filename,
        upload_date=upload_date,
        verdict_status="processing",
        version_number=next_num,
    )
    db.add(ver); db.commit(); db.refresh(ver)
    return ver

def update_version_analysis(db: Session, version_id: int, ann_pdf_path: str, report_path: str):
    ver = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
    if not ver:
        return None
    doc = db.query(Document).filter(Document.id == ver.document_id).first()
    if not doc:
        return None

    # ---- финальные пути в v{version_number} ----
    base_dir = os.path.join("data", "original", str(doc.id), f"v{ver.version_number}")
    os.makedirs(base_dir, exist_ok=True)

    ann_name = os.path.basename(ann_pdf_path) if ann_pdf_path else (os.path.splitext(ver.filename or "document")[0] + ".annotated.pdf")
    if not ann_name.lower().endswith(".pdf"):
        ann_name = os.path.splitext(ann_name)[0] + ".pdf"
    final_ann = os.path.join(base_dir, ann_name)

    rep_name = os.path.basename(report_path) if report_path else (os.path.splitext(ver.filename or "document")[0] + ".report.txt")
    final_rep = os.path.join(base_dir, rep_name)

    # перенос/копия результатов анализа
    try:
        if ann_pdf_path and os.path.abspath(ann_pdf_path) != os.path.abspath(final_ann):
            shutil.move(ann_pdf_path, final_ann)
        elif ann_pdf_path and os.path.exists(ann_pdf_path) and not os.path.exists(final_ann):
            shutil.copyfile(ann_pdf_path, final_ann)
    except Exception:
        if ann_pdf_path and os.path.exists(ann_pdf_path):
            shutil.copyfile(ann_pdf_path, final_ann)

    try:
        if report_path and os.path.abspath(report_path) != os.path.abspath(final_rep):
            shutil.move(report_path, final_rep)
        elif report_path and os.path.exists(report_path) and not os.path.exists(final_rep):
            shutil.copyfile(report_path, final_rep)
    except Exception:
        if report_path and os.path.exists(report_path):
            shutil.copyfile(report_path, final_rep)

    ver.ann_pdf_path = final_ann
    ver.report_path = final_rep
    ver.analysis_completed_at = datetime.utcnow()
    db.commit(); db.refresh(ver)

    # синхронизируем указатели в Document (необязательно, но удобно)
    doc.ann_pdf_path = ver.ann_pdf_path
    doc.description = ver.report_path
    db.commit()

    # ---- парсим текущий отчёт ----
    error_counts, total_violations, occ_map = {}, 0, {}
    if ver.report_path and os.path.exists(ver.report_path):
        with open(ver.report_path, "r", encoding="utf-8") as f:
            rc = f.read()
        parsed = parse_report(rc, doc_id=doc.id)
        error_counts = parsed.get("error_counts", {}) or {}
        total_violations = int(parsed.get("total_violations", 0) or 0)
        for occ in parsed.get("occurrences", []) or []:
            oid = occ.get("id"); pt = occ.get("point")
            if oid and pt:
                occ_map[oid] = {"point": pt, "description": occ.get("description")}

    # point -> [occ_ids] текущей версии (для красивого тега [occ:...] при фолбэке по критерию)
    point_to_occs = {}
    for oid, meta in occ_map.items():
        point_to_occs.setdefault(meta["point"], []).append(oid)

    def _extract_occ_id(comment: str | None) -> str | None:
        if not comment:
            return None
        m = re.search(r"\[occ:([0-9a-fA-F]{6,64})\]", comment)
        return m.group(1) if m else None

    # ---- developer fixed: текущая версия ----
    dev_current = db.query(Decision).filter(
        Decision.version_id == ver.id,
        Decision.author_role == "developer",
        Decision.status == "fixed"
    ).all()

    # ---- developer fixed: исторические версии ----
    all_versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).all()
    prev_ids = [v.id for v in all_versions if v.id != ver.id]
    dev_history = []
    if prev_ids:
        dev_history = db.query(Decision).filter(
            Decision.version_id.in_(prev_ids),
            Decision.author_role == "developer",
            Decision.status == "fixed"
        ).all()

    # ---- уже созданные system rejected в текущей версии (не дублировать) ----
    existing_rej_keys = set()
    sys_rej = db.query(Decision).filter(
        Decision.version_id == ver.id,
        Decision.author_role == "norm_controller",
        Decision.status == "rejected"
    ).all()
    for r in sys_rej:
        occ = _extract_occ_id(r.comment or "")
        if occ:
            existing_rej_keys.add(("occ", occ))
        elif r.error_point:
            existing_rej_keys.add(("pt", r.error_point.strip()))

    # ---- восстановление error_point для старых fixed по отчёту той версии ----
    version_occ_cache = {}
    def _restore_point_for_fixed(dec: Decision) -> str | None:
        ep = (dec.error_point or "").strip()
        if ep:
            return ep
        occ_prev = _extract_occ_id(dec.comment or "")
        if not occ_prev:
            return None
        if dec.version_id not in version_occ_cache:
            vprev = db.query(DocumentVersion).filter(DocumentVersion.id == dec.version_id).first()
            mp = {}
            if vprev and vprev.report_path and os.path.exists(vprev.report_path):
                with open(vprev.report_path, "r", encoding="utf-8") as f2:
                    rc2 = f2.read()
                parsed_prev = parse_report(rc2, doc_id=doc.id)
                for o in parsed_prev.get("occurrences", []) or []:
                    if o.get("id") and o.get("point"):
                        mp[o["id"]] = o["point"]
            version_occ_cache[dec.version_id] = mp
        return version_occ_cache[dec.version_id].get(occ_prev)

    # ---- главная проверка: текущие + исторические fixed против текущего отчёта ----
    all_fixed = list(dev_current) + list(dev_history)

    for dfix in all_fixed:
        occ_prev = _extract_occ_id(dfix.comment or "")
        point_prev = (dfix.error_point or "").strip() or _restore_point_for_fixed(dfix)

        # 1) если тот же occ_id присутствует в текущем отчёте
        if occ_prev and occ_prev in occ_map:
            key = ("occ", occ_prev)
            if key not in existing_rej_keys:
                add_decision(
                    db,
                    version_id=ver.id,
                    error_point=occ_map[occ_prev]["point"],
                    status="rejected",
                    author="Цифровой помощник конструктора",
                    author_role="norm_controller",
                    comment=f"[occ:{occ_prev}] Указанная как исправленная ошибка присутствует в отчёте текущей версии",
                    timestamp=datetime.utcnow(),
                )
                existing_rej_keys.add(key)
            continue

        # 2) фолбэк по критерию: если по этому пункту есть срабатывания в текущем отчёте
        if point_prev and int(error_counts.get(point_prev, 0) or 0) > 0:
            key = ("pt", point_prev)
            if key not in existing_rej_keys:
                occ_now = (point_to_occs.get(point_prev, []) or [None])[0]
                occ_tag = f"[occ:{occ_now}]" if occ_now else ""
                add_decision(
                    db,
                    version_id=ver.id,
                    error_point=point_prev,
                    status="rejected",
                    author="Цифровой помощник конструктора",
                    author_role="norm_controller",
                    comment=f"{occ_tag} Ранее отмеченный как исправленный пункт снова содержит нарушения",
                    timestamp=datetime.utcnow(),
                )
                existing_rej_keys.add(key)

    # (опционально) дозаполним error_point у старых fixed, если смогли восстановить
    for dfix in dev_history:
        if not (dfix.error_point or "").strip():
            ep = _restore_point_for_fixed(dfix)
            if ep:
                dfix.error_point = ep

    db.commit()

    # ---- финальный вердикт по текущей версии/документу ----
    if total_violations == 0:
        set_verdict(db, ver.id, status="approved", comment="Все замечания устранены",
                    author_name="Цифровой помощник конструктора", author_role="norm_controller")
    else:
        set_verdict(db, ver.id, status="rejected", comment="Остались нарушения",
                    author_name="Цифровой помощник конструктора", author_role="norm_controller")

    return ver

# НОВОЕ: фиксируем, кто поставил статус
def set_verdict(db: Session, version_id: int, status: str, comment: str | None = None,
                author_name: str | None = None, author_role: str | None = None):
    ver = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
    if not ver:
        return None
    ver.verdict_status = status
    ver.verdict_comment = comment
    if author_name:
        ver.verdict_author_name = author_name
    if author_role:
        ver.verdict_author_role = author_role
    doc = db.query(Document).filter(Document.id == ver.document_id).first()
    if doc:
        doc.status = status
    db.commit(); db.refresh(ver)
    return ver

def add_decision(db: Session, version_id: int, error_point: str, status: str,
                 author: str, author_role: str, comment: str, timestamp: datetime):
    # author — уже ФИО или "Цифровой помощник конструктора"
    dec = Decision(
        version_id=version_id,
        error_point=error_point,
        status=status,
        author=author,
        author_role=author_role,
        comment=comment,
        timestamp=timestamp
    )
    db.add(dec); db.commit(); db.refresh(dec)
    return dec

def list_decisions_for_version(db: Session, version_id: int):
    return db.query(Decision).filter(Decision.version_id == version_id).all()  # строго == !

def list_versions_for_document(db: Session, document_id: int):
    from .models import DocumentVersion
    # последние сначала (бОльший version_number)
    return db.query(DocumentVersion) \
             .filter(DocumentVersion.document_id == document_id) \
             .order_by(DocumentVersion.version_number.desc(), DocumentVersion.upload_date.desc()) \
             .all()

def list_all_documents(db: Session):
    from .models import Document
    return db.query(Document).all()

def get_documents_for_user(db: Session, user_id: int):
    return db.query(Document).filter(Document.user_id == user_id).all()

def get_document(db: Session, doc_id: int):
    return db.query(Document).filter(Document.id == doc_id).first()

def get_version(db: Session, version_id: int):
    return db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()

# --- Back-compat для analysis (ВАЖНО: поправить авторов) ---
def update_document_analysis(db: Session, doc_id: int, ann_pdf_path: str, description: str):
    """
    Обновление атрибутов версии после анализа + авто-вердикт и авто-отказы.
    Дополнительно:
      - учитываем все ранее заявленные developer 'fixed' по документу (регрессии);
      - помечаем 'новые найденные ошибки', если текущие срабатывания не связаны с прошлыми fixed.
    """
    import os, re, shutil
    from datetime import datetime as datetime
    from .parse_report import parse_report
    from .models import Decision, Document, DocumentVersion  # локальные модели

    # ---------- helpers ----------
    def _extract_occ_id(comment: str | None) -> str | None:
        if not comment:
            return None
        m = re.search(r"\[occ:([0-9a-fA-F]{6,64})\]", comment)
        return m.group(1) if m else None

    def _map_occ_to_point_from_version(vobj: DocumentVersion) -> dict:
        """Построить карту occ_id -> point из отчёта конкретной версии."""
        mp = {}
        rp = getattr(vobj, "report_path", None)
        if rp and os.path.exists(rp):
            with open(rp, "r", encoding="utf-8") as f2:
                rc2 = f2.read()
            parsed_prev = parse_report(rc2, doc_id=doc_id)
            for o in parsed_prev.get("occurrences", []) or []:
                if o.get("id") and o.get("point"):
                    mp[o["id"]] = o["point"]
        return mp

    # ---------- документ и целевая версия ----------
    doc = db.query(Document).filter(Document.id == doc_id).first()
    if not doc:
        return None

    versions = list_versions_for_document(db, doc_id)  # ожидается сортировка по version_number desc
    if not versions:
        return doc

    # выбираем «целевую» версию: первую без report/ann или, если все заполнены — последнюю (самую новую)
    target = next((v for v in versions if not v.report_path or not v.ann_pdf_path), versions[0])

    # ---------- гарантируем правильные конечные пути в v{version_number} ----------
    base_dir = os.path.join("data", "original", str(doc.id), f"v{target.version_number}")
    os.makedirs(base_dir, exist_ok=True)

    # финальное имя аннотированного PDF: <basename>.annotated.pdf (по имени версии)
    ann_name = os.path.splitext(target.filename or "document")[0] + ".annotated.pdf"
    final_ann = os.path.join(base_dir, ann_name)

    # финальное имя отчёта: <basename>.report.txt
    report_input_path = description  # исходный путь к отчёту пришёл в параметре description
    rep_name = os.path.splitext(target.filename or "document")[0] + ".report.txt"
    final_rep = os.path.join(base_dir, rep_name)

    # перенос/копирование результатов анализа в правильную папку
    try:
        if ann_pdf_path and os.path.abspath(ann_pdf_path) != os.path.abspath(final_ann):
            shutil.move(ann_pdf_path, final_ann)
        elif ann_pdf_path and os.path.exists(ann_pdf_path) and not os.path.exists(final_ann):
            shutil.copyfile(ann_pdf_path, final_ann)
    except Exception:
        if ann_pdf_path and os.path.exists(ann_pdf_path):
            shutil.copyfile(ann_pdf_path, final_ann)

    try:
        if report_input_path and os.path.abspath(report_input_path) != os.path.abspath(final_rep):
            shutil.move(report_input_path, final_rep)
        elif report_input_path and os.path.exists(report_input_path) and not os.path.exists(final_rep):
            shutil.copyfile(report_input_path, final_rep)
    except Exception:
        if report_input_path and os.path.exists(report_input_path):
            shutil.copyfile(report_input_path, final_rep)

    # фиксируем пути и момент завершения анализа
    target.ann_pdf_path = final_ann
    target.report_path = final_rep
    target.analysis_completed_at = datetime.utcnow()
    db.commit(); db.refresh(target)

    # (поддержка старых мест, где брали пути из Document)
    doc.ann_pdf_path = target.ann_pdf_path
    doc.description = target.report_path
    db.commit()

    # ---------- парсинг отчёта текущей версии ----------
    error_counts, total_violations, occ_map = {}, 0, {}
    if target.report_path and os.path.exists(target.report_path):
        with open(target.report_path, "r", encoding="utf-8") as f:
            rc = f.read()
        parsed = parse_report(rc, doc_id=doc_id)
        error_counts = parsed.get("error_counts", {}) or {}
        total_violations = int(parsed.get("total_violations", 0) or 0)
        for occ in parsed.get("occurrences", []) or []:
            if occ.get("id") and occ.get("point"):
                occ_map[occ["id"]] = {"point": occ["point"], "description": occ.get("description")}

    # карта point -> список текущих occ_id (нужно для тега [occ:...] в фолбэке по критерию)
    point_to_occs = {}
    for oid, meta in occ_map.items():
        point_to_occs.setdefault(meta["point"], []).append(oid)

    # ---------- developer fixed: текущая и исторические ----------
    dev_claims_current = db.query(Decision).filter(
        Decision.version_id == target.id,
        Decision.author_role == "developer",
        Decision.status == "fixed",
    ).all()

    # все версии этого документа (для восстановления по отчётам предыдущих версий)
    all_versions = db.query(DocumentVersion).filter(DocumentVersion.document_id == doc.id).all()
    prev_ids = [v.id for v in all_versions if v.id != target.id]

    dev_claims_history = db.query(Decision).filter(
        Decision.version_id.in_(prev_ids),
        Decision.author_role == "developer",
        Decision.status == "fixed",
    ).all() if prev_ids else []

    # уже созданные system rejected в текущей версии (не дублировать)
    existing_rej_keys = set()
    sys_rej = db.query(Decision).filter(
        Decision.version_id == target.id,
        Decision.author_role == "norm_controller",
        Decision.status == "rejected",
    ).all()
    for r in sys_rej:
        occ = _extract_occ_id(r.comment or "")
        if occ:
            existing_rej_keys.add(("occ", occ))
        elif r.error_point:
            existing_rej_keys.add(("pt", (r.error_point or "").strip()))

    # восстановление error_point для старых fixed
    version_occ_cache = {}  # {version_id: {occ_id: point}}

    def _restore_point_for_fixed(dec: Decision) -> str | None:
        ep = (dec.error_point or "").strip()
        if ep:
            return ep
        occ_prev = _extract_occ_id(dec.comment or "")
        if not occ_prev:
            return None

        # 1) отчёт той версии, где стоит решение
        if dec.version_id not in version_occ_cache:
            vprev = next((v for v in all_versions if v.id == dec.version_id), None)
            version_occ_cache[dec.version_id] = _map_occ_to_point_from_version(vprev) if vprev else {}
        ep = version_occ_cache[dec.version_id].get(occ_prev)
        if ep:
            return ep

        # 2) другие версии документа (частый кейс: dev сослался на старый occ_id)
        for vobj in all_versions:
            if vobj.id == dec.version_id:
                continue
            if vobj.id not in version_occ_cache:
                version_occ_cache[vobj.id] = _map_occ_to_point_from_version(vobj)
            ep = version_occ_cache[vobj.id].get(occ_prev)
            if ep:
                return ep

        return None

    # ---------- регрессии: текущие + исторические fixed против текущего отчёта ----------
    all_fixed = list(dev_claims_current) + list(dev_claims_history)

    for dfix in all_fixed:
        occ_prev = _extract_occ_id(dfix.comment or "")
        point_prev = _restore_point_for_fixed(dfix)

        # 1) совпал тот же occ_id в текущем отчёте
        if occ_prev and occ_prev in occ_map:
            key = ("occ", occ_prev)
            if key not in existing_rej_keys:
                add_decision(
                    db,
                    version_id=target.id,
                    error_point=occ_map[occ_prev]["point"],
                    status="rejected",
                    author="Цифровой помощник конструктора",
                    author_role="norm_controller",
                    comment=f"[occ:{occ_prev}] Регресс: ранее отмеченная как исправленная ошибка снова обнаружена",
                    timestamp=datetime.utcnow(),
                )
                existing_rej_keys.add(key)
            continue

        # 2) фолбэк по критерию (ошибка того же пункта есть снова)
        if point_prev and int(error_counts.get(point_prev, 0) or 0) > 0:
            key = ("pt", point_prev)
            if key not in existing_rej_keys:
                occ_now = (point_to_occs.get(point_prev, []) or [None])[0]
                occ_tag = f"[occ:{occ_now}]" if occ_now else ""
                add_decision(
                    db,
                    version_id=target.id,
                    error_point=point_prev,
                    status="rejected",
                    author="Цифровой помощник конструктора",
                    author_role="norm_controller",
                    comment=f"{occ_tag} Регресс: пункт ранее отмечался как исправленный, но нарушения снова обнаружены",
                    timestamp=datetime.utcnow(),
                )
                existing_rej_keys.add(key)

    # дозаполняем error_point у старых fixed, если смогли восстановить
    for dfix in dev_claims_history:
        if not (dfix.error_point or "").strip():
            ep = _restore_point_for_fixed(dfix)
            if ep:
                dfix.error_point = ep
    db.commit()

    # ---------- базовая проверка текущих заявок (как и раньше) ----------
    for d in dev_claims_current:
        occ = _extract_occ_id(d.comment or "")
        if occ:
            if occ in occ_map:
                key = ("occ", occ)
                if key not in existing_rej_keys:
                    add_decision(
                        db,
                        version_id=target.id,
                        error_point=occ_map[occ]["point"],
                        status="rejected",
                        author="Цифровой помощник конструктора",
                        author_role="norm_controller",
                        comment=f"[occ:{occ}] Указанная конкретная ошибка осталась в отчёте",
                        timestamp=datetime.utcnow(),
                    )
                    existing_rej_keys.add(key)
        elif d.error_point:
            still = int(error_counts.get(d.error_point, 0) or 0) > 0
            if still:
                key = ("pt", d.error_point)
                if key not in existing_rej_keys:
                    add_decision(
                        db,
                        version_id=target.id,
                        error_point=d.error_point,
                        status="rejected",
                        author="Цифровой помощник конструктора",
                        author_role="norm_controller",
                        comment="Пункт отмечен как исправленный, но нарушения по нему остались в отчёте",
                        timestamp=datetime.utcnow(),
                    )
                    existing_rej_keys.add(key)

    # ---------- НОВОЕ: помечаем «новые найденные ошибки» (только если это не первая версия) ----------
    if len(all_versions) > 1:
        fixed_occ_ids = set()
        fixed_points = set()
        for dfix in all_fixed:
            occ_prev = _extract_occ_id(dfix.comment or "")
            if occ_prev:
                fixed_occ_ids.add(occ_prev)
            ep_prev = (dfix.error_point or "").strip() or _restore_point_for_fixed(dfix)
            if ep_prev:
                fixed_points.add(ep_prev)

        for oid, meta in occ_map.items():
            # если уже создали system-rejected по этому occ/пункту — пропускаем
            if ("occ", oid) in existing_rej_keys or ("pt", meta["point"]) in existing_rej_keys:
                continue

            # если это явно НЕ из ранее заявленных фикс-обещаний — помечаем как новую найденную ошибку
            if (oid not in fixed_occ_ids) and (meta["point"] not in fixed_points):
                add_decision(
                    db,
                    version_id=target.id,
                    error_point=meta["point"],
                    status="rejected",
                    author="Цифровой помощник конструктора",
                    author_role="norm_controller",
                    comment=f"[occ:{oid}] Найдено новое нарушение в текущем отчёте",
                    timestamp=datetime.utcnow(),
                )
                existing_rej_keys.add(("occ", oid))

    db.commit()

    # ---------- финальный вердикт по текущей версии ----------
    if total_violations == 0:
        set_verdict(
            db, target.id,
            status="approved",
            comment="Все замечания устранены",
            author_name="Цифровой помощник конструктора",
            author_role="norm_controller",
        )
    else:
        set_verdict(
            db, target.id,
            status="rejected",
            comment="Остались нарушения",
            author_name="Цифровой помощник конструктора",
            author_role="norm_controller",
        )

    return doc


def update_decision(db: Session, decision_id: int, status: str = None, author: str = None, 
                   author_role: str = None, comment: str = None):
    """
    Обновляет существующее решение (Decision).
    Если передан author, то обновляется автор и роль.
    Если передан comment, то комментарий обновляется (предыдущий затирается).
    """
    from .models import Decision
    decision = db.query(Decision).filter(Decision.id == decision_id).first()
    if not decision:
        return None
    
    if status is not None:
        decision.status = status
    if author is not None:
        decision.author = author
    if author_role is not None:
        decision.author_role = author_role
    if comment is not None:
        decision.comment = comment
    
    db.commit()
    db.refresh(decision)
    return decision


def get_decision_by_id(db: Session, decision_id: int):
    """Получает решение по ID"""
    from .models import Decision
    return db.query(Decision).filter(Decision.id == decision_id).first()


def get_decision_by_occ_id(db: Session, version_id: int, error_point: str, occ_id: str):
    """
    Находит решение по version_id, error_point и occ_id (через поле comment).
    Ищет в comment строку вида [occ:...] или (occ:...)
    """
    from .models import Decision
    import re
    
    decisions = db.query(Decision).filter(
        Decision.version_id == version_id,
        Decision.error_point == error_point
    ).all()
    
    for decision in decisions:
        comment = decision.comment or ""
        # Ищем [occ:...] или (occ:...) в комментарии
        match = re.search(r"\[(?:occ|occ_id):([0-9a-fA-F]{6,64})\]|\((?:occ|occ_id):([0-9a-fA-F]{6,64})\)", comment)
        if match:
            found_occ_id = match.group(1) or match.group(2)
            if found_occ_id == occ_id:
                return decision
    
    return None


def get_decisions_by_version_and_point(db: Session, version_id: int, error_point: str):
    """
    Получает все решения для определенной версии и критерия
    """
    from .models import Decision
    return db.query(Decision).filter(
        Decision.version_id == version_id,
        Decision.error_point == error_point
    ).all()


