import json
from pathlib import Path
import re
import fitz  # PyMuPDF
from rich import print
import yaml

# =========================
# Генератор регексов по русскому названию
# =========================

_RU_SUFFIXES = [
    "иями","ями","ами","иях","ях","ием","ьем","ем","ия","ие","ий","ью","ье","ья",
    "ого","его","ому","ему","ыми","ими","ой","ый","ий","ое","ее","ых","их","ую","юю",
    "ая","яя","ые","ие","ам","ям","ах","ях","ою","ею","ов","ев","ям","ем","ам",
    "а","я","о","е","ы","и","у","ю","ь"
]

def _norm_text(s: str) -> str:
    return re.sub(r"\s+", " ", s.lower().replace("ё", "е")).strip()

def _strip_suffix(token: str) -> str:
    for suf in sorted(_RU_SUFFIXES, key=len, reverse=True):
        if len(token) > 3 and token.endswith(suf):
            token = token[: -len(suf)]
            break
    if token.endswith("нн"):
        token = token[:-1]
    return token

def generate_ru_regex(name: str) -> str:
    """
    'Ведомость эксплуатационных документов'
      -> r'\\bведомост\\w*\\s+эксплуатацион\\w*\\s+документ\\w*\\b'
    """
    s = _norm_text(name)
    words = [w for w in re.split(r"[^\w\-]+", s) if w]
    parts = []
    for w in words:
        if "-" in w:
            sub = []
            for seg in w.split("-"):
                seg = _strip_suffix(seg)
                if seg:
                    sub.append(fr"{re.escape(seg)}\w*")
            if sub:
                parts.append(r"[- ]?".join(sub))
        else:
            stem = _strip_suffix(w)
            if stem:
                parts.append(fr"{re.escape(stem)}\w*")
    if not parts:
        return r"\b\w+\b"
    return r"\b" + r"\s+".join(parts) + r"\b"


# =========================
# Конфиг и компиляция
# =========================

class CompiledConfig:
    def __init__(self, conf: dict):
        self.conf = conf

        # 1) код документа
        doc_code_rx = conf.get("doc_code_regex") or r"^[A-ZА-Я0-9]{2,}\.\d{3,}\.\d{3,}[A-ZА-Я0-9-]*$"
        self.DOC_CODE_RE = re.compile(doc_code_rx, re.IGNORECASE | re.UNICODE)

        # 2) эвристики
        self.name_min_font_size = int(conf.get("name_min_font_size", 18))
        self.name_y_window = int(conf.get("name_y_window", 60))

        # 3) коды -> имена
        self.code_suffix_map: dict[str, str] = {}
        for k, v in (conf.get("code_suffix_map") or {}).items():
            self.code_suffix_map[str(k).upper()] = v

        # 4) wildcard-и
        self.code_wildcards: list[tuple[str, str]] = []
        for wc in conf.get("code_wildcards", []) or []:
            self.code_wildcards.append((str(wc["prefix"]).upper(), wc["name"]))

        # 5) имена типов документов = уникальные имена из карт + имена из wildcard-ов + (опц.) extra_names
        names_set = set(self.code_suffix_map.values()) | {name for _, name in self.code_wildcards}
        names_set |= set(conf.get("extra_doc_type_names", []) or [])

        # 6) overrides для регексов (name -> custom regex)
        regex_overrides: dict[str, str] = conf.get("regex_overrides", {}) or {}

        # 7) компилируем паттерны для каждого имени
        self.DOC_TYPE_PATTERNS: dict[str, re.Pattern] = {}
        for name in sorted(names_set):
            rx = regex_overrides.get(name) or generate_ru_regex(name)
            self.DOC_TYPE_PATTERNS[name] = re.compile(rx, re.IGNORECASE | re.UNICODE)

    # служебные
    def match_doc_type(self, text: str) -> str | None:
        s = _norm_text(text)
        for name, rx in self.DOC_TYPE_PATTERNS.items():
            if rx.search(s):
                return name
        return None


# =========================
# Соответствие шифр ↔ тип документа
# =========================

_LAT2CYR = str.maketrans({
    "A":"А","B":"В","C":"С","E":"Е","H":"Н","K":"К","M":"М","O":"О","P":"Р","T":"Т","X":"Х","Y":"У","I":"И",
    "a":"А","b":"В","c":"С","e":"Е","h":"Н","k":"К","m":"М","o":"О","p":"Р","t":"Т","x":"Х","y":"У","i":"И",
    "R":"Р","r":"Р","Z":"З","z":"З",
})

_SUFFIX_RE = re.compile(r"([A-Za-zА-Яа-я]{1,5})$")

def _to_cyr_upper(s: str) -> str:
    return s.translate(_LAT2CYR).upper()

def extract_code_suffix(doc_code_text: str) -> str | None:
    if not doc_code_text:
        return None
    m = _SUFFIX_RE.search(doc_code_text.strip())
    if not m:
        return None
    return _to_cyr_upper(m.group(1))

def name_by_code_suffix(suffix: str | None, cc: CompiledConfig) -> str | None:
    if not suffix:
        return None
    if suffix in cc.code_suffix_map:
        return cc.code_suffix_map[suffix]
    for pref, name in cc.code_wildcards:
        if suffix.startswith(pref):
            return name
    return None

def canonicalize_doc_type_name(doc_type_text: str | None, cc: CompiledConfig) -> str:
    if not doc_type_text:
        return ""
    s = doc_type_text.strip()
    known = set(cc.DOC_TYPE_PATTERNS.keys()) | set(cc.code_suffix_map.values())
    for v in known:
        if _norm_text(s) == _norm_text(v):
            return v
    return s

