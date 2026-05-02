# Tender Alchemist

Интеллектуальная система автоматизации подготовки тендерных предложений для ИТ-оборудования. Принимает на вход техническое задание (DOCX/HTML), извлекает из него список товаров с характеристиками и НМЦК, а затем ищет точные соответствия в прайс-листах поставщиков через гибридный векторный поиск (Dense + Sparse, RRF) с финальной валидацией через LLM.

---

## Содержание

1. [Как это работает](#как-это-работает)
2. [Архитектура](#архитектура)
3. [Стек технологий](#стек-технологий)
4. [Требования](#требования)
5. [Быстрый старт (локально)](#быстрый-старт-локально)
6. [Запуск через Docker Compose](#запуск-через-docker-compose)
7. [Настройка окружения](#настройка-окружения)
8. [Индексация каталога e2e4](#индексация-каталога-e2e4)
9. [Работа в интерфейсе](#работа-в-интерфейсе)
10. [Автоматическое обновление каталога](#автоматическое-обновление-каталога)
11. [Структура проекта](#структура-проекта)
12. [Переменные окружения](#переменные-окружения)
13. [Разработка и тесты](#разработка-и-тесты)

---

## Как это работает

Обработка одного тендерного документа проходит **5 этапов**:

```
Документ (DOCX/HTML)
        │
        ▼
 Этап 1 │ Парсинг файла
        │ pandoc/docling → HTML → очищенный текст
        │
        ▼
 Этап 2 │ Сборка prompt
        │ Системный промпт + текст документа
        │
        ▼
 Этап 3 │ LLM-извлечение (ministral-3:3b)
        │ Возвращает JSON: список товаров,
        │ характеристики, НМЦК на каждый лот
        │
        ▼
 Этап 4 │ Нормализация
        │ Приведение единиц измерения, категорий,
        │ числовых значений к стандартному виду
        │
        ▼
 Этап 5 │ Поиск в Qdrant (гибридный)
        │  ├── LLM-дистилляция запроса (qwen2.5:1.5b)
        │  ├── Dense + Sparse векторы (FastEmbed)
        │  ├── RRF fusion + фильтр по цене ≤ НМЦК
        │  └── LLM-судья: ДА/НЕТ для топ-кандидатов
        │
        ▼
  Результат: подобранные позиции прайс-листа
             с RRF-скором, ценой и вердиктом LLM
```

---

## Архитектура

```
┌─────────────────────────────────────────────────────────────┐
│                        Docker Compose                        │
│                                                              │
│  ┌──────────┐   ┌───────────────┐   ┌──────────────────┐   │
│  │  Ollama  │   │ docling-serve │   │     Qdrant        │   │
│  │:11434    │   │    :5001      │   │  :6333 (REST)     │   │
│  │          │   │               │   │  :6334 (gRPC)     │   │
│  │ministral │   │ DOCX/PDF→HTML │   │  Векторная БД     │   │
│  │qwen2.5   │   │   pandoc/AI   │   │  Dense+Sparse     │   │
│  └────┬─────┘   └──────┬────────┘   └────────┬──────────┘   │
│       │                │                      │              │
│  ┌────┴────────────────┴──────────────────────┴──────────┐  │
│  │                   ta-webui  :8000                      │  │
│  │                                                        │  │
│  │  FastAPI  +  Jinja2  +  WebSocket (live logs)          │  │
│  │                                                        │  │
│  │  worker.py — 5-этапный синхронный пайплайн             │  │
│  │  catalog_scheduler.py — APScheduler (суббота, 00:00)   │  │
│  └────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### Ключевые модули

| Файл | Назначение |
|------|-----------|
| `src/core/config.py` | Все настройки через переменные окружения |
| `src/core/document_parser.py` | Конвертация DOCX→HTML (pandoc) и вызов docling API |
| `src/core/docx_parser.py` | Резервный парсер DOCX без pandoc (python-docx) |
| `src/core/llm_client.py` | HTTP-клиент к Ollama-совместимому LLM API |
| `src/core/qdrant_indexer.py` | Гибридный поиск: индексация + поиск + LLM-судья |
| `src/core/text_utils.py` | Нормализация текста, загрузка product_terms.json |
| `src/core/analysis_service.py` | Оркестратор пайплайна (async-обёртка для тестов) |
| `src/webui/app_impl.py` | FastAPI-приложение, маршруты, WebSocket |
| `src/webui/worker.py` | Синхронный 5-этапный пайплайн (run_in_executor) |
| `src/webui/catalog_scheduler.py` | APScheduler: автообновление каталога каждую субботу |
| `scripts/e2e4_ingest.py` | Скачивание ZIP e2e4, извлечение и уплощение XLSX→CSV |
| `scripts/index_catalog.py` | Индексация CSV в коллекцию Qdrant |
| `scripts/run_webui.py` | Точка входа: запуск uvicorn |
| `data/product_terms.json` | Иерархический словарь категорий товаров |
| `data/catalogs/e2e4_flat.csv` | Плоский прайс-лист e2e4 (генерируется скриптом) |

---

## Стек технологий

| Категория | Технология |
|-----------|-----------|
| Web-фреймворк | FastAPI + Uvicorn |
| Шаблоны UI | Jinja2 (server-side HTML) |
| Real-time логи | WebSocket (нативный FastAPI) |
| LLM-инференс | Ollama (`ministral-3:3b`, `qwen2.5:1.5b`) |
| Парсинг документов | pandoc, python-docx, docling-serve (опционально) |
| Эмбеддинги (dense) | FastEmbed — `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` |
| Эмбеддинги (sparse) | FastEmbed — `Qdrant/bm42-all-minilm-l6-v2-attentions` (BM42) |
| Слияние результатов | RRF (Reciprocal Rank Fusion) в Qdrant |
| Векторная база | Qdrant (REST + gRPC) |
| Нормализация единиц | pint |
| Планировщик | APScheduler (`AsyncIOScheduler`) |
| Контейнеризация | Docker + Docker Compose |

---

## Требования

### Локальный запуск

- Python 3.11+
- [pandoc](https://pandoc.org/installing.html) (для конвертации DOCX)
- [Ollama](https://ollama.com/) с загруженными моделями:
  ```
  ollama pull ministral-3:3b
  ollama pull qwen2.5:1.5b
  ```
- Qdrant (локально или в Docker):
  ```
  docker run -p 6333:6333 qdrant/qdrant
  ```

### Docker Compose

- Docker Engine 24+
- Docker Compose v2
- NVIDIA GPU (опционально, для ускорения Ollama; работает и на CPU)

---

## Быстрый старт (локально)

```bash
# 1. Клонировать репозиторий
git clone https://github.com/deepsilver123/tender-alchemist.git
cd tender-alchemist

# 2. Создать и активировать виртуальное окружение
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # Linux / macOS

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Запустить сервисы (Ollama, Qdrant должны быть уже запущены)
python scripts/run_webui.py
```

Веб-интерфейс будет доступен по адресу: **http://127.0.0.1:8001**

### Первоначальная индексация каталога

Перед первым поиском необходимо загрузить прайс-лист e2e4 и проиндексировать его в Qdrant:

```bash
# Скачать прайс-лист и сконвертировать в CSV
python scripts/e2e4_ingest.py

# Проиндексировать CSV в Qdrant (~138 тыс. позиций, ~5–15 мин на CPU)
python scripts/index_catalog.py

# Опционально: указать другой CSV или регион
python scripts/e2e4_ingest.py --url https://e2e4online.ru/ws/excel/msk.e2e4online.ru.zip
python scripts/index_catalog.py --csv data/catalogs/e2e4_flat.csv
```

---

## Запуск через Docker Compose

```bash
# Скопировать файл переменных окружения
cp .env.example .env
# Отредактировать .env при необходимости (порты, ключи и т.д.)

# Собрать и запустить все сервисы
docker compose up -d --build

# Посмотреть логи webui
docker compose logs -f ta-webui

# Остановить
docker compose down
```

### Сервисы после запуска

| Сервис | Адрес | Описание |
|--------|-------|----------|
| ta-webui | http://localhost:8000 | Основной веб-интерфейс |
| ollama | http://localhost:11434 | LLM API (Ollama) |
| docling-serve | http://localhost:5001 | Конвертация документов (REST) |
| qdrant | http://localhost:6333/dashboard | Qdrant Web UI + REST API |

### Загрузка моделей в Docker

После первого запуска нужно загрузить модели в Ollama-контейнер:

```bash
docker exec -it ollama ollama pull ministral-3:3b
docker exec -it ollama ollama pull qwen2.5:1.5b
```

### Индексация каталога в Docker

```bash
# Выполнить индексацию внутри контейнера webui
docker exec -it ta-webui python scripts/index_catalog.py
```

---

## Настройка окружения

Скопируйте `.env.example` в `.env` и настройте нужные параметры:

```env
# Порты сервисов
WEBUI_PORT=8000
OLLAMA_PORT=11434
DOCLING_PORT=5001
```

Все остальные параметры передаются напрямую через переменные окружения или конфигурируются в `docker-compose.yml`. Полный список — в разделе [Переменные окружения](#переменные-окружения).

---

## Индексация каталога e2e4

Скрипт `scripts/e2e4_ingest.py` скачивает ZIP-архив с Excel-прайсом e2e4 и преобразует его в плоский CSV.

```bash
# Стандартный запуск (Иркутск, кеширует ZIP)
python scripts/e2e4_ingest.py

# Принудительное обновление (игнорировать кеш)
python scripts/e2e4_ingest.py --force

# Другой регион e2e4
python scripts/e2e4_ingest.py --url https://e2e4online.ru/ws/excel/nsk.e2e4online.ru.zip

# Тестовый прогон (только 500 строк)
python scripts/e2e4_ingest.py --sample 500
```

Результат: `data/catalogs/e2e4_flat.csv` (~138 тыс. позиций).

После этого запустите индексацию:

```bash
python scripts/index_catalog.py
```

Скрипт пересоздаёт коллекцию `e2e4_catalog` в Qdrant с именованными векторами:
- `dense` — 384-мерный вектор (multilingual-MiniLM), косинусное расстояние
- `sparse` — BM42 (лексический поиск по токенам)

Каждой точке в payload записывается: `title`, `price`, `link`, `sheet`, `vendor`.

---

## Работа в интерфейсе

### Главная страница — анализ тендера

1. Откройте **http://localhost:8000** (или `:8001` при локальном запуске).
2. Нажмите **«Новый анализ»** или перетащите файлы (DOCX, HTML) в область загрузки.
3. Опционально выберите LLM-модель и адрес сервера.
4. Нажмите **«Анализировать»** — в реальном времени появятся логи всех 5 этапов через WebSocket.
5. По завершении откроется страница результатов:
   - Список извлечённых товаров с техническими требованиями и НМЦК
   - Для каждого товара — топ-3 совпадения из каталога e2e4:
     - Название, цена
     - RRF-скор (чем выше — тем лучше семантическое + лексическое соответствие)
     - Вердикт LLM-судьи: ✅ подходит / ❌ не подходит

### Страница истории

Переход по ссылке **«История»** — список всех прошлых анализов с датой, названием и статусом. Можно повторно открыть результаты любого завершённого задания.

### Страница администратора (`/admin`)

Служебные функции:
- **Статус Qdrant** — проверка подключения и количества записей в коллекции
- **Ручной запуск индексации** — загрузить и переиндексировать каталог без CLI
- **Статус фонового задания** — прогресс текущей индексации (`/admin/status`)

---

## Автоматическое обновление каталога

При старте веб-сервера автоматически регистрируется задание в **APScheduler**:

- **Расписание**: каждую субботу в **00:00** по часовому поясу `TZ` (по умолчанию `Asia/Irkutsk`)
- **Цепочка**: скачать ZIP → распаковать → уплощить XLSX → переиндексировать Qdrant
- **Устойчивость к перезапускам**: `misfire_grace_time=3600` — задание выполнится при запуске сервера, если было пропущено в течение 1 часа

Управление через переменные окружения:

| Переменная | Значение по умолчанию | Описание |
|------------|----------------------|----------|
| `E2E4_CATALOG_URL` | `https://e2e4online.ru/ws/excel/irkutsk.e2e4online.ru.zip` | URL ZIP-архива с прайсом |
| `TZ` | `Asia/Irkutsk` | Часовой пояс планировщика |

---

## Структура проекта

```
tender-alchemist/
├── deploy/
│   ├── webui/
│   │   ├── Dockerfile          # Production-образ webui (Python 3.13-slim)
│   │   └── requirements.txt    # Зависимости для Docker-образа
│   ├── Dockerfile.ollama       # Ollama с предзагруженными моделями
│   └── Dockerfile.docling      # docling-serve
├── scripts/
│   ├── e2e4_ingest.py          # Скачать ZIP e2e4 → уплощить в CSV
│   ├── index_catalog.py        # Индексировать CSV → Qdrant
│   └── run_webui.py            # Запуск uvicorn (точка входа)
├── src/
│   ├── core/
│   │   ├── config.py           # Все настройки (env vars + defaults)
│   │   ├── analysis_service.py # Async-оркестратор пайплайна
│   │   ├── document_parser.py  # pandoc + docling конвертация DOCX→HTML
│   │   ├── docx_parser.py      # Резервный парсер DOCX (python-docx)
│   │   ├── llm_client.py       # HTTP-клиент к Ollama API (sync)
│   │   ├── ollama_client.py    # Async-клиент к Ollama
│   │   ├── qdrant_indexer.py   # Гибридный поиск + индексация + LLM-судья
│   │   ├── json_utils.py       # Извлечение JSON из текста LLM
│   │   └── text_utils.py       # Нормализация, product_terms.json
│   └── webui/
│       ├── app_impl.py         # FastAPI маршруты, WebSocket, lifespan
│       ├── app.py              # Точка входа ASGI (импортирует app из app_impl)
│       ├── worker.py           # Синхронный 5-этапный пайплайн
│       ├── catalog_scheduler.py # APScheduler: субботнее обновление каталога
│       └── templates/          # Jinja2 HTML-шаблоны
│           ├── index.html      # Главная страница / загрузка файлов
│           ├── task.html       # Страница задания с live-логами
│           ├── history.html    # История анализов
│           ├── admin.html      # Административная панель
│           ├── admin_result.html
│           └── admin_status.html
├── data/
│   ├── product_terms.json      # Иерархический словарь категорий товаров
│   ├── catalogs/
│   │   └── e2e4_flat.csv       # Плоский прайс-лист e2e4 (генерируется)
│   └── uploads/                # Загруженные пользователем файлы
├── docs/                       # Техническая документация
├── docker-compose.yml          # Оркестрация всех сервисов
├── .env.example                # Шаблон переменных окружения
└── requirements.txt            # Зависимости для разработки
```

---

## Переменные окружения

| Переменная | По умолчанию | Описание |
|------------|-------------|----------|
| `WEBUI_HOST` | `127.0.0.1` | Адрес прослушивания веб-сервера |
| `WEBUI_PORT` | `8001` | Порт веб-сервера |
| `LLM_BASE_URL` | `http://localhost:11434` | Базовый URL Ollama |
| `LLM_MODEL` | `ministral-3:3b` | Основная LLM-модель (извлечение ТЗ, LLM-судья, discover) |
| `LLM_QUERY_MODEL` | `qwen2.5:1.5b` | Лёгкая модель для дистилляции поисковых запросов |
| `LLM_API_KEY` | — | API-ключ (для совместимых провайдеров) |
| `DOCLING_BASE_URL` | `http://localhost:5001` | URL docling-serve |
| `QDRANT_URL` | `http://localhost:6333` | URL Qdrant REST API |
| `QDRANT_COLLECTION_NAME` | `e2e4_catalog` | Имя коллекции в Qdrant |
| `QDRANT_LOCAL_PATH` | `data/qdrant_db` | Путь для локального режима Qdrant (без сервера) |
| `DENSE_MODEL_NAME` | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | FastEmbed dense-модель |
| `DENSE_DIM` | `384` | Размерность dense-вектора |
| `SPARSE_MODEL_NAME` | `Qdrant/bm42-all-minilm-l6-v2-attentions` | FastEmbed sparse-модель (BM42) |
| `SEARCH_MAX_RESULTS` | `10` | Максимальное число кандидатов из Qdrant |
| `E2E4_CATALOG_URL` | `https://e2e4online.ru/ws/excel/irkutsk.e2e4online.ru.zip` | URL ZIP-прайса e2e4 |
| `TZ` | `Asia/Irkutsk` | Часовой пояс (для планировщика) |

---

## Разработка и тесты

```bash
# Активировать окружение
.venv\Scripts\activate

# Запуск тестов
python -m pytest tests/

# Запуск с авто-перезагрузкой при изменении кода
uvicorn webui.app:app --reload --app-dir src --port 8001

# Проверка подключения к Qdrant и количества записей
python -c "from qdrant_client import QdrantClient; c = QdrantClient('localhost', port=6333); print(c.get_collection('e2e4_catalog'))"
```

### Структура логов

Все задания сохраняют артефакты в `logs/<task_id>/`:

| Файл | Содержимое |
|------|-----------|
| `prompt.html` | Полный prompt, отправленный в LLM |
| `raw_answer.log` | Сырой ответ модели |
| `result.json` | Нормализованный JSON с товарами |
| `search_results.json` | Результаты поиска в Qdrant |

Общий лог сервера: `logs/webui.log`.

---

## Лицензия

Каво?
