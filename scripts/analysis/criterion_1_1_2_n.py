import json
import re
from pathlib import Path
import fitz  # PyMuPDF

PT_PER_INCH = 72.0
MM_PER_INCH = 25.4
MM_PER_PT = MM_PER_INCH / PT_PER_INCH  # ~0.352777...
PT_PER_MM = PT_PER_INCH / MM_PER_INCH  # ~2.83464567

TARGET_WIDTH_MM = 185.0
TOL_MM = 2.0  # tolerance for "≈ 185 мм"
TOL_PT = TOL_MM * PT_PER_MM
ABOVE_GAP_TOL_PT = 1.5 * PT_PER_MM  # small gap tolerance (~1.5 mm)
COL_X_CLUSTER_PT = 14 * PT_PER_MM   # x0 distance to cluster lines into columns (~14 mm)
ALIGNMENT_MIN_OVERLAP_RATIO = 0.3   # overlap ratio to consider "aligned above title block"

TB_KEYWORDS = [
    "Масштаб", "Масса", "Лит", "Разраб", "Пров", "Т.контр", "Н.контр",
    "Утв", "Лист", "Листов", "Изм.", "№ докум", "Подп.", "Дата"
]


def _page_lines_with_bbox(page):
    pd = page.get_text("dict")
    out = []
    for block in pd.get("blocks", []):
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
            bbox = [min(x0s), min(y0s), max(x1s), max(y1s)]
            size = max(float(s.get("size", 0.0)) for s in spans)
            out.append({"text": text, "bbox": bbox, "size": size})
    out.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))
    return out


def _split_tt_and_field(lines):
    tt = []
    field = []
    for it in lines:
        t = it["text"]
        if re.match(r"^\s*\d+\s*[.)-]?\s+", t):
            tt.append(it)
        else:
            field.append(it)
    return tt, field


def _find_title_block_bbox(lines, page_rect):
    matches = []
    for it in lines:
        low = it["text"].lower().replace("ё", "е")
        if any(k.lower() in low for k in TB_KEYWORDS):
            matches.append(it)

    if len(matches) >= 2:
        x0 = min(it["bbox"][0] for it in matches)
        y0 = min(it["bbox"][1] for it in matches)
        x1 = max(it["bbox"][2] for it in matches)
        y1 = max(it["bbox"][3] for it in matches)
        return matches, fitz.Rect(x0, y0, x1, y1), "keywords"

    # Fallback: bottom-right zone text union
    w, h = page_rect.width, page_rect.height
    br_matches = [it for it in lines if it["bbox"][0] > 0.6 * w and it["bbox"][1] > 0.6 * h]
    if not br_matches:
        br_matches = [it for it in lines if it["bbox"][0] > 0.5 * w and it["bbox"][1] > 0.7 * h]
    if br_matches:
        x0 = min(it["bbox"][0] for it in br_matches)
        y0 = min(it["bbox"][1] for it in br_matches)
        x1 = max(it["bbox"][2] for it in br_matches)
        y1 = max(it["bbox"][3] for it in br_matches)
        return br_matches, fitz.Rect(x0, y0, x1, y1), "bottom-right"
    return [], fitz.Rect(0, page_rect.height - 50, page_rect.width, page_rect.height), "default-bottom-strip"


def _cluster_columns(tt_lines):
    if not tt_lines:
        return []
    lines_sorted = sorted(tt_lines, key=lambda it: it["bbox"][0], reverse=True)
    cols = [[lines_sorted[0]]]
    for it in lines_sorted[1:]:
        x0 = it["bbox"][0]
        col = cols[-1]
        mean_x0 = sum(e["bbox"][0] for e in col) / len(col)
        if abs(mean_x0 - x0) <= COL_X_CLUSTER_PT:
            col.append(it)
        else:
            cols.append([it])
    for col in cols:
        col.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))
    return cols


def _column_bbox(col):
    x0 = min(it["bbox"][0] for it in col)
    y0 = min(it["bbox"][1] for it in col)
    x1 = max(it["bbox"][2] for it in col)
    y1 = max(it["bbox"][3] for it in col)
    return fitz.Rect(x0, y0, x1, y1)


def _overlap_ratio(a, b):
    x0 = max(a.x0, b.x0)
    y0 = max(a.y0, b.y0)
    x1 = min(a.x1, b.x1)
    y1 = min(a.y1, b.y1)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter_w = x1 - x0
    base = max(min(a.width, b.width), 1e-6)
    return float(inter_w / base)