def code_suffix_matches_doc_type(doc_code_text: str, doc_type_text: str | None, cc: CompiledConfig):
    suffix = extract_code_suffix(doc_code_text)
    name_from_suffix = name_by_code_suffix(suffix, cc)
    canon_doc_type = canonicalize_doc_type_name(doc_type_text or "", cc)
    if not name_from_suffix or not canon_doc_type:
        return (False, suffix, name_from_suffix, canon_doc_type or None)
    return (_norm_text(name_from_suffix) == _norm_text(canon_doc_type), suffix, name_from_suffix, canon_doc_type)


# =========================
# Извлечение и фильтрация
# =========================

def extract_pdf_text_as_dict(pdf_path: str) -> dict:
    pdf_path = str(pdf_path)
    doc = fitz.open(pdf_path)
    data: dict[int, list[dict]] = {}
    try:
        for page_index, page in enumerate(doc, start=1):
            page_dict = page.get_text("dict")
            items = []
            for block in page_dict.get("blocks", []):
                if block.get("type", 0) != 0:
                    continue
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        text = (span.get("text") or "").strip()
                        if not text:
                            continue
                        bbox = [round(float(v), 2) for v in span.get("bbox", (0, 0, 0, 0))]
                        items.append({
                            "text": text,
                            "bbox": bbox,
                            "font": span.get("font"),
                            "size": round(float(span.get("size", 0.0)), 2),
                        })
            items.sort(key=lambda it: (it["bbox"][1], it["bbox"][0]))
            data[page_index] = items
    finally:
        doc.close()
    return data

def filter_titleblock_items(extracted: dict, cc: CompiledConfig) -> dict:
    """
    Оставляет:
      - код (добавит code_suffix / code_suffix_name / code_doc_type_match),
      - имя изделия,
      - тип документа (doc_type_name).
    """
    filtered = {}
    for page, items in extracted.items():
        # код
        code_items = [it for it in items if cc.DOC_CODE_RE.match(it.get('text', ''))]

        # тип документа
        doc_type_items = []
        for it in items:
            name = cc.match_doc_type(it.get('text', ''))
            if name:
                it2 = dict(it)
                it2["doc_type_name"] = name
                doc_type_items.append(it2)

        # кандидаты на наименование изделия
        name_candidates = []
        if doc_type_items:
            anchor = max(doc_type_items, key=lambda it: (it.get('size', 0), -it['bbox'][1]))
            y0 = anchor['bbox'][1]
            name_candidates = [
                it for it in items
                if it.get('size', 0) >= cc.name_min_font_size
                and not any(ch.isdigit() for ch in it.get('text', ''))
                and cc.match_doc_type(it.get('text', '')) is None
                and (y0 - cc.name_y_window) <= it['bbox'][1] <= (y0 + cc.name_y_window)
            ]
        if not name_candidates:
            only_words = [
                it for it in items
                if not any(ch.isdigit() for ch in it.get('text', ''))
                and cc.match_doc_type(it.get('text', '')) is None
            ]
            name_candidates = sorted(only_words, key=lambda it: it.get('size', 0), reverse=True)[:3]

        # выбор лучших
        best_code = max(code_items, key=lambda it: (it.get('size', 0), it['bbox'][1])) if code_items else None
        best_name = max(name_candidates, key=lambda it: it.get('size', 0)) if name_candidates else None
        best_doc_type = max(doc_type_items, key=lambda it: (it.get('size', 0), -it['bbox'][1])) if doc_type_items else None

        # сопоставление кода и типа
        if best_code:
            bc = dict(best_code)
            if best_doc_type:
                match, suffix, suffix_name, canon_doc = code_suffix_matches_doc_type(
                    best_code["text"], best_doc_type.get("doc_type_name") or best_doc_type.get("text"), cc
                )
            else:
                suffix = extract_code_suffix(best_code["text"])
                suffix_name = name_by_code_suffix(suffix, cc)
                match, canon_doc = (False, None)
            bc["code_suffix"] = suffix
            bc["code_suffix_name"] = suffix_name
            bc["code_doc_type_match"] = bool(match)
            best_code = bc

        if best_doc_type:
            bdt = dict(best_doc_type)
            if "doc_type_name" not in bdt:
                bdt["doc_type_name"] = canonicalize_doc_type_name(bdt.get("text", ""), cc)
            best_doc_type = bdt

        filtered_list = [it for it in (best_code, best_name, best_doc_type) if it]
        filtered[page] = filtered_list
    return filtered


# =========================
# CLI
# =========================

def load_config(path: str) -> CompiledConfig:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return CompiledConfig(cfg)

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Extract text from a PDF drawing into a Python dict using PyMuPDF (configurable via YAML).")
    parser.add_argument("pdf", help="Путь к PDF файлу")
    parser.add_argument("-c", "--config", default="config.yaml", help="Путь к YAML конфигу (по умолчанию: config.yaml)")
    parser.add_argument("-o", "--output", help="Путь для сохранения JSON (по умолчанию вывод в stdout)")
    parser.add_argument("--gen-regex", help="Сгенерировать регекс по русскому названию и выйти (игнорирует PDF)")
    args = parser.parse_args()

    # генератор регексов по названию
    if args.gen_regex:
        print(generate_ru_regex(args.gen_regex))
        raise SystemExit(0)

    if not Path(args.pdf).exists():
        raise SystemExit(f"Файл не найден: {args.pdf}")
    if not Path(args.config).exists():
        raise SystemExit(f"Конфиг не найден: {args.config}")

    cc = load_config(args.config)
    result = extract_pdf_text_as_dict(args.pdf)
    result = filter_titleblock_items(result, cc)

    out_json = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        Path(args.output).write_text(out_json, encoding="utf-8")
    else:
        print(out_json)

