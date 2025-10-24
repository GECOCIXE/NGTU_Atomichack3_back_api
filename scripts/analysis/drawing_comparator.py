import argparse
import os
from pydantic import BaseModel, Field
from typing import Optional
import fitz  # PyMuPDF
import base64
from openai import OpenAI
from dotenv import load_dotenv
load_dotenv()

class DrawingComparisonResult(BaseModel):
    """Модель результата сравнения чертежей"""
    similar: bool = Field(..., description="Похожи ли чертежи")
    confidence: float = Field(..., description="Уровень уверенности в результате (0-1)")


def _pdf_first_page_to_png(pdf_path: str, dpi: int = 150) -> Optional[str]:
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
        out_png = pdf_path.replace('.pdf', '.page1.png')
        pix.save(out_png)
        doc.close()
        return out_png
    except Exception:
        return None


def _file_to_data_uri(path: str) -> str:
    """Конвертирует файл в Data URI"""
    ext = os.path.splitext(path)[1].lower().lstrip(".")
    mime = {
        "png": "image/png",
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "bmp": "image/bmp",
        "gif": "image/gif",
    }.get(ext, "image/png")
    
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден по пути: {path}")
    
    with open(path, "rb") as f:
        data = f.read()
    
    return f"data:{mime};base64," + base64.b64encode(data).decode("ascii")


def compare_drawings(pdf_path1: str, pdf_path2: str, api_key: Optional[str] = None) -> DrawingComparisonResult:
    """
    Сравнивает два PDF-файла по визуальному содержанию чертежей с использованием OpenRouter API.
    
    Args:
        pdf_path1: Путь к первому PDF-файлу
        pdf_path2: Путь к второму PDF-файлу
        api_key: API-ключ для OpenRouter (если не указан, будет использована переменная окружения)
    
    Returns:
        DrawingComparisonResult: Результат сравнения
    """
    if not api_key:
        api_key = os.getenv("OPENROUTER_API_KEY")
    
    if not api_key:
        raise ValueError("API-ключ не предоставлен. Установите переменную OPENROUTER_API_KEY или передайте ключ явно.")
    
    # Конвертируем PDF-файлы в PNG
    png1_path = _pdf_first_page_to_png(pdf_path1)
    png2_path = _pdf_first_page_to_png(pdf_path2)
    
    if not png1_path or not png2_path:
        raise ValueError("Не удалось конвертировать PDF в изображения.")
    
    try:
        # Подготовка изображений как Data URI
        image1_uri = _file_to_data_uri(png1_path)
        image2_uri = _file_to_data_uri(png2_path)
        
        # Создаем OpenAI клиент
        client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
        
        # Подготовка сообщения
        user_msg = "Сравни два чертежа и определи, насколько они визуально похожи. Верни результат в формате JSON с полями: similar (true/false) и confidence (0-1). расположение тоже влияет если чертёж повёрнут или немного отличается форма то false"
        
        content_parts = [{"type": "text", "text": user_msg}]
        
        # Добавляем изображения
        content_parts.append({"type": "image_url", "image_url": {"url": image1_uri}})
        content_parts.append({"type": "image_url", "image_url": {"url": image2_uri}})
        
        # Вызов API с использованием parse для автоматического парсинга в Pydantic модель
        resp = client.chat.completions.parse(
            model="qwen/qwen3-vl-8b-instruct",
            messages=[
                {"role": "system", "content": "Ты эксперт по сравнению технических чертежей."},
                {"role": "user", "content": content_parts},
            ],
            response_format=DrawingComparisonResult,
            temperature=0,
            max_tokens=500,
        )
        
        # Получаем результат напрямую как Pydantic модель
        result: DrawingComparisonResult = resp.choices[0].message.parsed
        return result
        
    finally:
        # Удаление временных файлов
        try:
            if png1_path and os.path.exists(png1_path):
                os.remove(png1_path)
            if png2_path and os.path.exists(png2_path):
                os.remove(png2_path)
        except:
            pass  # Не критично, если не удалось удалить временные файлы


def main():
    parser = argparse.ArgumentParser(description="Сравнение двух PDF-файлов по визуальному содержанию чертежей")
    parser.add_argument("pdf1", help="Путь к первому PDF-файлу")
    parser.add_argument("pdf2", help="Путь ко второму PDF-файлу")
    parser.add_argument("--api-key", help="API-ключ для OpenRouter (если не указан, используется переменная окружения)")
    
    args = parser.parse_args()
    
    if not os.path.exists(args.pdf1):
        print(f"Ошибка: файл не найден - {args.pdf1}")
        return
    
    if not os.path.exists(args.pdf2):
        print(f"Ошибка: файл не найден - {args.pdf2}")
        return
    
    try:
        result = compare_drawings(args.pdf1, args.pdf2, args.api_key)
        print(f"Результат сравнения: {result.similar}")
        print(f"Уверенность: {result.confidence}")
    except Exception as e:
        print(f"Ошибка при сравнении: {e}")


if __name__ == "__main__":
    main()
