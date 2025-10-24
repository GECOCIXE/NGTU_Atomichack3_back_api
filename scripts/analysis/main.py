from pathlib import Path
import fitz  # PyMuPDF
from typing import List, Dict, Any
from rich import print
from .criterion_1_1_1 import extract_pdf_text_as_dict, filter_titleblock_items, load_config  # :contentReference[oaicite:2]{index=2}
from .criterion_1_1_2_n import run_check as run_check_1_1_2                                   # :contentReference[oaicite:3]{index=3}
from .criterion_1_1_3_n import check_letter_designations, extract_lines_with_bbox                                        # :contentReference[oaicite:4]{index=4}
from .criterion_1_1_4_n import check_stars                                                       # :contentReference[oaicite:5]{index=5}
from .criterion_1_1_5 import check as check_1_1_5                                              # :contentReference[oaicite:6]{index=6}
from .criterion_1_1_6 import check as check_1_1_6                                              # :contentReference[oaicite:7]{index=7}
from .criterion_1_1_8 import check_bases_vs_frames
import os
import re
from typing import Optional
from .multi_page_gost_checker import check_both_criteria_multi_page

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
    res_1_1_1 = filter_titleblock_items(out_1_1_1, load_config("D:\\Atomhack3\\NGTU_Atomichack3_back_api\\scripts\\analysis\\config.yaml"))
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

    result = check_both_criteria_multi_page(pdf_path, api_key)
    
    output["1.1.9"] = result["1.1.9"]
    output["1.1.7"] = result["1.1.7"]
    #output["1.1.3"] = _safe_check("1.1.3")

    return output


