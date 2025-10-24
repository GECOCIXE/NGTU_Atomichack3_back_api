import json
import re
from pathlib import Path
import fitz  # PyMuPDF

# ------------------------
# Константы и перевод единиц
# ------------------------
PT_PER_INCH = 72.0
MM_PER_INCH = 25.4
PT_PER_MM = PT_PER_INCH / MM_PER_INCH

# Пороги геометрического связывания "допуск → буква"
HORIZ_MM = 30.0   # максимум вправо от строки допуска
VERT_MM  = 6.0    # допуск по вертикальному выравниванию
HORIZ_PT = HORIZ_MM * PT_PER_MM
VERT_PT  = VERT_MM * PT_PER_MM

# Нормализация латиницы в кириллицу (часто в PDF встречаются лат. A,B,C... вместо А,В,С)
_LAT2CYR = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "I": "И", "R": "Р", "Z": "З",
    "a": "А", "b": "В", "c": "С", "e": "Е", "h": "Н", "k": "К", "m": "М",
    "o": "О", "p": "Р", "t": "Т", "x": "Х", "y": "У", "i": "И", "r": "Р", "z": "З",
})
def _to_cyr_upper(s: str) -> str:
    return s.translate(_LAT2CYR).upper()

# Десятичное число (допуск), ограничим разумно чтобы отсечь номера документов.
# Пример: 0,1  0.05  10,0
RE_TOL_DECIMAL = re.compile(r"\b\d{1,2}[.,]\d{1,3}\b")

# Буквы базы в конце строки ПОСЛЕ пробела (обязателен!)
# Пример: '0,1 А' → 'А' ; '0,1 АБ' → 'АБ'
RE_FRAME_TAIL = re.compile(r"\b\d{1,2}[.,]\d{1,3}\s+([А-Я]{1,2})\s*$")

