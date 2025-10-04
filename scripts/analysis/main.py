from pathlib import Path
import fitz  # PyMuPDF
from typing import List, Dict, Any
from rich import print
from scripts.analysis.criterion_1_1_1 import extract_pdf_text_as_dict, filter_titleblock_items, load_config  # :contentReference[oaicite:2]{index=2}
from scripts.analysis.criterion_1_1_2_n import run_check as run_check_1_1_2                                   # :contentReference[oaicite:3]{index=3}
from scripts.analysis.criterion_1_1_3_n import check_letter_designations                                        # :contentReference[oaicite:4]{index=4}
from scripts.analysis.criterion_1_1_4 import check_stars                                                       # :contentReference[oaicite:5]{index=5}
from scripts.analysis.criterion_1_1_5 import check as check_1_1_5                                              # :contentReference[oaicite:6]{index=6}
from scripts.analysis.criterion_1_1_6 import check as check_1_1_6                                              # :contentReference[oaicite:7]{index=7}
from scripts.analysis.criterion_1_1_8 import check_bases_vs_frames
import os
from typing import Optional
from scripts.analysis.test import check_gost  # 1.1.7 и 1.1.9 через OpenRouter (см. test.py)  :contentReference[oaicite:1]{index=1}

from scripts.db import SessionLocal
from scripts.crud import update_document_analysis

def _pdf_first_page_to_png(pdf_path: str, dpi: int = 200) -> Optional[str]:
    """
    Рендерит первую страницу PDF в PNG и возвращает путь к PNG.
    Если не удалось — возвращает None.
    """
    try:
        doc = fitz.open(pdf_path)
        page = doc[0]
        zoom = dpi / 72.0
        mat = fitz.Matrix(zoom, zoom)
        pix = page.get_pixmap(matrix=mat, alpha=False)
        out_png = str(Path(pdf_path).with_suffix(".page1.png"))
        pix.save(out_png)
        doc.close()
        return out_png
    except Exception:
        return None


# ---------- PIPELINE ----------
def pipeline(pdf_path: str) -> dict:
    output: dict = {}

    out_1_1_1 = extract_pdf_text_as_dict(pdf_path)
    res_1_1_1 = filter_titleblock_items(out_1_1_1, load_config("scripts/analysis/config.yaml"))
    output["1.1.1"] = res_1_1_1

    output["1.1.2"] = run_check_1_1_2(pdf_path)
    output["1.1.3"] = check_letter_designations(pdf_path)
    output["1.1.4"] = check_stars(pdf_path)
    output["1.1.5"] = check_1_1_5(pdf_path)
    output["1.1.6"] = check_1_1_6(pdf_path)
    output["1.1.8"] = check_bases_vs_frames(pdf_path)
        # --- 1.1.7 и 1.1.9: проверки без bbox (ok/comment) ---
    # Берем API-ключ из переменной окружения, рендерим 1-ю страницу PDF в PNG.
    api_key = os.getenv("OPENROUTER_API_KEY")
    candidate_png = _pdf_first_page_to_png(pdf_path)

    def _safe_check(rule: str) -> dict:
        # стандартный ответ по-умолчанию (если не смогли проверить)
        fallback = {"ok": None, "comment": "Проверка не выполнена (нет API-ключа или изображения)."}
        if not api_key or not candidate_png:
            return fallback
        try:
            return check_gost(rule, candidate_png, api_key)
        except Exception as e:
            return {"ok": None, "comment": f"Проверка не выполнена: {e}"}

    output["1.1.9"] = _safe_check("1.1.9")
    output["1.1.7"] = _safe_check("1.1.7")

    return output


# ---------- ВСПОМОГАТЕЛЬНОЕ ----------

import re
import fitz
from typing import Iterable, Tuple

GD_T_SYMBOLS = set("⊥∥⌖⌓⏥⌭⌯⟂⟂⟂⌀")  # позиционность, параллельность, профиль, и пр. (как минимум самые частые)
VERT_BAR = {"|", "⎪", "¦"}  # вертикальные разделители в рамке

