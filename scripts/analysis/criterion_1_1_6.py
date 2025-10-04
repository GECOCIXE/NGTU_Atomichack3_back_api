
import re
import json
import math
from pathlib import Path
import fitz  # PyMuPDF

# --- Настройка распознавания размерных / сносок ---
DIMENSION_PATTERNS = [
    r"[⌀ØO]\s*\d+(?:[.,]\d+)?",
    r"\bR\s*\d+(?:[.,]\d+)?",
    r"\bM\s*\d+(?:[×xX]\d+(?:[.,]\d+)?)?",
    r"\d+\s*±\s*\d+(?:[.,]\d+)?",
    r"[+−\-]\s*\d+(?:[.,]\d+)?",
    r"\b\d+(?:[.,]\d+)?\s*мм\b",
    r"\b\d+(?:[.,]\d+)?\s*mm\b",
    r"\b\d+(?:[.,]\d+)?\s*°\b",
    r"\b\d+(?:[.,]\d+)?\b"
]

def is_dimension_note(text: str) -> bool:
    t = (text or "").strip()
    if len(t) > 60 or len(t) < 1:
        return False
    if not re.search(r"\d", t):
        return False
    for pat in DIMENSION_PATTERNS:
        if re.search(pat, t, flags=re.IGNORECASE):
            return True
    return False

# --- Работа с углами ---
def normalize_tilt(angle_deg: float) -> float:
    """Наклон относительно горизонтали (0..90], 0 и 180 считаются 0."""
    a = angle_deg % 180.0
    if a > 90.0:
        a = 180.0 - a
    return abs(a)

def angle_from_matrix(m):
    """Угол базовой оси X из матрицы (a,b,c,d, e,f)."""
    try:
        a = float(m[0]); b = float(m[1])
        return math.degrees(math.atan2(b, a))
    except Exception:
        return None

def angle_from_dir(dir_vec):
    try:
        dx, dy = dir_vec
        return math.degrees(math.atan2(dy, dx))
    except Exception:
        return None

# --- Извлечение ---
def collect_from_rawdict(struct, sink, page_rot_deg, tag):
    for block in struct.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", []) or []
            if not spans:
                continue
            # базовый угол по линии (часто бесполезен при вложенных матрицах)
            line_angle = angle_from_dir(line.get("dir")) if "dir" in line else None
            for s in spans:
                text = (s.get("text") or "").strip()
                if not text:
                    continue
                x0, y0, x1, y1 = map(float, s.get("bbox", [0,0,0,0]))
                bbox = [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)]
                size = float(s.get("size", 0.0) or 0.0)

                # 1) пробуем взять угол из матрицы спана
                mat = s.get("matrix") or s.get("Matrix") or None  # PyMuPDF версии по-разному именуют
                span_angle = angle_from_matrix(mat) if mat else None

                # 2) fallback — угол линии
                angle = span_angle if span_angle is not None else line_angle

                # 3) ещё один fallback — если ни у спана, ни у линии нет угла, оставим 0
                if angle is None:
                    angle = 0.0

                # 4) нормализуем с учётом поворота страницы (если страница повернута)
                angle_corr = angle - float(page_rot_deg or 0.0)

                sink.append({
                    "kind": tag,
                    "text": text,
                    "bbox": bbox,
                    "size": round(size, 2) if size else None,
                    "raw_angle_deg": round(angle, 2),
                    "page_rot_deg": float(page_rot_deg or 0.0),
                    "angle_deg": round(angle_corr, 2),
                    "tilt_deg": round(normalize_tilt(angle_corr), 2),
                    "is_dimension": is_dimension_note(text),
                    "source": "span-matrix" if span_angle is not None else ("line-dir" if line_angle is not None else "fallback-0")
                })

def collect_annotations(page, sink):
    try:
        a = page.first_annot
        while a:
            a_type = a.type[1]
            text = (a.info.get("content") or "").strip()
            r = a.rect
            bbox = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
            rotation = float(a.rotation or 0.0)
            sink.append({
                "kind": f"annot:{a_type}",
                "text": text,
                "bbox": bbox,
                "size": None,
                "raw_angle_deg": round(rotation, 2),
                "page_rot_deg": float(page.rotation or 0.0),
                "angle_deg": round(rotation - float(page.rotation or 0.0), 2),
                "tilt_deg": round(normalize_tilt(rotation - float(page.rotation or 0.0)), 2),
                "is_dimension": is_dimension_note(text),
                "source": "annotation"
            })
            a = a.next
    except Exception:
        pass

