# scripts/parse_report.py
import hashlib

def _natkey(point: str):
    parts = []
    for p in point.split("."):
        try:
            parts.append(int(p))
        except Exception:
            parts.append(p)
    return tuple(parts)

def _occ_id(point: str, desc: str) -> str:
    h = hashlib.sha1(f"{point}|{desc}".encode("utf-8")).hexdigest()
    return h[:12]

def parse_report(report_content: str, doc_id: int = None):
    """
    Возвращает:
      - error_points: список по КАЖДОМУ срабатыванию (по одному элементу на ошибку)
          { point, description, pdf_url, occ_id, error_num }
      - error_counts: dict(point -> count)
      - total_violations: общее число срабатываний
      - full_report: исходный текст
      - occurrences: [{id, point, description}] — вспомогательное (можно игнорировать на фронте)
    Формат входного отчёта прежний:
        [# ...]
        Пункты: 1.1, 1.1.1
        - (описание/контекст; часто содержит страницу/лист)
        - ...
    Также обрабатывает глобальные ошибки формата:
        [GLOBAL] <rule>: <comment>
    """
    lines = report_content.splitlines()

    current_points = []
    current_error_num = None  # Номер текущей ошибки
    occurrences = []   # [(point, desc, occ_id, error_num)]
    error_counts = {}

    for raw in lines:
        line = raw.rstrip("\n")

        # Обработка глобальных ошибок формата [GLOBAL] <rule>: <comment>
        import re
        global_match = re.match(r"\[GLOBAL\]\s+([\d.]+):\s*(.*)", line)
        if global_match:
            rule = global_match.group(1).strip()
            comment = global_match.group(2).strip()
            # Добавляем глобальную ошибку как обычную ошибку
            oid = _occ_id(rule, comment)
            occurrences.append((rule, comment, oid, None))  # нет номера ошибки для глобальных
            error_counts[rule] = error_counts.get(rule, 0) + 1
            continue

        if line.startswith("[#"):
            current_points = []
            # Извлекаем номер ошибки из формата [#013]
            match = re.search(r"\[#(\d+)\]", line)
            current_error_num = match.group(1) if match else None

        elif line.strip().startswith("Пункты:"):
            _, rhs = line.split("Пункты:", 1)
            pts = [p.strip() for p in rhs.split(",") if p.strip()]
            current_points = pts

        elif line.strip().startswith("-"):
            if not current_points:
                continue
            desc = line.strip()
            if desc.startswith("-"):
                desc = desc[1:].strip()
            if desc.startswith("(") and desc.endswith(")"):
                desc = desc[1:-1].strip()

            for pt in current_points:
                oid = _occ_id(pt, desc)
                occurrences.append((pt, desc, oid, current_error_num))
                error_counts[pt] = error_counts.get(pt, 0) + 1

    # сортируем: по критерию «натурально»
    occurrences.sort(key=lambda x: (_natkey(x[0]), ))

    error_points = []
    occ_list = []
    for _, (pt, desc, oid, error_num) in enumerate(occurrences):
        pdf_url = f"/download_annotated/{doc_id}?point={pt}&occ={oid}" if doc_id else ""
        error_points.append({
            "point": pt,
            "description": desc,
            "pdf_url": pdf_url,
            "occ_id": oid,              # <-- НОВОЕ в /result
            "error_num": error_num,     # <-- НОВОЕ: номер ошибки
        })
        occ_list.append({"id": oid, "point": pt, "description": desc})

    total_violations = len(occurrences)

    return {
        "error_points": error_points,
        "error_counts": error_counts,
        "total_violations": total_violations,
        "full_report": report_content,
        "occurrences": occ_list,
    }
