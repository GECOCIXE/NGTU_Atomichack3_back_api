from typing import Literal
from pydantic import BaseModel
from openai import OpenAI
import base64
import os
import dotenv
import fitz  # PyMuPDF
from PIL import Image
import io

dotenv.load_dotenv()

# --- Pydantic класс под JSON, остается без изменений ---
class GostResult(BaseModel):
    ok: bool
    comment: str

# --- Хранилище правил ГОСТ ---
# Легко расширяемая структура. Чтобы добавить новое правило,
# просто добавьте новый элемент в этот словарь.
GOST_RULES = {
    "1.1.9": {
        "description": "Правило ГОСТ 1.1.9: Проверка наличия знака √ в скобках в углу шероховатости при наличии указанной шероховатости.",
        "reference_image": "./scripts/analysis/ref-1.1.7-correct.png"  # Изображение, где правило 1.1.9 выполнено ВЕРНО
    },
    "1.1.7": {
        "description": "Правило ГОСТ 2.308: Проверка наличия дополнительной стрелки при простановке допусков формы и расположения.",
        "reference_image": "./scripts/analysis/ref-1.1.7-correct.png"  # Изображение, где правило 1.1.7 выполнено ВЕРНО
    }
    # Можете добавлять сюда новые правила по аналогии
    # "номер_госта": { "description": "...", "reference_image": "..." }
}

# Используем Literal для автодополнения и статической проверки типов.
# Он автоматически возьмет ключи из нашего словаря.
GostRuleType = Literal[tuple(GOST_RULES.keys())]


def _is_url(path: str) -> bool:
    return path.startswith("http://") or path.startswith("https://")


def _file_to_data_uri(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "bmp": "image/bmp",
        "gif": "image/gif",
    }.get(ext, "image/png")
    # Добавим проверку на существование файла для более ясной ошибки
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден по пути: {path}")
    with open(path, "rb") as f:
        data = f.read()
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def check_gost(
    gost_rule: GostRuleType, # <-- ИЗМЕНЕНИЕ: принимаем номер правила
    candidate_image: str,
    api_key: str,
    model: str = "qwen/qwen2.5-vl-32b-instruct",
) -> dict:
    """
    Проверяет чертёж на соответствие указанному правилу ГОСТ.
    Возвращает словарь {"ok": bool, "comment": str}.
    """
    # 1. Проверка и получение данных о правиле из нашего хранилища
    if gost_rule not in GOST_RULES:
        raise ValueError(f"Неизвестное правило ГОСТ: {gost_rule}. Доступные правила: {list(GOST_RULES.keys())}")

    rule_data = GOST_RULES[gost_rule]
    user_msg = rule_data["description"]
    reference_image = rule_data["reference_image"] # <-- Путь к эталону берется из словаря

    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")

    system_msg = (
        "Ты эксперт по ГОСТ. Сравни эталонный и проверяемый чертеж по указанному правилу."
        " Верни JSON строго по схеме: {\"ok\": true/false, \"comment\": \"короткий комментарий\"}."
        " Комментарий должен быть очень сжатым (не более 25 слов)."
    )

    # 2. Формирование запроса к модели (логика осталась прежней)
    content_parts = [{"type": "text", "text": user_msg}]

    # Reference
    if _is_url(reference_image):
        content_parts.append({"type": "image_url", "image_url": {"url": reference_image}})
    else:
        content_parts.append({"type": "image_url", "image_url": {"url": _file_to_data_uri(reference_image)}})

    # Candidate
    if _is_url(candidate_image):
        content_parts.append({"type": "image_url", "image_url": {"url": candidate_image}})
    else:
        content_parts.append({"type": "image_url", "image_url": {"url": _file_to_data_uri(candidate_image)}})

    try:
        resp = client.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": content_parts},
            ],
            response_format=GostResult,
            temperature=0,
            max_tokens=80,
        )

        if resp and resp.choices and resp.choices[0] and resp.choices[0].message:
            parsed_object: GostResult = resp.choices[0].message.parsed
            if parsed_object is None:
                # Возвращаем стандартный результат при ошибке
                return {"ok": False, "comment": "Ошибка при анализе изображения"}
            return parsed_object.model_dump()  # Используем model_dump вместо dict как рекомендовано}
        else:
            return {"ok": False, "comment": "Нет ответа от API"}
    except Exception as e:
        print(f"Ошибка при вызове API: {e}")
        return {"ok": False, "comment": f"Ошибка API: {str(e)}"}