def collect_words(page, sink):
    # На случай, если ни rawdict, ни matrix не дали углов — используем слова (угол 0)
    try:
        words = page.get_text("words")
        from collections import defaultdict
        lines = defaultdict(list)
        for (x0, y0, x1, y1, wtext, b, l, wno) in words:
            lines[(b, l)].append((x0, y0, x1, y1, wtext))
        for (b, l), ws in lines.items():
            ws.sort(key=lambda t: t[0])
            text = " ".join(w[-1] for w in ws).strip()
            if not text:
                continue
            x0 = min(w[0] for w in ws); y0 = min(w[1] for w in ws)
            x1 = max(w[2] for w in ws); y1 = max(w[3] for w in ws)
            sink.append({
                "kind": "text:words",
                "text": text,
                "bbox": [round(x0, 2), round(y0, 2), round(x1, 2), round(y1, 2)],
                "size": None,
                "raw_angle_deg": 0.0,
                "page_rot_deg": float(page.rotation or 0.0),
                "angle_deg": 0.0,
                "tilt_deg": 0.0,
                "is_dimension": is_dimension_note(text),
                "source": "words-fallback"
            })
    except Exception:
        pass

def extract_items(page, use_words_fallback=True):
    items = []
    page_rot = float(page.rotation or 0.0)

    # rawdict → с углом по матрице спанов
    try:
        rd = page.get_text("rawdict")
        collect_from_rawdict(rd, items, page_rot, "text:rawdict")
    except Exception:
        pass

    # textpage.extractDICT() — иногда содержит другую структуру
    try:
        tp = page.get_textpage()
        dd = tp.extractDICT()
        collect_from_rawdict(dd, items, page_rot, "text:dict")
    except Exception:
        pass

    # аннотации
    collect_annotations(page, items)

    # words fallback (без угла)
    if use_words_fallback:
        collect_words(page, items)

    # дедуп по (text,bbox,source)
    seen = set()
    uniq = []
    for it in items:
        key = (it["text"], tuple(it["bbox"]), it.get("source"))
        if key in seen:
            continue
        seen.add(key)
        uniq.append(it)

    # стабильная сортировка
    uniq.sort(key=lambda it: (it["bbox"][1], it["bbox"][0], it["kind"]))
    return uniq

def check(pdf_path: str, angle_threshold: float = 30.0, include_all_kinds=False, verbose=False):
    doc = fitz.open(pdf_path)
    report = {"pdf": str(pdf_path), "threshold_deg": angle_threshold, "pages": {}, "ok": True}
    try:
        for i, page in enumerate(doc, start=1):
            items = extract_items(page, use_words_fallback=True)
            candidates = [it for it in items if it["text"]] if include_all_kinds else [it for it in items if it["is_dimension"]]
            bad = [it for it in candidates if it["tilt_deg"] > angle_threshold]
            page_ok = len(bad) == 0
            if not page_ok:
                report["ok"] = False

            page_block = {
                "dimension_items": candidates,
                "violations": bad,
                "page_ok": page_ok
            }
            if verbose:
                # немножко статистики для отладки
                kinds = {}
                sources = {}
                for it in items:
                    kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
                    sources[it["source"]] = sources.get(it["source"], 0) + 1
                page_block["diagnostics"] = {
                    "counts_by_kind": kinds,
                    "counts_by_source": sources,
                    "total_items_seen": len(items)
                }
            report["pages"][i] = page_block
    finally:
        doc.close()
    return report

if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(description="Проверка сносок/размерных текстов на наклон > порога.")
    parser.add_argument("pdf", help="Путь к PDF")
    parser.add_argument("-t", "--threshold", type=float, default=30.0, help="Порог в градусах (default: 30.0)")
    parser.add_argument("-j", "--json", action="store_true", help="Вывести JSON-отчёт вместо true/false")
    parser.add_argument("--all", action="store_true", help="Проверять все тексты (не только размерные)")
    parser.add_argument("-v", "--verbose", action="store_true", help="Диагностика и счётчики")
    args = parser.parse_args()

    p = Path(args.pdf)
    if not p.exists():
        print(f"Файл не найден: {p}", file=sys.stderr)
        sys.exit(2)

    rep = check(str(p), angle_threshold=args.threshold, include_all_kinds=args.all, verbose=args.verbose)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        print("true" if rep["ok"] else "false")

