from typing import Literal
from pydantic import BaseModel
from openai import OpenAI
import base64
import os
import dotenv

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
        "reference_image": "scripts/analysis/ref-1.1.9-correct.png"  # Изображение, где правило 1.1.9 выполнено ВЕРНО
    },
    "1.1.7": {
        "description": "Правило ГОСТ 1.1.7 (по ГОСТ 2.308): Проверка наличия дополнительной стрелки при простановке допусков формы и расположения.",
        "reference_image": "scripts/analysis/ref-1.1.7-correct.png"  # Изображение, где правило 1.1.7 выполнено ВЕРНО
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
    model: str = "qwen/qwen2.5-vl-32b-instruct:free",
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

    parsed_object: GostResult = resp.choices[0].message.parsed
    return parsed_object.dict()

# === ОБНОВЛЕННЫЙ Пример использования ===
if __name__ == "__main__":
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise ValueError("Не найден ключ OPENROUTER_API_KEY. Проверьте ваш .env файл.")

    # Предположим, у вас есть один чертеж на проверку и два эталонных изображения
    # для каждого из правил.
    # Убедитесь, что эти файлы существуют в папке с вашим скриптом!
    CANDIDATE_FILE = "РНАТ.123456.001МЧ.png" # Проверяемый чертеж

    print(f"--- Проверка файла '{CANDIDATE_FILE}' ---")

    try:
        # 1. Проверка по правилу 1.1.9
        print("\n[Запуск] Проверка по ГОСТ 1.1.9 (знак шероховатости)...")
        result_9 = check_gost("1.1.9", CANDIDATE_FILE, api_key)
        print(f"[Результат] {result_9}")

        # 2. Проверка по правилу 1.1.7
        print("\n[Запуск] Проверка по ГОСТ 1.1.7 (дополнительная стрелка)...")
        result_7 = check_gost("1.1.7", CANDIDATE_FILE, api_key)
        print(f"[Результат] {result_7}")

    except FileNotFoundError as e:
        print(f"\nОшибка! Не удалось найти файл изображения: {e}")
        print("Пожалуйста, убедитесь, что файлы 'РНАТ.123456.001МЧ.png', 'ref-1.1.9-correct.png' и 'ref-1.1.7-correct.png' находятся в той же папке, что и скрипт.")
    except Exception as e:
        print(f"Произошла непредвиденная ошибка: {e}")
