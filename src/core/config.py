# config.py (moved into core package)
from pathlib import Path
import os

# --- Docling Serve ---
DOCLING_BASE_URL = os.environ.get("DOCLING_BASE_URL", "http://localhost:5001").rstrip("/")
DOCLING_URL = os.environ.get("DOCLING_URL", f"{DOCLING_BASE_URL}/v1/convert/file")
DOCLING_URL_ASYNC = os.environ.get("DOCLING_URL_ASYNC", f"{DOCLING_BASE_URL}/v1/convert/file/async")
DOCLING_STATUS_URL = os.environ.get("DOCLING_STATUS_URL", f"{DOCLING_BASE_URL}/v1/status/poll")
DOCLING_RESULT_URL = os.environ.get("DOCLING_RESULT_URL", f"{DOCLING_BASE_URL}/v1/result")
DOCLING_TIMEOUT = 120
DOCLING_API_KEY = os.environ.get("DOCLING_API_KEY")

# --- LLM service ---
# This is a generic external LLM endpoint. It can be any Ollama-compatible HTTP API,
# not necessarily Ministral.
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "http://localhost:11434").rstrip("/")
LLM_URL = os.environ.get("LLM_URL", f"{LLM_BASE_URL}/api")
LLM_API_KEY = os.environ.get("LLM_API_KEY")
LLM_MODEL = os.environ.get("LLM_MODEL", "ministral-3:3b")
# Модель для генерации поисковых запросов — лёгкая и быстрая
LLM_QUERY_MODEL = os.environ.get("LLM_QUERY_MODEL", "qwen2.5:1.5b")
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 4000
LLM_NUM_CTX = 32384   # целимся в устойчивую работу на слабой GPU
LLM_NUM_PREDICT = 8192

# --- Embedding service ---
# Separate embedding service endpoint for vectorization.
# By default, use the same host as the main LLM service when an embedding-specific URL is not configured.
EMBEDDING_BASE_URL = os.environ.get("EMBEDDING_BASE_URL", LLM_BASE_URL).rstrip("/")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "bge-m3")

# Backwards compatibility aliases for older embedding config names.
OLLAMA_URL = EMBEDDING_BASE_URL
OLLAMA_EMBED_MODEL = EMBEDDING_MODEL

# --- Prompt for converting tender documentation into JSON specification ---
TENDER_DOCUMENT_JSON_PROMPT = """Ты — эксперт по закупкам. Проанализируй документ и верни JSON по схеме.

Документ содержит HTML-разметку, но основная информация — в тексте и таблицах.
Найди все товары. Обычно товары перечислены в строках таблиц, где есть название, цена, количество.
Технические характеристики товаров — это строки, где в первой ячейке название характеристики, во второй — значение.
Цены, количество и суммы бери из таблиц с коммерческими данными.
Объедини характеристики с соответствующим товаром.
Если перед документом дан предварительно извлечённый список кандидатов товаров, используй его как ориентир для сопоставления данных, но не считай его исчерпывающим и не выдумывай позиции только на его основе.
Если в самом документе есть более точное наименование товара, чем в предварительном списке, выбирай более точное наименование из документа.

ВАЖНО: Из НМЦК для каждого товара извлекай именно среднюю цену за единицу и среднюю общую стоимость.

Схема JSON:
{
  "products": [
    {
      "product_name": "название товара",
      "technical_requirements": {"характеристика": "значение"},
      "commercial_terms": {"quantity": число, "price_per_unit": число, "total_amount": число}
    }
  ]
}

Важно:
- Верни строго валидный JSON.
- Не добавляй никаких пояснений, комментариев или текста до/после JSON.
- Не используй больше одного двоеточия внутри одной пары ключ-значение.
- Если характеристика содержит фразу "Совместим с закупаемыми", используй её как ключ, а все перечисленные компоненты после двоеточия — как значение. Пример:
  "Совместим с закупаемыми": "материнская плата 2, блок питания 2, видеокарта, кулер для процессора, модуль памяти 2"
- Не добавляй дополнительные поля или вложенные строки, которые ломают структуру.
- Если значение состоит из нескольких частей, сохраняй его как одну строку текста, но внутри одной пары должно быть ровно одно значение.

Верни только JSON. Никакого текста до или после."""