def _words_by_lines(page: fitz.Page) -> dict:
    """
    Группирует page.get_text('words') по (block, line).
    Возвращает: {(block_no, line_no): [ (x0,y0,x1,y1, text, ...), ... ]}
    """
    words = page.get_text("words")  # x0,y0,x1,y1,text, block, line, word
    by_line = {}
    for w in words:
        *xyxy, text, block, line, _ = w
        by_line.setdefault((block, line), []).append(w)
    # слева-направо
    for k in by_line:
        by_line[k].sort(key=lambda w: (w[0], w[1], w[2]))
    return by_line


def _cluster_rect(words: Iterable[Tuple[float, float, float, float, str, int, int, int]]) -> fitz.Rect:
    """Объединяет слова в один прямоугольник."""
    xs0 = [w[0] for w in words]; ys0 = [w[1] for w in words]
    xs1 = [w[2] for w in words]; ys1 = [w[3] for w in words]
    return fitz.Rect(min(xs0), min(ys0), max(xs1), max(ys1))


def find_ra_without_check(page: fitz.Page) -> list[fitz.Rect]:
    """
    Ищет последовательности вида 'Ra <число>' и проверяет, есть ли дальше скобки '(...)' с символом '√'.
    Если '√' (U+221A) нет, возвращает bbox кластера 'Ra + значение (+ скобки, если есть)'.
    """
    result: list[fitz.Rect] = []
    by_line = _words_by_lines(page)

    for (_blk, _ln), words in by_line.items():
        # соберём строку (для проверок) и одновременно обработаем токены
        texts = [w[4] for w in words]
        # нормализованный список (с запятой/точкой)
        norm = [t.replace(" ", "") for t in texts]

        # пробегаем по словам и ищем 'Ra'
        for i, t in enumerate(texts):
            if t.strip().lower() != "ra":
                continue

            # возьмем небольшой «коридор» вправо (до 10 слов или 180pt)
            cluster = [words[i]]
            x_right_limit = words[i][2] + 180.0

            j = i + 1
            has_value = False
            paren_chunk = ""  # содержимое после '(' и до ')'
            has_parens = False
            while j < len(words) and words[j][0] <= x_right_limit:
                txt = texts[j].strip()
                cluster.append(words[j])

                # ловим число «0,5 / 3.2 / 6»
                if re.fullmatch(r"\d+[.,]?\d*", txt):
                    has_value = True

                # ловим скобки «( ... )»
                if "(" in txt or ")" in txt:
                    has_parens = True
                # упрощённо собираем всё, что между первой '(' и ближайшей ')'
                if "(" in txt:
                    paren_chunk = txt.split("(", 1)[1]
                elif paren_chunk != "":
                    paren_chunk += txt
                if ")" in txt and paren_chunk != "":
                    paren_chunk = paren_chunk.split(")", 1)[0]

                j += 1

            if not has_value:
                # без числа — это не параметр шероховатости, пропускаем
                continue

            need_sqrt = True  # по ТЗ тут нужен '√'
            has_sqrt = ("√" in paren_chunk) if has_parens else False

            if need_sqrt and not has_sqrt:
                rect = _cluster_rect(cluster)
                result.append(rect)

    return result


def find_gdt_frames(page: fitz.Page) -> list[fitz.Rect]:
    """
    Находит «похожие на контрольные рамки GD&T» строки:
    — в строке есть хотя бы один символ из GD_T_SYMBOLS И хотя бы один вертикальный разделитель.
    Возвращает bbox строки (как грубый ориентир для подсветки).
    """
    result: list[fitz.Rect] = []
    by_line = _words_by_lines(page)

    for (_blk, _ln), words in by_line.items():
        texts = "".join(w[4] for w in words)
        if any(sym in texts for sym in GD_T_SYMBOLS) and any(v in texts for v in VERT_BAR):
            result.append(_cluster_rect(words))
    return result


def _union_tt_bbox(report_1_1_2: dict, page_index: int):
    try:
        page = report_1_1_2["pages"][page_index]
        cols = page.get("tt_columns") or []
        if not cols:
            return None
        rects = [fitz.Rect(*c["bbox_pt"]) for c in cols]
        x0 = min(r.x0 for r in rects); y0 = min(r.y0 for r in rects)
        x1 = max(r.x1 for r in rects); y1 = max(r.y1 for r in rects)
        return fitz.Rect(x0, y0, x1, y1)
    except Exception:
        return None

