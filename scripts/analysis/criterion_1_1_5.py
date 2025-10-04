

import re
import json
from pathlib import Path
import math
import fitz  # PyMuPDF


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


def angle_from_dir(dir_vec) -> float:
    dx, dy = dir_vec
    ang = math.degrees(math.atan2(dy, dx))
    return ang


def tilt_from_horizontal(angle_deg: float) -> float:
    a = angle_deg % 180.0
    if a > 90.0:
        a = 180.0 - a
    return abs(a)


def _collect_from_dict(struct, sink, tag):
    for block in struct.get("blocks", []):
        if block.get("type", 0) != 0:
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            text = "".join((s.get("text") or "") for s in spans).strip()
            if not text:
                continue
            x0s = [float(s["bbox"][0]) for s in spans]
            y0s = [float(s["bbox"][1]) for s in spans]
            x1s = [float(s["bbox"][2]) for s in spans]
            y1s = [float(s["bbox"][3]) for s in spans]
            bbox = [round(min(x0s), 2), round(min(y0s), 2), round(max(x1s), 2), round(max(y1s), 2)]
            size = max(float(s.get("size", 0)) for s in spans)
            dir_vec = line.get("dir", (1.0, 0.0))
            angle = angle_from_dir(dir_vec)
            sink.append({
                "kind": tag,
                "text": text,
                "bbox": bbox,
                "size": round(size, 2),
                "angle_deg": round(angle, 2),
                "tilt_deg": round(tilt_from_horizontal(angle), 2),
                "is_dimension": is_dimension_note(text),
            })


def extract_items(page):
    items = []
    # 1) rawdict
    try:
        rd = page.get_text("rawdict")
        _collect_from_dict(rd, items, "text:rawdict")
    except Exception:
        pass
    # 2) dict via textpage
    try:
        tp = page.get_textpage()
        dd = tp.extractDICT()
        _collect_from_dict(dd, items, "text:dict")
    except Exception:
        pass
    # 3) words fallback (no angle; we'll treat as horizontal)
    try:
        words = page.get_text("words")  # list of (x0,y0,x1,y1,"text", block, line, word_no)
        # group by line id (block,line)
        from collections import defaultdict
        lines = defaultdict(list)
        for (x0, y0, x1, y1, wtext, b, l, wno) in words:
            lines[(b,l)].append((x0, y0, x1, y1, wtext))
        for (b,l), ws in lines.items():
            ws.sort(key=lambda t: t[0])
            text = " ".join(w[-1] for w in ws).strip()
            if not text:
                continue
            x0 = min(w[0] for w in ws); y0 = min(w[1] for w in ws)
            x1 = max(w[2] for w in ws); y1 = max(w[3] for w in ws)
            bbox = [round(x0,2), round(y0,2), round(x1,2), round(y1,2)]
            items.append({
                "kind": "text:words",
                "text": text,
                "bbox": bbox,
                "size": None,
                "angle_deg": 0.0,
                "tilt_deg": 0.0,
                "is_dimension": is_dimension_note(text),
            })
    except Exception:
        pass
    # 4) annotations
    try:
        annot = page.first_annot
        while annot:
            a_type = annot.type[1]
            try:
                text = (annot.info.get("content") or "").strip()
            except Exception:
                text = ""
            rotation = 0.0
            try:
                rotation = float(annot.rotation or 0.0)
            except Exception:
                rotation = 0.0
            r = annot.rect
            bbox = [round(r.x0, 2), round(r.y0, 2), round(r.x1, 2), round(r.y1, 2)]
            items.append({
                "kind": f"annot:{a_type}",
                "text": text,
                "bbox": bbox,
                "size": None,
                "angle_deg": round(rotation, 2),
                "tilt_deg": round(tilt_from_horizontal(rotation), 2),
                "is_dimension": is_dimension_note(text),
            })
            annot = annot.next
    except Exception:
        pass

    # deduplicate by (text,bbox)
    seen = set()
    uniq = []
    for it in items:
        key = (it["text"], tuple(it["bbox"]))
        if key in seen: 
            continue
        seen.add(key)
        uniq.append(it)
    uniq.sort(key=lambda it: (it["bbox"][1], it["bbox"][0], it["kind"]))
    return uniq


def check(pdf_path: str, angle_threshold: float = 30.0, include_all_kinds=False, verbose=False):
    doc = fitz.open(pdf_path)
    report = {"pdf": str(pdf_path), "threshold_deg": angle_threshold, "pages": {}, "ok": True}
    try:
        for i, page in enumerate(doc, start=1):
            items = extract_items(page)
            if include_all_kinds:
                candidates = [it for it in items if it["text"]]
            else:
                candidates = [it for it in items if it["is_dimension"]]
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
                kinds = {}
                for it in items:
                    k = it["kind"]
                    kinds[k] = kinds.get(k, 0) + 1
                page_block["diagnostics"] = {
                    "counts_by_kind": kinds,
                    "total_items_seen": len(items)
                }
            report["pages"][i] = page_block
    finally:
        doc.close()
    return report


if __name__ == "__main__":
    import argparse, sys
    parser = argparse.ArgumentParser(description="Мульти-метод извлечения текстов и аннотаций с оценкой наклона.")
    parser.add_argument("pdf", help="Путь к PDF файлу")
    parser.add_argument("-t", "--threshold", type=float, default=30.0)
    parser.add_argument("-j", "--json", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
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