# --- SearXNG ---
SEARXNG_URL = os.environ.get("SEARXNG_URL", "http://localhost:8080").rstrip("/")
SEARXNG_TIMEOUT = 10
SEARCH_MAX_RESULTS = int(os.environ.get("SEARCH_MAX_RESULTS", "15"))

# --- Qdrant ---
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333").rstrip("/")

# Модель для генерации поисковых запросов (лёгкая — для быстрого NER)
SEARCH_MODEL = os.environ.get("SEARCH_MODEL", "qwen2.5:1b")
SEARCH_NUM_CTX = 4096
SEARCH_NUM_PREDICT = 512

# --- Промпт: генерация поисковых запросов из JSON-продукта ---
SEARCH_PROMPT = """Ты — поисковый ассистент для российских интернет-магазинов IT-техники.

ЗАДАЧА: Из описания товара извлеки тип товара и его СОБСТВЕННЫЕ характеристики для поиска в интернете.
Тебе нужно сформировать поисковой запрос, чтобы найти похожий товар в интернете с использованием таких поисковиков как Google, Yandex и т.д.

Твой продукт:
{product_json}

Верни ТОЛЬКО JSON без пояснений:
{"category": "...", "search_terms": []}"""

# --- Промпт: сопоставление результатов поиска с требованиями ---
MATCH_PROMPT = """Ты — суровый технический аудитор. Твоя задача: выявить в куче поискового мусора реальные модели, подходящие под ТЗ, и написать супер-краткий вердикт.

ПРАВИЛА:
1. ИЩИ КОНКРЕТИКУ: Добавляй результат ТОЛЬКО если найдена конкретная модель товара (например «Блок питания Chieftec 600W»). Каталоги, справочники и мусор — пропускай.
2. ФИЛЬТРАЦИЯ: Базовые характеристики (форм-фактор, мощность, диагональ, процессоры) должны в целом подходить под ТЗ.
3. ПОЛЕ NOTES: Никаких длинных предложений! Делай короткую выжимку (булетами через разделитель). Обязательно используй эмодзи ✅ (найдено/совпадает) и ❌ (отсутствует/не совпадает).
   ПРИМЕР ХОРОШЕГО NOTES: "✅ Лазерная печать | ✅ 40 стр/мин | ❌ Нет Wi-Fi | ❌ Цветной (нужен ч/б)"
   ПРИМЕР ПЛОХОГО NOTES: "Принтер лазерный, формат А4, печатает со скоростью 40 страниц в минуту, но у него нет вайфая..."
4. ВЕРНИ: До 5 лучших кандидатов (если ничего не подходит под главные характеристики — верни пустой массив []).

Технические требования (ТЗ):
{technical_requirements}

Результаты поиска:
{search_results}

Верни СТРОГО JSON-массив объектов. Схема объекта: 
{{"model": "Точное Имя Модели", "url": "ссылка", "notes": "✅ ... | ❌ ..."}}
Никаких комментариев до или после массива."""

# --- Project folders ---
# `DATA_DIR` and `LOG_DIR` point to project-level folders (one level above `src/`)
# PROJECT_ROOT should point to repository root (one level above `src/`).
# config.py is in `src/core/`, so go up three levels from the file to reach repo root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"

# --- Web UI host/port ---
# `WEBUI_HOST` and `WEBUI_PORT` can be set via environment variables
# or consumed by other scripts that import this config.
WEBUI_HOST = os.environ.get("WEBUI_HOST", "0.0.0.0")
try:
  WEBUI_PORT = int(os.environ.get("WEBUI_PORT", "8000"))
except (TypeError, ValueError):
  WEBUI_PORT = 8000