# --- NEW: утилиты для группировки одинаковых объектов 1.1.5/1.1.6 ---
_DIM_CRITS = {"1.1.5", "1.1.6"}

def _dim_group_key(item: dict) -> tuple | None:
    """
    Если item относится к 1.1.5/1.1.6 — вернуть ключ группировки «одного объекта».
    Берём текст (meta['text']) как идентификатор. Если текста нет — None.
    """
    c = item["criterion"]
    if c in _DIM_CRITS:
        meta = item.get("meta") or {}
        t = meta.get("text")
        if t:
            # нормализуем пробелы
            t = " ".join(str(t).split())
            return ("DIM_TILT", t)
    return None


def _prefer_criterion(candidates: set[str]) -> str:
    """
    Для группы 1.1.5/1.1.6 отдаём приоритет 1.1.5.
    Для всех остальных — единственный элемент множества.
    """
    if candidates & _DIM_CRITS:
        return "1.1.5" if "1.1.5" in candidates else "1.1.6"
    # fallback
    return sorted(candidates)[0]



def _add_violation(viol_list: list, page: int, bbox, crit: str, note: str, payload: dict | None = None):
    viol_list.append({
        "page": page,
        "bbox": [float(bbox[0]), float(bbox[1]), float(bbox[2]), float(bbox[3])],
        "criterion": crit,
        "note": note,
        "meta": payload or {}
    })


def _rect_distance(r1: fitz.Rect, r2: fitz.Rect) -> float:
    """0 если пересекаются; иначе евклидово расстояние между ближайшими точками прямоугольников."""
    if r1.intersects(r2) or r1.tl in r2 or r1.br in r2 or r2.tl in r1 or r2.br in r1:
        return 0.0
    dx = max(r2.x0 - r1.x1, r1.x0 - r2.x1, 0)
    dy = max(r2.y0 - r1.y1, r1.y0 - r2.y1, 0)
    return (dx * dx + dy * dy) ** 0.5


def _iou(r1: fitz.Rect, r2: fitz.Rect) -> float:
    inter = r1 & r2
    if inter.is_empty: 
        return 0.0
    a1 = r1.get_area()
    a2 = r2.get_area()
    return inter.get_area() / (a1 + a2 - inter.get_area() + 1e-9)


