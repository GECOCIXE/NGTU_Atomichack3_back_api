import json
import re
from pathlib import Path
import fitz  # PyMuPDF

# =========================
# Нормализация букв (латиница -> кириллица)
# =========================

_LAT2CYR = str.maketrans({
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "K": "К", "M": "М",
    "O": "О", "P": "Р", "T": "Т", "X": "Х", "Y": "У", "I": "И",
    "a": "А", "b": "В", "c": "С", "e": "Е", "h": "Н", "k": "К",
    "m": "М", "o": "О", "p": "Р", "t": "Т", "x": "Х", "y": "У", "i": "И",
    "R": "Р", "r": "Р", "Z": "З", "z": "З",
})

def _to_cyr_upper(s: str) -> str:
    return s.translate(_LAT2CYR).upper()


# =========================
# Извлечение букв
# =========================

def _is_single_letter_token(text: str) -> bool:
    s = text.strip().strip(".:,;()[]{}<>«»'\"")
    return bool(len(s) == 1 and re.fullmatch(r"[А-Яа-яЁё]", s))


def _extract_letters_from_tt(text: str) -> list[str]:
    """
    Извлекает буквенные обозначения из строки технических требований.
    Буква должна идти после слова 'поверхн' / 'поверхность'.
    """
    m = re.match(r"^\s*\d+\s*[.)-]?\s*(.*)", text)
    if not m:
        return []
    remainder = m.group(1)

    letters = []
    # Ищем "поверхн. А", "поверхность Б"
    m2 = re.search(r"поверхн(?:ость)?\.?\s*([A-Za-zА-Яа-я])", remainder, flags=re.IGNORECASE)
    if m2:
        letters.append(_to_cyr_upper(m2.group(1)))
    return letters



def _extract_letters_from_field(lines: list[dict]) -> list[str]:
    """
    Извлекает буквенные обозначения с поля чертежа:
      - отдельные строки из 1–2 букв,
      - буквы в конце размерных надписей (например '⌀20 +0,2 А').
    """
    letters = []
    for it in lines:
        text = it["text"].strip()

        # --- игнорируем обозначения шероховатости (латиница): Ra, Rz, Rt ---
        if re.match(r"(?i)^(Ra|Rz|Rt)\b", text):
            continue

        # отдельная буква на строке
        if _is_single_letter_token(text):
            letters.append(_to_cyr_upper(text))
            continue

        # буква в конце строки ( ... ' ⌀20 +0,2 А' )
        m = re.search(r"\s([A-Za-zА-Яа-я])$", text)
        if m:
            letters.append(_to_cyr_upper(m.group(1)))
    return letters


# =========================
# Извлечение текста с координатами
# =========================

def extract_lines_with_bbox(pdf_path: str) -> dict[int, list[dict]]:
    """
    Возвращает {page_index: [ {text, bbox, size}, ... ] }
    """
    doc = fitz.open(pdf_path)
    pages: dict[int, list[dict]] = {}
    try:
        for i, page in enumerate(doc, start=1):
            pd = page.get_text("dict")
            lines_out = []
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
                    bbox = [
                        round(min(x0s), 2), round(min(y0s), 2),
                        round(max(x1s), 2), round(max(y1s), 2)
                    ]
                    size = max(float(s.get("size", 0)) for s in spans)
                    lines_out.append({"text": text, "bbox": bbox, "size": round(size, 2)})
            lines_out.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))
            pages[i] = lines_out
    finally:
        doc.close()
    return pages


# =========================
# Разделение на ТТ и поле
# =========================

def split_into_tt_and_field(lines: list[dict]) -> tuple[list[dict], list[dict]]:
    """
    Эвристика:
      - ТТ: только строки, начинающиеся с номера ("1 ", "2.", "3)").
      - Поле: все остальные строки.
    """
    tt_lines = []
    field_lines = []
    for it in lines:
        t = it["text"].strip()
        if re.match(r"^\s*\d+\s*[.)-]?\s+", t):
            tt_lines.append(it)
        else:
            field_lines.append(it)
    return tt_lines, field_lines


# =========================
# Основная проверка
# =========================

def check_letter_designations(pdf_path: str) -> dict:
    pages = extract_lines_with_bbox(pdf_path)
    report = {"pages": {}, "ok": True}
    for pageno, lines in pages.items():
        tt_lines, field_lines = split_into_tt_and_field(lines)

        # буквы из ТТ
        tt_letters: list[str] = []
        for it in tt_lines:
            tt_letters.extend(_extract_letters_from_tt(it["text"]))
        tt_letters_norm = sorted(set(tt_letters))

        # буквы с поля
        field_letters = _extract_letters_from_field(field_lines)
        field_letters_norm = sorted(set(field_letters))

        # проверка
        missing_on_field = sorted(set(tt_letters_norm) - set(field_letters_norm))
        extra_on_field = sorted(set(field_letters_norm) - set(tt_letters_norm))

        page_info = {
            "tt_lines": [it["text"] for it in tt_lines],
            "tt_letters": tt_letters_norm,
            "field_letters": field_letters_norm,
            "missing_on_field": missing_on_field,
            "extra_on_field": extra_on_field,
            "page_ok": not missing_on_field and not extra_on_field,  # ошибка только если из ТТ нет на поле
        }
        report["pages"][pageno] = page_info
        if missing_on_field or extra_on_field:
            report["ok"] = False
    return report


# =========================
# CLI
# =========================

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Критерий 1.1.3 — проверка буквенных обозначений в ТТ и на поле чертежа."
    )
    parser.add_argument("pdf", help="Путь к PDF файлу чертежа")
    parser.add_argument(
        "-o", "--output", help="Путь для сохранения JSON (по умолчанию — stdout)"
    )
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        raise SystemExit(f"Файл не найден: {args.pdf}")

    res = check_letter_designations(args.pdf)
    out = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
