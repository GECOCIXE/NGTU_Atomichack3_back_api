import json
import re
from pathlib import Path
import fitz  # PyMuPDF


def extract_lines_with_bbox(pdf_path: str) -> dict[int, list[str]]:
    """
    Возвращает {page_index: [строки текста]}.
    """
    doc = fitz.open(pdf_path)
    pages: dict[int, list[str]] = {}
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
                    if text:
                        lines_out.append(text)
            pages[i] = lines_out
    finally:
        doc.close()
    return pages


def _extract_stars(text: str) -> list[str]:
    """
    Ищет *, **, *** в строке текста, включая варианты типа "2**" и "2 **".
    """
    # Находим звездочки, которые могут идти сразу после числа или после пробела
    # Паттерн: число, за которым может следовать 0 или более пробельных символов, затем 1-3 звездочки
    matches = re.findall(r"\d+\s*(\*{1,3})|(\b\*{1,3}\b)", text)
    # Извлекаем звездочки из обеих групп захвата
    stars = []
    for match in matches:
        if match[0]:  # Первая группа захвата (после числа)
            stars.append(match[0])
        elif match[1]:  # Вторая группа захвата (отдельные звездочки)
            stars.append(match[1])
    return stars


def split_into_tt_and_field(lines: list[str]) -> tuple[list[str], list[str]]:
    """
    Делим строки на ТТ (нумерованные пункты) и поле чертежа.
    - ТТ: начинаются с числа + (точка/скобка/дефис + пробел) или число + звездочки + текст или просто число + текст.
    - Остальное: поле.
    """
    tt_lines = []
    field_lines = []
    for t in lines:
        # Проверяем, начинается ли строка с числа, за которым следует:
        # 1. точка/скобка/дефис и пробел, ИЛИ
        # 2. одна или несколько звездочек и затем пробел и текст, ИЛИ
        # 3. просто пробел и текст (а не просто число)
        if (re.match(r"^\s*\d+\s*[.)-]\s+", t) or 
            re.match(r"^\s*\d+\s*\*{1,3}\s+.+", t) or  # для строк вида "2** текст", "2 * текст"
            (re.match(r"^\s*\d+\s+.+", t) and not re.match(r"^\s*\d+\s*$", t))):  # строка с числом, пробелом и текстом (не только число)
            tt_lines.append(t)
        else:
            field_lines.append(t)
    return tt_lines, field_lines


def check_stars(pdf_path: str) -> dict:
    pages = extract_lines_with_bbox(pdf_path)
    report = {"pages": {}, "ok": True}

    for pageno, lines in pages.items():
        tt_lines, field_lines = split_into_tt_and_field(lines)

        tt_stars = sorted(set(st for line in tt_lines for st in _extract_stars(line)))
        field_stars = sorted(set(st for line in field_lines for st in _extract_stars(line)))

        missing_in_tt = sorted(set(field_stars) - set(tt_stars))
        missing_on_field = sorted(set(tt_stars) - set(field_stars))

        page_info = {
            "tt_lines": tt_lines,
            "tt_stars": tt_stars,
            "field_lines": field_lines,
            "field_stars": field_stars,
            "missing_in_tt": missing_in_tt,
            "missing_on_field": missing_on_field,
            "page_ok": not missing_in_tt and not missing_on_field,
        }
        report["pages"][pageno] = page_info
        if not page_info["page_ok"]:
            report["ok"] = False

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Критерий 1.1.4 — проверка наличия *, **, *** в ТТ и на поле чертежа."
    )
    parser.add_argument("pdf", help="Путь к PDF файлу чертежа")
    parser.add_argument(
        "-o", "--output", help="Путь для сохранения JSON (по умолчанию — stdout)"
    )
    args = parser.parse_args()

    if not Path(args.pdf).exists():
        raise SystemExit(f"Файл не найден: {args.pdf}")

    res = check_stars(args.pdf)
    out = json.dumps(res, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out, encoding="utf-8")
    else:
        print(out)