def collect_violations(pdf_path: str, out: dict) -> list[dict]:
    """Собирает все нарушения с bbox (без объединения)."""
    violations: list[dict] = []

    # --- 1.1.1 ---
    rep_111 = out.get("1.1.1") or {}
    for page, items in (rep_111 or {}).items():
        if not isinstance(items, list):
            continue
        best_code = None
        best_doc_type = None
        for it in items:
            if "code_doc_type_match" in it:
                best_code = it
            if "doc_type_name" in it:
                best_doc_type = it
        if best_code and not best_code.get("code_doc_type_match", False):
            bbox = best_code["bbox"]
            suffix = best_code.get("code_suffix")
            suffix_name = best_code.get("code_suffix_name")
            doc_type_name = (best_doc_type or {}).get("doc_type_name") or (best_doc_type or {}).get("text")
            note = f"Суффикс '{suffix}' ⇒ '{suffix_name}' ≠ типу документа '{doc_type_name}'"
            _add_violation(violations, int(page), bbox, "1.1.1", note, {
                "code": best_code.get("text"),
                "doc_type": doc_type_name
            })

    # --- 1.1.4: «на поле есть, в ТТ нет» — обводим найденное на поле ---
    rep_112 = out.get("1.1.2") or {}
    rep_114 = out.get("1.1.4") or {}
    try:
        doc = fitz.open(pdf_path)
        try:
            for page_idx, page_info in (rep_114.get("pages") or {}).items():
                p = int(page_idx)
                tokens = page_info.get("missing_in_tt") or []
                if not tokens:
                    continue
                page = doc[p - 1]
                tt_rect = _union_tt_bbox(rep_112, p)
                for token in tokens:
                    for r in page.search_for(token) or []:
                        if tt_rect and r.intersects(tt_rect):
                            continue
                        _add_violation(
                            violations, p, [r.x0, r.y0, r.x1, r.y1],
                            "1.1.4", f"На поле присутствует '{token}', но в ТТ отсутствует",
                            {"token": token}
                        )
        finally:
            doc.close()
    except Exception:
        pass

        # --- 1.1.3: на поле обнаружены лишние буквенные обозначения (extra_on_field) — обводим эти буквы ---
    rep_113 = out.get("1.1.3") or {}
    rep_112 = out.get("1.1.2") or {}
    try:
        doc = fitz.open(pdf_path)
        try:
            for page_idx, page_info in (rep_113.get("pages") or {}).items():
                p = int(page_idx)
                extra_letters = page_info.get("extra_on_field") or []
                if not extra_letters:
                    continue

                page = doc[p - 1]
                tt_rect = _union_tt_bbox(rep_112, p)

                # берём слова со страницы и ищем РОВНО одну букву
                words = page.get_text("words")  # (x0,y0,x1,y1, "text", block, line, word_no)
                for (x0, y0, x1, y1, w, *_rest) in words:
                    text = str(w).strip()
                    # только одна кириллическая буква
                    if not text or len(text) != 1:
                        continue
                    if not fitz.re.match(r"[А-Яа-яЁё]$", text):
                        continue

                    up = text.upper()
                    if up not in {str(L).upper() for L in extra_letters}:
                        continue

                    r = fitz.Rect(x0, y0, x1, y1)
                    # исключаем ТТ
                    if tt_rect and r.intersects(tt_rect):
                        continue

                    _add_violation(
                        violations, p, [r.x0, r.y0, r.x1, r.y1],
                        "1.1.3", f"Буква «{up}» присутствует на поле, но в ТТ не используется",
                        {"letter": up}
                    )
        finally:
            doc.close()
    except Exception:
        pass


        # --- 1.1.9: Ra без знака √ в скобках — подсвечиваем конкретные Ra (эвристика по тексту) ---
    rep_119 = out.get("1.1.9") or {}
    if rep_119.get("ok") is False:
        try:
            doc = fitz.open(pdf_path)
            try:
                # при желании можно пройтись по всем страницам; пока 1-я страница — чаще всего там легенда/титулы
                for p in range(1, len(doc) + 1):
                    page = doc[p - 1]
                    rects = find_ra_without_check(page)
                    for r in rects:
                        _add_violation(
                            violations, p, [r.x0, r.y0, r.x1, r.y1],
                            "1.1.9", rep_119.get("comment") or "Ra без знака √ в скобках",
                            {"detector": "text-heuristic", "feature": "Ra"}
                        )
            finally:
                doc.close()
        except Exception:
            pass

    # --- 1.1.7: доп. стрелка у допусков формы/расположения — подсветим контрольные рамки как подозрительные ---
    rep_117 = out.get("1.1.7") or {}
    if rep_117.get("ok") is False:
        try:
            doc = fitz.open(pdf_path)
            try:
                for p in range(1, len(doc) + 1):
                    page = doc[p - 1]
                    rects = find_gdt_frames(page)
                    for r in rects:
                        _add_violation(
                            violations, p, [r.x0, r.y0, r.x1, r.y1],
                            "1.1.7", rep_117.get("comment") or "Проверьте наличие дополнительной стрелки",
                            {"detector": "text-heuristic", "feature": "gdt-frame"}
                        )
            finally:
                doc.close()
        except Exception:
            pass



    # --- 1.1.5 ---
    rep_115 = out.get("1.1.5") or {}
    for page_idx, page_block in (rep_115.get("pages") or {}).items():
        for v in page_block.get("violations") or []:
            bbox = v["bbox"]
            txt = v.get("text") or ""
            tilt = v.get("tilt_deg")
            thr = rep_115.get("threshold_deg")
            note = f"Наклон {tilt}° > порога {thr}° — «{txt}»"
            _add_violation(violations, int(page_idx), bbox, "1.1.5", note, v)

    # --- 1.1.6 ---
    rep_116 = out.get("1.1.6") or {}
    for page_idx, page_block in (rep_116.get("pages") or {}).items():
        for v in page_block.get("violations") or []:
            bbox = v["bbox"]
            txt = v.get("text") or ""
            tilt = v.get("tilt_deg")
            thr = rep_116.get("threshold_deg")
            note = f"Наклон {tilt}° > порога {thr}° — «{txt}»"
            _add_violation(violations, int(page_idx), bbox, "1.1.6", note, v)

    return violations