# ------------------------
# Вытягивание строк с bbox
# ------------------------
def _page_lines_with_bbox(page) -> list[dict]:
    pd = page.get_text("dict")
    raw_spans = []
    for block in pd["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                txt = span["text"].strip()
                if not txt:
                    continue
                x0, y0, x1, y1 = span["bbox"]
                raw_spans.append({"text": txt, "bbox": (x0, y0, x1, y1)})

    # группировка спанов по строкам (Y)
    rows = []
    for sp in sorted(raw_spans, key=lambda it: (it["bbox"][1], it["bbox"][0])):
        placed = False
        cy = (sp["bbox"][1] + sp["bbox"][3]) / 2
        for row in rows:
            rcy = (row["bbox"][1] + row["bbox"][3]) / 2
            if abs(cy - rcy) < 3:  # допуск по Y ~3pt
                row["text"] += " " + sp["text"]
                x0, y0, x1, y1 = row["bbox"]
                sx0, sy0, sx1, sy1 = sp["bbox"]
                row["bbox"] = (min(x0, sx0), min(y0, sy0),
                               max(x1, sx1), max(y1, sy1))
                placed = True
                break
        if not placed:
            rows.append({"text": sp["text"], "bbox": sp["bbox"]})
    return rows

# ------------------------
# Поиск баз и букв в рамках
# ------------------------
def _is_base_token(text: str) -> bool:
    """
    Отдельная короткая метка-база: 1–2 буквы (лат/кирилл), без цифр/прочего.
    """
    s = text.strip().strip(".:,;()[]{}<>«»'\"")
    return bool(1 <= len(s) <= 2 and re.fullmatch(r"[A-Za-zА-Яа-я]{1,2}", s))

def _extract_bases(lines: list[dict], doc_name: str = None) -> list[str]:
    bases = []
    doc_name_letters = set()
    
    # Если у нас есть имя документа, извлекаем из него буквы
    if doc_name:
        # Извлекаем все кириллические буквы из имени документа
        doc_name_letters = set(re.findall(r'[А-Яа-я]', doc_name.upper()))
    
    for it in lines:
        txt = _to_cyr_upper(it["text"])
        # Проверяем, не является ли текст частью имени документа
        if doc_name and txt in doc_name_letters:
            continue
            
        if _is_base_token(it["text"]):
            bases.append(txt)
    return bases

def _extract_frame_letters(lines: list[dict], doc_name: str = None) -> list[str]:
    letters = []
    doc_name_letters = set()
    
    # Если у нас есть имя документа, извлекаем из него буквы
    if doc_name:
        # Извлекаем все кириллические буквы из имени документа
        doc_name_letters = set(re.findall(r'[А-Яа-я]', doc_name.upper()))

    # кандидаты буквенных меток (1–2 буквы)
    letter_tokens = [it for it in lines if _is_base_token(it["text"])]

    for it in lines:
        txt = _to_cyr_upper(it["text"].strip())
        # Проверяем, не является ли текст частью имени документа
        if doc_name and txt in doc_name_letters:
            continue
            
        m = RE_FRAME_TAIL.search(txt)
        if m:
            # нашли, например "0,1 А"
            group = m.group(1)
            for ch in group:
                if "А" <= ch <= "Я":
                    letters.append(ch)

            # ищем соседей справа (например отдельное "Б")
            x0, y0, x1, y1 = it["bbox"]
            cy = (y0 + y1) / 2
            for lt in letter_tokens:
                lx0, ly0, lx1, ly1 = lt["bbox"]
                lcy = (ly0 + ly1) / 2
                if lx0 >= x1 and (lx0 - x1) <= HORIZ_PT and abs(lcy - cy) <= VERT_PT:
                    for ch in _to_cyr_upper(lt["text"]):
                        if "А" <= ch <= "Я":
                            letters.append(ch)

    # Дополнительная проверка: если в строке есть только буква и она находится рядом с допуском
    for it in lines:
        txt = _to_cyr_upper(it["text"].strip())
        # Проверяем, не является ли текст частью имени документа
        if doc_name and txt in doc_name_letters:
            continue
            
        if _is_base_token(txt):
            # Проверяем, находится ли эта буква рядом с допуском
            x0, y0, x1, y1 = it["bbox"]
            cy = (y0 + y1) / 2
            for line in lines:
                line_txt = _to_cyr_upper(line["text"].strip())
                if RE_TOL_DECIMAL.search(line_txt):
                    lx0, ly0, lx1, ly1 = line["bbox"]
                    lcy = (ly0 + ly1) / 2
                    if lx0 <= x1 and (x1 - lx0) <= HORIZ_PT and abs(lcy - cy) <= VERT_PT:
                        for ch in txt:
                            if "А" <= ch <= "Я":
                                letters.append(ch)

    return letters


# ------------------------
# Основная проверка
# ------------------------
def check_bases_vs_frames(pdf_path: str) -> dict:
    # Импортируем необходимые функции из критерия 1.1.1
    try:
        from criterions.criterion_1_1_1 import extract_pdf_text_as_dict, filter_titleblock_items, load_config
        # Загружаем конфигурацию
        config_path = "./config.yaml"
        cc = load_config(config_path) if Path(config_path).exists() else None
        
        # Извлекаем информацию о документе
        extracted = extract_pdf_text_as_dict(pdf_path)
        filtered = filter_titleblock_items(extracted, cc) if cc else {}
        
        # Получаем имя документа из первой страницы
        doc_name = None
        if 1 in filtered:
            items = filtered[1]
            for item in items:
                if "doc_type_name" in item:
                    doc_name = item["doc_type_name"]
                    break
                elif "text" in item and "doc_type_name" not in item:
                    # Это может быть название документа
                    doc_name = item["text"]
                    break
    except Exception:
        doc_name = None

    doc = fitz.open(pdf_path)
    report = {"pages": {}, "ok": True}
    try:
        for pageno, page in enumerate(doc, start=1):
            lines = _page_lines_with_bbox(page)

            bases_set  = sorted(set(_extract_bases(lines, doc_name)))
            frames_set = sorted(set(_extract_frame_letters(lines, doc_name)))

            missing = sorted(set(frames_set) - set(bases_set))  # в рамках есть, базы нет → ошибка
            extra   = sorted(set(bases_set) - set(frames_set))  # база есть, не используется → не критично

            page_ok = (len(missing) == 0)
            if not page_ok:
                report["ok"] = False

            report["pages"][pageno] = {
                "bases_found":  bases_set,
                "frames_found": frames_set,
                "missing_bases": missing,
                "extra_bases":   extra,
                "page_ok": page_ok,
            }
    finally:
        doc.close()
    return report

# ------------------------
# CLI
# ------------------------
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Критерий 1.1.8 — сопоставление буквенных обозначений баз и букв в рамках допусков формы/расположения"
    )
    parser.add_argument("pdf", help="Путь к PDF файлу чертежа")
    parser.add_argument("-o", "--output", help="Путь для сохранения JSON (по умолчанию — stdout)")
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        raise SystemExit(f"Файл не найден: {args.pdf}")

    res = check_bases_vs_frames(args.pdf)
    out = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