def convert_pdf_to_images(pdf_path: str, dpi: int = 150) -> list:
    """
    Конвертирует PDF в список изображений (по одному на страницу).
    """
    doc = fitz.open(pdf_path)
    images = []
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        mat = fitz.Matrix(dpi / 72, dpi / 72)  # 72 - стандартный DPI для PDF
        pix = page.get_pixmap(matrix=mat)
        
        # Конвертируем в PIL Image
        img_data = pix.tobytes("png")
        img = Image.open(io.BytesIO(img_data))
        images.append(img)
    
    doc.close()
    return images


def save_image_to_temp_file(image: Image.Image, prefix: str = "temp_page") -> str:
    """
    Сохраняет PIL изображение во временный файл и возвращает путь к нему.
    """
    import tempfile
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False, prefix=prefix) as temp_file:
        image.save(temp_file.name, format='PNG')
        return temp_file.name


def check_gost_multi_page(
    gost_rule: GostRuleType,
    pdf_path: str,
    api_key: str,
    model: str = "qwen/qwen2.5-vl-32b-instruct",
    dpi: int = 150
) -> dict:
    """
    Проверяет многостраничный PDF на соответствие указанному правилу ГОСТ.
    Возвращает словарь в формате как в test.py, где если хотя бы на одной странице результат false,
    то весь результат для критерия будет false.
    """
    # Конвертируем PDF в изображения
    images = convert_pdf_to_images(pdf_path, dpi)
    
    results = {
        "ok": True,  # Начальное значение True
        "comment": f"Критерий {gost_rule} пройден на всех страницах PDF",
        "pages_count": len(images),
        "pages": {}
    }
    
    all_comments = []
    
    for i, img in enumerate(images):
        # Сохраняем изображение во временный файл
        temp_img_path = save_image_to_temp_file(img, f"page_{i+1}_")
        
        try:
            # Проверяем страницу
            page_result = check_gost(gost_rule, temp_img_path, api_key, model)
            results["pages"][i+1] = {
                "page_number": i+1,
                "result": page_result
            }
            
            # Если хотя бы одна страница не прошла проверку, общий результат - False
            if not page_result["ok"]:
                results["ok"] = False
                all_comments.append(f"Стр. {i+1}: {page_result['comment']}")
                
        finally:
            # Удаляем временный файл
            os.unlink(temp_img_path)
    
    # Если были ошибки, формируем общий комментарий
    if not results["ok"]:
        results["comment"] = f"Критерий {gost_rule} не пройден: {', '.join(all_comments)}"
    
    return results


def check_both_criteria_multi_page(
    pdf_path: str,
    api_key: str,
    model: str = "qwen/qwen2.5-vl-32b-instruct",
    dpi: int = 150
) -> dict:
    """
    Проверяет многостраничный PDF по критериям 1.1.7 и 1.1.9.
    Возвращает словарь в формате как в test.py, где если хотя бы на одной странице результат false,
    то весь результат для критерия будет false.
    """
    results = {}
    
    for criterion in ["1.1.7", "1.1.9"]:
        results[criterion] = check_gost_multi_page(
            criterion, pdf_path, api_key, model, dpi
        )
    
    return results


# === Пример использования ===
if __name__ == "__main__":
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("Не найден ключ OPENROUTER_API_KEY. Проверьте ваш .env файл.")

    # Путь к PDF-файлу для проверки
    PDF_FILE = "/home/user/atomichack_3.0/data/НГТУ Африкантов/Трейн датасет/РНАТ.123456.005УЧ.annotated.pdf"

    print(f"--- Проверка многостраничного файла '{PDF_FILE}' ---")

    try:
        # Проверка по обоим критериям
        results = check_both_criteria_multi_page(PDF_FILE, api_key)
        
        for criterion, criterion_results in results.items():
            status = "ПРОЙДЕНО" if criterion_results["ok"] else "НЕ ПРОЙДЕНО"
            print(f"\n[Результаты] Проверка по ГОСТ {criterion}: {status}")
            print(f"  Комментарий: {criterion_results['comment']}")
            print(f"  Количество страниц: {criterion_results['pages_count']}")
            
            # Выводим результаты по страницам
            for page_num, page_data in criterion_results["pages"].items():
                page_result = page_data["result"]
                page_status = "ПРОЙДЕНО" if page_result["ok"] else "НЕ ПРОЙДЕНО"
                print(f"    Страница {page_num}: {page_status} - {page_result['comment']}")

    except FileNotFoundError as e:
        print(f"\nОшибка! Не удалось найти файл: {e}")
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")