def merge_violations(violations: List[Dict[str, Any]],
                     iou_threshold: float = 0.30,
                     dist_threshold: float = 8.0) -> List[Dict[str, Any]]:
    """
    Объединяет близкие/перекрывающиеся нарушения в кластеры.
    Дополнительно внутри кластера схлопывает 1.1.5/1.1.6 по одному и тому же объекту (тексту).
    """
    by_page: Dict[int, List[Dict[str, Any]]] = {}
    for v in violations:
        by_page.setdefault(v["page"], []).append(v)

    merged_all: List[Dict[str, Any]] = []

    for page, items in by_page.items():
        used = [False] * len(items)
        for i, v in enumerate(items):
            if used[i]:
                continue
            cluster_rect = fitz.Rect(*v["bbox"])
            cluster_idx = [i]
            changed = True
            while changed:
                changed = False
                for j, w in enumerate(items):
                    if used[j] or j in cluster_idx:
                        continue
                    r2 = fitz.Rect(*w["bbox"])
                    if _iou(cluster_rect, r2) >= iou_threshold or _rect_distance(cluster_rect, r2) < dist_threshold:
                        cluster_idx.append(j)
                        cluster_rect = cluster_rect | r2
                        changed = True
            for idx in cluster_idx:
                used[idx] = True

            # --- внутри кластера собираем элементы и схлопываем дубли ---
            # 1) сначала сгруппируем 1.1.5/1.1.6 по объекту (тексту)
            groups_by_obj: Dict[tuple, List[dict]] = {}
            plain_items: List[dict] = []

            for idx in cluster_idx:
                it = items[idx]
                gk = _dim_group_key(it)
                if gk is not None:
                    groups_by_obj.setdefault(gk, []).append(it)
                else:
                    plain_items.append(it)

            merged_items: List[dict] = []

            # 1a) для каждой DIM-группы выбираем «предпочтительный» пункт и описание
            for gk, members in groups_by_obj.items():
                crits = {m["criterion"] for m in members}
                chosen_crit = _prefer_criterion(crits)  # 1.1.5 > 1.1.6
                # выберем note от 1.1.5 если есть, иначе первый попавшийся
                note_515 = next((m["note"] for m in members if m["criterion"] == "1.1.5"), None)
                chosen_note = note_515 if note_515 is not None else members[0]["note"]
                # meta тоже возьмём у 1.1.5 если есть
                meta_515 = next((m.get("meta", {}) for m in members if m["criterion"] == "1.1.5"), {})
                chosen_meta = meta_515 if meta_515 else (members[0].get("meta", {}) or {})
                merged_items.append({"criterion": chosen_crit, "note": chosen_note, "meta": chosen_meta})

            # 1b) добавляем остальные пункты и убираем дословные дубли по (criterion, note)
            unique_pairs = {}
            for it in plain_items + merged_items:
                c = it["criterion"]; n = it["note"]
                unique_pairs[(c, n)] = {"criterion": c, "note": n, "meta": it.get("meta", {})}

            # финальные поля кластера
            criteria = sorted({p["criterion"] for p in unique_pairs.values()})
            out_items = list(unique_pairs.values())

            merged_all.append({
                "page": page,
                "bbox": [round(cluster_rect.x0, 2), round(cluster_rect.y0, 2),
                         round(cluster_rect.x1, 2), round(cluster_rect.y1, 2)],
                "criteria": criteria,
                "items": out_items,
            })

    merged_all.sort(key=lambda x: (x["page"], x["bbox"][1], x["bbox"][0]))
    return merged_all