def check_tt_position_and_width(pdf_path: str) -> dict:
    doc = fitz.open(pdf_path)
    report = {"pages": {}, "ok": True}
    try:
        for pageno, page in enumerate(doc, start=1):
            page_rect = page.rect
            page_w_mm = page_rect.width * MM_PER_PT
            page_h_mm = page_rect.height * MM_PER_PT

            lines = _page_lines_with_bbox(page)
            tt_lines, field_lines = _split_tt_and_field(lines)

            tb_matches, tb_bbox, tb_method = _find_title_block_bbox(lines, page_rect)

            cols = _cluster_columns(tt_lines)
            cols_bboxes = [_column_bbox(c) for c in cols]

            page_info = {
                "page_size_mm": [round(page_w_mm, 2), round(page_h_mm, 2)],
                "title_block": {
                    "bbox_pt": [round(tb_bbox.x0, 2), round(tb_bbox.y0, 2), round(tb_bbox.x1, 2), round(tb_bbox.y1, 2)],
                    "detected_by": tb_method,
                    "keywords_found": [m["text"] for m in tb_matches[:10]],
                },
                "tt_columns": [],
                "placement": {},
            }

            if cols_bboxes:
                tt_union = fitz.Rect(
                    min(r.x0 for r in cols_bboxes),
                    min(r.y0 for r in cols_bboxes),
                    max(r.x1 for r in cols_bboxes),
                    max(r.y1 for r in cols_bboxes),
                )
            else:
                tt_union = fitz.Rect(0, 0, 0, 0)

            above_ok = False
            aligned_ok = False
            ALLOWANCE_MM = 10.0
            ALLOWANCE_PT = ALLOWANCE_MM * PT_PER_MM
            if cols_bboxes:
                above_ok = (tt_union.y1 <= tb_bbox.y0 + ALLOWANCE_PT)

            # Проверяем выравнивание правого столбца с основной надписью
            if cols_bboxes:
                rightmost_bbox = cols_bboxes[0]
                overlap = _overlap_ratio(rightmost_bbox, tb_bbox)
                aligned_ok = (overlap >= ALIGNMENT_MIN_OVERLAP_RATIO)

            widths_ok = True
            widths_details = []
            for idx, col_bbox in enumerate(cols_bboxes):
                width_mm = col_bbox.width * MM_PER_PT
                le_ok = width_mm <= (TARGET_WIDTH_MM + TOL_MM)
                approx_ok = True if idx == 0 else abs(width_mm - TARGET_WIDTH_MM) <= TOL_MM
                col_ok = le_ok and approx_ok
                widths_ok = widths_ok and col_ok
                widths_details.append({
                    "index": idx,
                    "bbox_pt": [round(col_bbox.x0, 2), round(col_bbox.y0, 2), round(col_bbox.x1, 2), round(col_bbox.y1, 2)],
                    "width_mm": round(width_mm, 2),
                    "le_185mm_ok": bool(le_ok),
                    "approx_185mm_ok": bool(approx_ok),
                    "column_ok": bool(col_ok),
                })

            page_ok = bool(above_ok and aligned_ok and widths_ok)
            if not page_ok:
                report["ok"] = False

            page_info["tt_columns"] = widths_details
            page_info["placement"] = {
                "tt_found": bool(cols_bboxes),
                "above_title_block_ok": bool(above_ok),
                "aligned_above_title_block_ok": bool(aligned_ok),
                "widths_ok": bool(widths_ok),
                "page_ok": bool(page_ok),
            }
            report["pages"][pageno] = page_info
    finally:
        doc.close()
    return report


# >>> НОВОЕ: импортируемая обёртка, возвращающая JSON-строку <<<
def run_check(pdf_path: str) -> dict:
    """
    Запускает проверку и возвращает JSON-строку ровно в том виде,
    как она раньше печаталась в stdout.
    """
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"Файл не найден: {pdf_path}")
    res = check_tt_position_and_width(pdf_path)
    return res


# (опц.) отдельная функция аннотирования, чтобы можно было вызвать из кода
def annotate_pdf(pdf_path: str, report: dict, out_path: str) -> None:
    doc = fitz.open(pdf_path)
    try:
        for pageno, page in enumerate(doc, start=1):
            if pageno not in report["pages"]:
                continue
            info = report["pages"][pageno]
            tb = info["title_block"]["bbox_pt"]
            tb_rect = fitz.Rect(*tb)
            ok = info["placement"]["page_ok"]

            page.draw_rect(tb_rect, color=(0, 0, 1), width=1)  # blue
            page.insert_text((tb_rect.x0, max(0, tb_rect.y0 - 10)),
                             "Основная надпись", fontsize=8, color=(0, 0, 1))

            for col in info["tt_columns"]:
                r = fitz.Rect(*col["bbox_pt"])
                col_ok = col["column_ok"]
                color = (0, 0.6, 0) if col_ok else (1, 0, 0)
                page.draw_rect(r, color=color, width=1)
                label = (f"ТТ кол.{col['index']} = {col['width_mm']} мм  "
                         f"<=185: {'OK' if col['le_185mm_ok'] else 'FAIL'}; "
                         f"≈185: {'OK' if col['approx_185mm_ok'] else 'FAIL'}")
                page.insert_text((r.x0, max(0, r.y0 - 10)), label, fontsize=8, color=color)

            banner = (f"Критерий 1.1.2 — {'OK' if ok else 'FAIL'} | "
                      f"Расположение над ОН: {'OK' if info['placement']['above_title_block_ok'] else 'FAIL'}; "
                      f"Выравнивание над ОН: {'OK' if info['placement']['aligned_above_title_block_ok'] else 'FAIL'}; "
                      f"Ширина столбцов: {'OK' if info['placement']['widths_ok'] else 'FAIL'}")
            page.insert_text((20, 20), banner, fontsize=10, color=(0, 0, 0))
    finally:
        doc.save(out_path)
        doc.close()


def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Критерий 1.1.2 — проверка расположения ТТ над основной надписью и ширины 185 мм."
    )
    parser.add_argument("pdf", help="Путь к PDF файлу чертежа")
    parser.add_argument("-o", "--output", help="Путь для сохранения JSON (по умолчанию — stdout)")
    parser.add_argument("--annotate", help="Сохранить аннотированный PDF (path)")
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        raise SystemExit(f"Файл не найден: {args.pdf}")

    # используем ту же обёртку, чтобы поведение CLI и импорт одинаково формировали JSON
    out_json = run_check(args.pdf)

    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
    else:
        print(out_json)

    if args.annotate:
        report = json.loads(out_json)
        annotate_pdf(args.pdf, report, args.annotate)


if __name__ == "__main__":
    main()

