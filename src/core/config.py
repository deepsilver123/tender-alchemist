# config.py (moved into core package)
from pathlib import Path
import os

# --- Docling Serve ---
DOCLING_URL = os.environ.get("DOCLING_URL", "http://localhost:5001/v1/convert/file")
DOCLING_TIMEOUT = 120
DOCLING_API_KEY = os.environ.get("DOCLING_API_KEY")

# --- Ministral ---
MINISTRAL_URL = os.environ.get("MINISTRAL_URL", "http://localhost:11434/api")
MINISTRAL_API_KEY = os.environ.get("MINISTRAL_API_KEY")
MINISTRAL_MODEL = os.environ.get("MINISTRAL_MODEL", "ministral-3:3b")
MINISTRAL_TEMPERATURE = 0.1
MINISTRAL_MAX_TOKENS = 4000
MINISTRAL_NUM_CTX = 32384   # целимся в устойчивую работу на слабой GPU
MINISTRAL_NUM_PREDICT = 8192

# --- Промпт для анализа небольших документов (без чанкования) ---
MINISTRAL_PROMPT = """Ты — эксперт по закупкам. Проанализируй документ и верни JSON по схеме.

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
      "brand_reference": {"brand": "", "allow_equivalent": true},
      "technical_requirements": {"характеристика": "значение"},
      "commercial_terms": {"quantity": число, "unit": "шт", "price_per_unit": число, "currency": "RUB", "total_amount": число}
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