# ---------- ВСПОМОГАТЕЛЬНОЕ ----------
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
                page = doc[p - 1]
                tt_rect = _union_tt_bbox(rep_112, p)
                
                # Проверяем, есть ли в ТТ элементы, содержащие "**"
                has_double_stars = False
                for page_info_check in (rep_114.get("pages") or {}).values():
                    tt_lines = page_info_check.get("tt_lines", [])
                    for line in tt_lines:
                        if "**" in line:
                            has_double_stars = True
                            break
                    if has_double_stars:
                        break
                
                # Если в ТТ есть "**", то отдельный символ "*" не отмечаем как ошибку
                tokens_to_process = [t for t in tokens if not (has_double_stars and t.strip() == "*")]
                
                for token in tokens_to_process:
                    token_text = token.strip()
                    
                    # Если в ТТ есть "**", то элементы, заканчивающиеся на "**", не отмечаем как ошибку
                    if has_double_stars and token_text.endswith("**"):
                        continue
                    
                    for r in page.search_for(token) or []:
                        if tt_rect and r.intersects(tt_rect):
                            continue
                        _add_violation(
                            violations, p, [r.x0, r.y0, r.x1, r.y1],
                            "1.1.4", f"На поле присутствует '{token}', но в ТТ отсутствует",
                            {"token": token}
                        )
                
                # Дополнительно: если в ТТ есть "**", проверяем, есть ли на поле элементы с одиночной "*",
                # которые не отмечены в missing_in_tt, но должны быть отмечены как ошибки
                if has_double_stars:
                    field_lines = page_info.get("field_lines", [])
                    # Ищем элементы, содержащие "*", но не "**" (например, "20*", "40*", "10*")
                    single_star_elements = [line for line in field_lines if "*" in line and "**" not in line and line.count("*") == 1]
                    
                    for element in single_star_elements:
                        # Проверяем, есть ли уже нарушение для этого элемента
                        element_exists = any(
                            v.get("meta", {}).get("token") == element and v["page"] == p and v["criterion"] == "1.1.4"
                            for v in violations
                        )
                        
                        if not element_exists:
                            # Ищем bbox для этого элемента на странице
                            search_results = page.search_for(element) or []
                            for r in search_results:
                                if tt_rect and r.intersects(tt_rect):
                                    continue
                                _add_violation(
                                    violations, p, [r.x0, r.y0, r.x1, r.y1],
                                    "1.1.4", f"На поле присутствует '{element}', но в ТТ отсутствует",
                                    {"token": element}
                                )
        finally:
            doc.close()
    except Exception:
        pass

        # --- 1.1.3: на поле обнаружены лишние буквенные обозначения (extra_on_field) — обводим эти буквы ---
    rep_113 = out.get("1.1.3") or {}
    rep_112 = out.get("1.1.2") or {}
    
    # Для корректной обработки обозначений сечений, нужно проверить все строки на всех страницах
    all_lines_by_page = extract_lines_with_bbox(pdf_path)
    
    try:
        doc = fitz.open(pdf_path)
        try:
            # Находим все обозначения сечений во всем документе (например, "А-А", "Б-Б")
            all_section_letters = set()
            for page_num, page_lines in all_lines_by_page.items():
                for line in page_lines:
                    text = line["text"].strip()
                    # Проверяем форматы обозначений сечений: "А-А", "Б-Б", "В-В" и т.д.
                    section_match = re.match(r'^[А-Яа-яA-Za-z]-[А-Яа-яA-Za-z]$', text)
                    if section_match:
                        # Извлекаем буквы из обозначения сечения
                        letters_in_section = re.findall(r'[А-Яа-яA-Za-z]', text)
                        all_section_letters.update(letters_in_section)
            
            for page_idx, page_info in (rep_113.get("pages") or {}).items():
                p = int(page_idx)
                extra_letter_bboxes = page_info.get("extra_letter_bboxes") or []
                if not extra_letter_bboxes:
                    continue

                page = doc[p - 1]
                tt_rect = _union_tt_bbox(rep_112, p)
                
                # Получаем все строки на текущей странице
                all_page_lines = all_lines_by_page.get(p, [])

                # используем точные координаты из extra_letter_bboxes
                for letter_info in extra_letter_bboxes:
                    text = letter_info["text"]
                    bbox = letter_info["bbox"]
                    
                    # Если буква является частью обозначения сечения в документе, 
                    # не отмечаем её как ошибку
                    if text in all_section_letters:
                        continue
                    
                    r = fitz.Rect(*bbox)
                    # исключаем ТТ
                    if tt_rect and r.intersects(tt_rect):
                        continue

                    _add_violation(
                        violations, p, [r.x0, r.y0, r.x1, r.y1],
                        "1.1.3", f"Буква «{text}» присутствует на поле, но в ТТ не используется",
                        {"letter": text}
                    )
        finally:
            doc.close()
    except Exception:
        pass


    # --- 1.1.5 ---
    rep_115 = out.get("1.1.5") or {}
    # Получаем название документа из 1.1.1, чтобы исключить его из 1.1.5
    doc_names_111 = set()
    rep_111 = out.get("1.1.1") or {}
    for page, items in (rep_111 or {}).items():
        if not isinstance(items, list):
            continue
        for it in items:
            if "doc_type_name" in it:
                doc_names_111.add(it["doc_type_name"])
            if "text" in it and "doc_type_name" not in it:  # Это может быть название документа
                doc_names_111.add(it["text"])
    
    for page_idx, page_block in (rep_115.get("pages") or {}).items():
        for v in page_block.get("violations") or []:
            txt = v.get("text") or ""
            # Исключаем название документа из проверки 1.1.5
            if txt in doc_names_111:
                continue
            bbox = v["bbox"]
            tilt = v.get("tilt_deg")
            thr = rep_115.get("threshold_deg")
            note = f"Наклон {tilt}° > порога {thr}° — «{txt}»"
            _add_violation(violations, int(page_idx), bbox, "1.1.5", note, v)

    # --- 1.1.6 ---
    rep_116 = out.get("1.1.6") or {}
    for page_idx, page_block in (rep_116.get("pages") or {}).items():
        for v in page_block.get("violations") or []:
            txt = v.get("text") or ""
            # Исключаем название документа из проверки 1.1.6
            if txt in doc_names_111:
                continue
            bbox = v["bbox"]
            tilt = v.get("tilt_deg")
            thr = rep_116.get("threshold_deg")
            note = f"Наклон {tilt}° > порога {thr}° — «{txt}»"
            _add_violation(violations, int(page_idx), bbox, "1.1.6", note, v)

    # --- 1.1.8: отсутствующие базы в рамках ---
    rep_118 = out.get("1.1.8") or {}
    try:
        doc = fitz.open(pdf_path)
        try:
            for page_idx, page_info in (rep_118.get("pages") or {}).items():
                p = int(page_idx)
                missing_bases = page_info.get("missing_bases") or []
                if not missing_bases:
                    continue

                page = doc[p - 1]
                frames_found = page_info.get("frames_found") or []
                
                # Для каждой отсутствующей базы ищем соответствующую рамку
                for base in missing_bases:
                    # Ищем текст с этой базой в рамках
                    for frame_text in frames_found:
                        if base in frame_text:
                            # Ищем координаты этого текста на странице
                            search_results = page.search_for(frame_text) or []
                            for r in search_results:
                                _add_violation(
                                    violations, p, [r.x0, r.y0, r.x1, r.y1],
                                    "1.1.8", f"База «{base}» отсутствует, но используется в рамке «{frame_text}»",
                                    {"base": base, "frame": frame_text}
                                )
        finally:
            doc.close()
    except Exception:
        pass

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
                    # Не объединяем 1.1.3 и 1.1.8 в один кластер
                    is_113_or_118_v = v["criterion"] in ["1.1.3", "1.1.8"]
                    is_113_or_118_w = w["criterion"] in ["1.1.3", "1.1.8"]
                    if is_113_or_118_v and is_113_or_118_w and v["criterion"] != w["criterion"]:
                        # Это нарушения 1.1.3 и 1.1.8 - не объединяем их
                        continue
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
                # Не объединяем 1.1.3 и 1.1.8, так как это разные типы нарушений
                if c in ["1.1.3", "1.1.8"]:
                    # Для 1.1.3 и 1.1.8 создаем отдельные записи
                    unique_pairs[(c, n)] = {"criterion": c, "note": n, "meta": it.get("meta", {})}
                else:
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

