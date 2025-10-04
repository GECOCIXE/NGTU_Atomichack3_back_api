*Backend часть системы для автоматической проверки технических PDF-документов на соответствие стандартам (ГОСТ, критерии 1.1.x). Разработана для хакатона AtomicHack 3 в НГТУ.*

[![Python](https://img.shields.io/badge/Python-3.12-blue?logo=python)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/FastAPI-0.104-green?logo=fastapi)](https://fastapi.tiangolo.com/)
## **Описание**

Backend, построенный на **FastAPI**, отвечает за API, аутентификацию, работу с базой данных и анализ PDF-документов. Он обрабатывает загрузку файлов, проверяет их на соответствие критериям (например, шифр документа, размеры, надписи), генерирует аннотированные PDF и отчеты, а также управляет историей проверок.

### **Основные функции**
- **API**: Роутеры для аутентификации (`/login`, `/reg`), загрузки (`/upload`), истории (`/history`), результатов (`/result/{doc_id}`) и скачивания файлов (`/download/{doc_id}`, `/download_annotated/{doc_id}`).
- **Анализ PDF**: Использует PyMuPDF для извлечения текста и аннотаций, с критериями, заданными в `scripts/analysis/config.yaml`.
- **Аутентификация**: JWT-токены для защиты маршрутов.
- **База данных**: SQLite (с поддержкой PostgreSQL) через SQLAlchemy.
- **Отчеты**: Парсинг и форматирование результатов анализа (нарушения, их количество, полные описания).

## **Технологии**
- **Framework**: FastAPI.
- **База данных**: SQLAlchemy с SQLite.
- **PDF-анализ**: PyMuPDF (fitz).
- **Аутентификация**: python-jose (JWT).
- **Конфигурация**: YAML (критерии анализа).
- **Дополнительно**: python-dotenv, Rich для логирования, python-multipart для загрузки файлов.

## **Структура**

```plaintext
backend/
├── README.md                # Этот файл
├── app.py                   # Основное приложение FastAPI
├── main.py                  # Запуск uvicorn
├── requirements.txt         # Зависимости (pip install -r)
├── routers/                 # API-роутеры
│   ├── auth.py              # Аутентификация и регистрация
│   ├── dependencies.py      # Зависимости (например, текущий пользователь)
│   ├── download.py          # Скачивание оригиналов и аннотированных файлов
│   ├── history.py           # История проверок
│   ├── result.py            # Детальные результаты
│   └── upload.py            # Загрузка файлов
└── scripts/                 # Бизнес-логика
    ├── crud.py              # CRUD-операции с БД
    ├── db.py                # Подключение к БД
    ├── models.py            # Модели SQLAlchemy (User, Document)
    ├── parse_report.py      # Парсинг отчетов
    └── analysis/            # Анализ PDF
        ├── config.yaml      # Конфигурация критериев
        ├── criterion_*.py   # Проверки по критериям (1.1.1, 1.1.2_n и т.д.)
        ├── main.py          # Основной скрипт анализа
        ├── requirements.txt # Зависимости анализа
        └── test.py          # Тесты
```

## **Установка и Запуск**

### **Предварительные требования**
- **Python** 3.12+.
- **Git**.

### **Шаги**
1. **Клонируйте репозиторий** (или перейдите в `backend`):
   ```bash
   cd backend
   ```

2. **Создайте виртуальное окружение и установите зависимости**:
   ```bash
   python -m venv venv
   source venv/bin/activate  # или venv\Scripts\activate на Windows
   pip install -r requirements.txt
   ```

3. **Конфигурация**:
   - Создайте файл `.env`:
     ```plaintext
     SECRET_KEY=your_secret
     DATABASE_URL=sqlite:///./test.db
     ```

4. **Запустите сервер**:
   ```bash
   python main.py
   ```
   Сервер доступен на `http://0.0.0.0:8234`.

## **API Эндпоинты**

- **POST /login**: Аутентификация пользователя (возвращает JWT).
- **POST /reg**: Регистрация нового пользователя.
- **POST /upload**: Загрузка PDF для анализа (фоновый процесс).
- **GET /history**: История проверок пользователя.
- **GET /result/{doc_id}**: Детальный отчет по документу.
- **GET /download/{doc_id}**: Скачивание оригинального файла.
- **GET /download_annotated/{doc_id}**: Скачивание аннотированного PDF.

**Пример ответа `/result/{doc_id}`**:
```json
{
  "id": 1,
  "filename": "drawing.pdf",
  "upload_date": "2025-10-04T06:22:00",
  "error_points": ["1.1.1"],
  "error_counts": {"1.1.1": 1},
  "total_violations": 1,
  "full_report": "[#1] Критерий 1.1.1: Несоответствие шифра и типа документа\nПункты: 1.1.1\n- (1) Шифр 'ABC.123.456ВО' не соответствует типу 'Ведомость эксплуатационных документов'."
}
```

## **Использование**
- Настройте критерии анализа в `scripts/analysis/config.yaml` (например, regex для шифров документов).
- Загрузите PDF через `/upload`, отчеты сохраняются в `data/original/{doc_id}` и аннотированные файлы в `data/annotated/`.
- Используйте SQLite БД (`test.db`) для хранения пользователей и документов.