def make_report_files(pdf_path: str, doc_id: int = None) -> tuple[Path, Path]:
    """
    Делает PDF с обводкой (после объединения) и TXT-реестр (без дублей).
    Номера и пункты выводятся максимально явно.
    """
    pipeline_out = pipeline(pdf_path)
    base_violations = collect_violations(pdf_path, pipeline_out)
    merged = merge_violations(base_violations)

    src = Path(pdf_path)
    annotated_path = src.with_suffix(".annotated.pdf")
    txt_path = src.with_suffix(".report.txt")

    # --- PDF ---
    doc = fitz.open(pdf_path)
    try:
        for num, v in enumerate(merged, start=1):
            p = v["page"]
            r = fitz.Rect(*v["bbox"])
            label = f"No {num}: n." + ", ".join(v["criteria"])
            page = doc[p - 1]
            # толще рамка и крупнее шрифт
            page.draw_rect(r, color=(1, 0, 0), width=2.2)
            y_text = r.y0 - 14 if r.y0 >= 18 else (r.y0 + 14)
            page.insert_text((r.x0, y_text), label, fontsize=20, color=(1, 0, 0))
            v["num"] = num
    finally:
        doc.save(annotated_path)
        doc.close()

    # --- TXT ---
    lines: List[str] = []
    lines.append(f"Файл: {src.name}")
    lines.append(f"Всего нарушений (кластеров): {len(merged)}")
    lines.append("")

    for v in merged:
        bbox = v["bbox"]
        lines.append(f"[#{v['num']:03d}] страница {v['page']}")
        lines.append(f"  Пункты: " + ",".join(v["criteria"]))
        lines.append("  Описания:")
        order = {c: i for i, c in enumerate(v["criteria"])}
        v["items"].sort(key=lambda it: (order.get(it["criterion"], 999), it["note"]))
        # уникальность уже обеспечена на этапе merge_violations
        for it in v["items"]:
            lines.append(f"   - ({it['criterion']}) {it['note']}")
        lines.append("")

    # инфо про отсутствия (1.1.4)
    rep_114 = pipeline_out.get("1.1.4") or {}
    for page_idx, page_block in (rep_114.get("pages") or {}).items():
        missing_on_field = page_block.get("missing_on_field") or []
        if missing_on_field:
            lines.append(
                f"[инфо] 1.1.4: в ТТ есть {missing_on_field}, на поле не найдено (обводка не ставится)."
            )
        # ---- инфо про "лишние" буквы на поле для 1.1.3 (extra_on_field) ----
    rep_113 = pipeline_out.get("1.1.3") or {}
    for page_idx, page_block in (rep_113.get("pages") or {}).items():
        extra_on_field = page_block.get("extra_on_field") or []
        if extra_on_field:
            # красивый список: «В» или «В, Г»
            letters = ", ".join(map(str, extra_on_field))
            # единичное/множественное
            if len(extra_on_field) == 1:
                lines.append(
                    f"[инфо] 1.1.3: буква «{letters}» присутствует на поле, но в ТТ не используется."
                )
            else:
                lines.append(
                    f"[инфо] 1.1.3: буквы «{letters}» присутствуют на поле, но в ТТ не используются."
                )

        # ---- Глобальные несоответствия без координат: 1.1.7 и 1.1.9 ----
    for rule in ("1.1.7", "1.1.9"):
        rep = pipeline_out.get(rule) or {}
        ok = rep.get("ok", None)
        comment = rep.get("comment") or ""
        if ok is None and comment:
            # Не ошибка, а информативная запись, почему проверка не выполнена
            lines.append(f"[инфо] {rule}: {comment}")


    Path(txt_path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    if doc_id:
        # Обновляем запись в БД
        with SessionLocal() as db:
            update_document_analysis(db, doc_id, annotated_path, txt_path)

    return annotated_path, txt_path
# ---------- CLI ----------
if __name__ == "__main__":
    pdf = "/home/user/atomichack_3.0/dataset/Для отправки_02102025/1.1.5/АБВГ.123456.001 правильно.pdf"
    # out = pipeline(pdf)
    # print(out)

    ann_pdf, txt = make_report_files(pdf)
    print(f"Готово: {ann_pdf}\nОтчёт: {txt}")