def make_report_files(pdf_path: str, pipeline_out: dict) -> tuple[Path, Path]:
    """
    Делает PDF с обводкой (после объединения) и TXT-реестр (без дублей).
    Номера и пункты выводятся максимально явно.
    Также создает отдельные PDF отчеты для каждого критерия.
    """
    base_violations = collect_violations(pdf_path, pipeline_out)
    merged = merge_violations(base_violations)

    src = Path(pdf_path)
    annotated_path = src.with_suffix(".annotated.pdf")
    txt_path = src.with_suffix(".report.txt")

    # --- Создание общего PDF ---
    doc = fitz.open(pdf_path)
    try:
        for num, v in enumerate(merged, start=1):
            p = v["page"]
            r = fitz.Rect(*v["bbox"])
            label = f"No {num}: n." + ", ".join(v["criteria"])
            page = doc[p - 1]
            
            # Используем красный цвет для всех нарушений
            color = (1, 0, 0)  # Красный
            
            # Улучшенная отрисовка с учетом положения на странице
            # Используем красный цвет и увеличенную толщину линии
            page.draw_rect(r, color=color, width=3)  # Увеличена толщина линии
            
            # Позиционируем текст над прямоугольником, учитывая края страницы
            text_height = 12  # Высота текста
            text_rect = page.rect  # Границы страницы
            
            # Проверяем, достаточно ли места над прямоугольником для текста
            if r.y0 - text_height >= 0:
                # Текст над прямоугольником
                text_pos = (r.x0, r.y0 - 2)
            else:
                # Текст под прямоугольником
                text_pos = (r.x0, r.y1 + text_height + 2)
            
            # Дополнительная проверка: если текст выходит за пределы страницы, сдвигаем его внутрь
            if text_pos[1] < 0:
                text_pos = (r.x0, r.y1 + text_height + 2)
            elif text_pos[1] > text_rect.height:
                text_pos = (r.x0, r.y0 - 2)
            
            # Вставляем текст красного цвета с улучшенным шрифтом
            page.insert_text(text_pos, label, fontsize=12, color=color, fontname="helv")
            
            v["num"] = num
    finally:
        doc.save(annotated_path)
        doc.close()

    # --- Создание отдельных PDF отчетов для каждого критерия ---
    for criterion in ["1.1.1", "1.1.2", "1.1.3", "1.1.4", "1.1.5", "1.1.6", "1.1.8"]:
        # Найдем все нарушения, содержащие данный критерий, с сохранением оригинальной нумерации
        criterion_violations = []
        for v in merged:
            if criterion in v["criteria"]:
                # Сохраняем оригинальный номер из общего отчета
                v_with_original_num = v.copy()
                criterion_violations.append(v_with_original_num)
        
        if criterion_violations:  # Создаем файл только если есть нарушения по критерию
            criterion_pdf_path = src.with_name(f"{src.stem}.{criterion}.pdf")
            doc = fitz.open(pdf_path)
            try:
                for v in criterion_violations:
                    p = v["page"]
                    r = fitz.Rect(*v["bbox"])
                    # Используем оригинальный номер из общего отчета
                    label = f"No {v['num']:03d}: {criterion}"
                    page = doc[p - 1]
                    
                    # Используем красный цвет для всех критериев
                    color = (1, 0, 0)  # Красный
                    
                    page.draw_rect(r, color=color, width=3)
                    
                    # Позиционируем текст над прямоугольником, учитывая края страницы
                    text_height = 12  # Высота текста
                    text_rect = page.rect  # Границы страницы
                    
                    # Проверяем, достаточно ли места над прямоугольником для текста
                    if r.y0 - text_height >= 0:
                        # Текст над прямоугольником
                        text_pos = (r.x0, r.y0 - 2)
                    else:
                        # Текст под прямоугольником
                        text_pos = (r.x0, r.y1 + text_height + 2)
                    
                    # Дополнительная проверка: если текст выходит за пределы страницы, сдвигаем его внутрь
                    if text_pos[1] < 0:
                        text_pos = (r.x0, r.y1 + text_height + 2)
                    elif text_pos[1] > text_rect.height:
                        text_pos = (r.x0, r.y0 - 2)
                    
                    # Вставляем текст с улучшенным шрифтом
                    page.insert_text(text_pos, label, fontsize=12, color=color, fontname="helv")
            finally:
                doc.save(criterion_pdf_path)
                doc.close()

    # --- Создание отдельных PDF отчетов для каждой конкретной ошибки ---
    for v in merged:
        error_num = v["num"]
        page_num = v["page"]
        criteria = v["criteria"]
        
        # Создаем отдельный PDF для каждой ошибки
        error_pdf_path = src.with_name(f"{src.stem}.error_{error_num:03d}.pdf")
        doc = fitz.open(pdf_path)
        try:
            p = page_num
            r = fitz.Rect(*v["bbox"])
            # Формируем метку с перечислением критериев
            criteria_str = ", ".join(criteria)
            label = f"No {error_num:03d}: {criteria_str}"
            page = doc[p - 1]
            
            # Используем красный цвет для всех ошибок
            color = (1, 0, 0)  # Красный
            
            page.draw_rect(r, color=color, width=3)
            
            # Позиционируем текст над прямоугольником, учитывая края страницы
            text_height = 12  # Высота текста
            text_rect = page.rect  # Границы страницы
            
            # Проверяем, достаточно ли места над прямоугольником для текста
            if r.y0 - text_height >= 0:
                # Текст над прямоугольником
                text_pos = (r.x0, r.y0 - 2)
            else:
                # Текст под прямоугольником
                text_pos = (r.x0, r.y1 + text_height + 2)
            
            # Дополнительная проверка: если текст выходит за пределы страницы, сдвигаем его внутрь
            if text_pos[1] < 0:
                text_pos = (r.x0, r.y1 + text_height + 2)
            elif text_pos[1] > text_rect.height:
                text_pos = (r.x0, r.y0 - 2)
            
            # Вставляем текст с улучшенным шрифтом
            page.insert_text(text_pos, label, fontsize=12, color=color, fontname="helv")
        finally:
            doc.save(error_pdf_path)
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
        if ok is False:
            # Явно фиксируем как нарушение, но без bbox
            lines.append(f"[GLOBAL] {rule}: {comment}")
        elif ok is None and comment:
            # Не ошибка, а информативная запись, почему проверка не выполнена
            lines.append(f"[инфо] {rule}: {comment}")


    Path(txt_path).write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return annotated_path, txt_path
# ---------- CLI ----------
if __name__ == "__main__":
    #pdf = "/home/user/atomichack_3.0/data/НГТУ Африкантов/Трейн датасет/РНАТ.123456.001СБ.pdf"
    #pdf = "./dataset/Для отправки_02102025/1.1.4/АБВГ.123456.001 правильно.pdf" 
    pdf = "./data/НГТУ Африкантов/Трейн датасет/РНАТ.123456.001СБ.pdf"
    out = pipeline(pdf)
    print(out)

    ann_pdf, txt = make_report_files(pdf, out)
    print(f"Готово: {ann_pdf}\nОтчёт: {txt}")
