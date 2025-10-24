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

def _is_section_designation(text: str) -> bool:
    """
    Проверяет, является ли текст обозначением сечения (например, "А-А", "Б-Б", "А А", "В-В").
    """
    s = text.strip().strip(".:,;()[]{}<>«»'\"")
    # Проверяем форматы: "А-А", "А А", "A-A", "A A" и т.д.
    return bool(re.match(r"^[А-Яа-яA-Za-z]\s*[-]?\s*[А-Яа-яA-Za-z]$", s))


def _is_single_letter_token(text: str) -> bool:
    # Проверяем, не является ли это обозначением сечения
    if _is_section_designation(text):
        return False
    
    s = text.strip().strip(".:,;()[]{}<>«»'\"")
    return bool(len(s) == 1 and re.fullmatch(r"[А-Яа-яЁё]", s))


def _extract_letters_from_tt(text: str) -> list[str]:
    """
    Извлекает буквенные обозначения из строки технических требований.
    Поддерживает формы 'поверхность А', 'поверхности А, Б', 'поверхн. А, Б, В'.
    А также другие форматы, например 'Размеры В, Д в сборочной единице не контролируется.'
    Также исключает буквы из обозначений сечений (например, "А-А", "Б-Б").
    """
    m = re.match(r"^\s*\d+\s*[.)-]?\s*(.*)", text)
    if not m:
        return []
    remainder = m.group(1)

    letters = []
    
    # Ищем "поверхн. А", "поверхность Б", "поверхности Б, В", "поверхн. Б, В, Г" и т.д.
    match_surface = re.search(r"поверхн(?:ость|ности|.)?\.?\s*([A-Za-zА-Яа-я,\s]+?)(?:\s+(?:не\s+|покр|штамп|шлиф|окраш|лакир|фосф|грунт|и\s+т\.д\.|и\s+др\.|и\s+т\.п\.|[,;\.]\s*|$))", remainder + " ", flags=re.IGNORECASE)
    if match_surface:
        letters_part = match_surface.group(1).strip()
        elements = re.split(r'[,\s]+', letters_part)
        for element in elements:
            element = element.strip()
            # Исключаем обозначения сечений
            if len(element) == 1 and re.match(r'[A-Za-zА-Яа-я]', element) and not _is_section_designation(element):
                letters.append(_to_cyr_upper(element))
    else:
        # Альтернативный паттерн для более сложных случаев
        alt_match = re.search(r"поверхн(?:ость|ности|.)?\.?\s+([A-Za-zА-Яа-я]+(?:\s*,\s*[A-Za-zА-Яа-я]+)*)", remainder, flags=re.IGNORECASE)
        if alt_match:
            letters_part = alt_match.group(1)
            elements = re.split(r'[,\s]+', letters_part)
            for element in elements:
                element = element.strip()
                # Исключаем обозначения сечений
                if len(element) == 1 and re.match(r'[A-Za-zА-Яа-я]', element) and not _is_section_designation(element):
                    letters.append(_to_cyr_upper(element))
    
    # Также ищем буквы в других форматах, например 'Размеры В, Д в сборочной единице не контролируется.'
    # Паттерн для поиска букв после слов типа "Размеры", "Поверхности" и т.д.
    other_patterns = [
        r"(?:Размеры|Поверхности|Обозначения|Обозначение)\s+([A-Za-zА-Яа-я]+(?:\s*,\s*[A-Za-zА-Яа-я]+)*)",
        r"([A-Za-zА-Яа-я])\s*,\s*([A-Za-zА-Яа-я](?:\s*,\s*[A-Za-zА-Яа-я])*)\s+(?:в|на|по)\s+\w"  # для формата "А, Б в сборке"
    ]
    
    for pattern in other_patterns:
        matches = re.findall(pattern, remainder, flags=re.IGNORECASE)
        for match in matches:
            if isinstance(match, tuple):
                # Если регулярка возвращает несколько групп, объединяем их
                match_str = ','.join(match)
                elements = re.split(r'[,\s]+', match_str)
            else:
                elements = re.split(r'[,\s]+', match)
            
            for element in elements:
                element = element.strip()
                # Исключаем обозначения сечений
                if len(element) == 1 and re.match(r'[A-Za-zА-Яа-я]', element) and not _is_section_designation(element):
                    letter = _to_cyr_upper(element)
                    if letter not in letters:  # избегаем дубликатов
                        letters.append(letter)
    
    return letters


def _is_arrow_designation(text: str) -> bool:
    """
    Проверяет, является ли текст обозначением стрелки (например, "No 1: n.1.1.3", "No 2: n.1.1.3").
    """
    s = text.strip()
    # Проверяем различные форматы обозначений стрелок
    return bool(re.match(r"^No\s+\d+\s*:\s*n\.\d+\.\d+\.\d+", s, re.IGNORECASE)) or \
           bool(re.match(r"^No\s+\d+\s*:\s*n\.\d+\.\d+", s, re.IGNORECASE)) or \
           bool(re.match(r"^No\s+\d+", s, re.IGNORECASE))


def _is_near_arrow(lines: list[dict], letter_bbox: list, distance_threshold: float = 50.0) -> bool:
    """
    Проверяет, находится ли буква рядом с обозначением стрелки.
    """
    x1, y1, x2, y2 = letter_bbox
    
    # Центр буквы
    center_x = (x1 + x2) / 2
    center_y = (y1 + y2) / 2
    
    for it in lines:
        text = it["text"].strip()
        bbox = it["bbox"]
        
        # Проверяем, является ли строка обозначением стрелки
        if _is_arrow_designation(text):
            ax1, ay1, ax2, ay2 = bbox
            
            # Центр обозначения стрелки
            arrow_center_x = (ax1 + ax2) / 2
            arrow_center_y = (ay1 + ay2) / 2
            
            # Вычисляем расстояние между центрами
            distance = ((center_x - arrow_center_x) ** 2 + (center_y - arrow_center_y) ** 2) ** 0.5
            
            if distance <= distance_threshold:
                return True
    
    return False


def _extract_letters_from_field(all_lines: list[dict]) -> tuple[list[str], list[dict]]:
    """
    Извлекает буквенные обозначения с поля чертежа:
      - отдельные строки из 1–2 букв,
      - буквы в конце размерных надписей (например '⌀20 +0,2 А').
    Также исключает буквы, которые являются частью обозначений сечений (например, "А-А", "Б-Б")
    или находятся рядом с обозначениями стрелок.
    Возвращает (список букв, список словарей с информацией о буквах {text, bbox})
    """
    letters = []
    letter_info = []
    
    # Разделяем на ТТ и поле, чтобы обрабатывать только поле
    _, field_lines = split_into_tt_and_field(all_lines)
    
    for it in field_lines:
        text = it["text"].strip()
        bbox = it["bbox"]

        # --- игнорируем обозначения шероховатости (латиница): Ra, Rz, Rt ---
        if re.match(r"(?i)^(Ra|Rz|Rt)\b", text):
            continue

        # --- игнорируем строки, содержащие обозначения сечений вида "А-А", "Б-Б", "В-В" и т.д. ---
        if _is_section_designation(text):
            continue

        # Проверяем, содержит ли строка обозначение сечения вида "Сечение А-А", "А-А" и т.д.
        section_pattern = r"(?:[Сс]ечение\s+)?[А-Яа-яA-Za-z]\s*[-]?\s*[А-Яа-яA-Za-z](?:\s+[Сс]ечение)?"
        if re.search(section_pattern, text):
            continue  # пропускаем строки, содержащие обозначения сечений

        # отдельная буква на строке
        if _is_single_letter_token(text):
            letter = _to_cyr_upper(text)
            
            # Проверяем, находится ли буква рядом с обозначением стрелки
            if _is_near_arrow(all_lines, bbox):
                continue  # пропускаем букву, если она рядом с обозначением стрелки
            
            letters.append(letter)
            letter_info.append({"text": letter, "bbox": bbox})
            continue

        # буква в конце строки ( ... ' ⌀20 +0,2 А' )
        m = re.search(r"\s([A-Za-zА-Яа-я])$", text)
        if m:
            letter = _to_cyr_upper(m.group(1))
            
            # Проверяем, находится ли буква рядом с обозначением стрелки
            if _is_near_arrow(all_lines, bbox):
                continue  # пропускаем букву, если она рядом с обозначением стрелки
            
            letters.append(letter)
            # Для буквы в конце строки, используем bbox последнего символа
            # Или можно использовать bbox всей строки
            letter_info.append({"text": letter, "bbox": bbox})
    return letters, letter_info


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
    
    # Сначала соберем все ТТ изо всех страниц
    all_tt_letters: list[str] = []
    all_lines_by_page = {}  # сохраним все строки для проверки близости к стрелкам
    
    for pageno, lines in pages.items():
        all_lines_by_page[pageno] = lines
        tt_lines, _ = split_into_tt_and_field(lines)
        for it in tt_lines:
            all_tt_letters.extend(_extract_letters_from_tt(it["text"]))
    
    # Уникальные буквы ТТ изо всех страниц
    all_tt_letters_norm = sorted(set(all_tt_letters))
    
    # Теперь для каждой страницы проверяем её поля против общих ТТ
    for pageno, lines in pages.items():
        tt_lines, _ = split_into_tt_and_field(lines)

        # буквы с поля и их координаты
        # передаем все строки страницы для проверки близости к стрелкам
        field_letters, field_letter_info = _extract_letters_from_field(all_lines_by_page[pageno])
        field_letters_norm = sorted(set(field_letters))

        # проверка: используем все ТТ изо всех страниц
        missing_on_field = sorted(set(all_tt_letters_norm) - set(field_letters_norm))
        extra_on_field = sorted(set(field_letters_norm) - set(all_tt_letters_norm))
        
        # Найти координаты для лишних букв на поле
        extra_letter_bboxes = []
        for letter_info in field_letter_info:
            if letter_info["text"] in extra_on_field:
                extra_letter_bboxes.append({
                    "text": letter_info["text"],
                    "bbox": letter_info["bbox"]
                })

        page_info = {
            "tt_lines": [it["text"] for it in tt_lines],
            "tt_letters": all_tt_letters_norm,  # теперь используем все ТТ изо всех страниц
            "field_letters": field_letters_norm,
            "missing_on_field": missing_on_field,
            "extra_on_field": extra_on_field,
            "extra_letter_bboxes": extra_letter_bboxes,  # координаты лишних букв для обводки
            "page_ok": not missing_on_field and not extra_on_field,
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