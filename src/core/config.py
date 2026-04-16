# config.py (moved into core package)
from pathlib import Path

# --- Docling Serve ---
DOCLING_URL = "http://localhost:5001/v1/convert/file"
DOCLING_TIMEOUT = 120
DOCLING_API_KEY = None

# --- Ministral ---
MINISTRAL_URL = "http://localhost:11434/api"
MINISTRAL_API_KEY = None
MINISTRAL_MODEL = "ministral-3:3b"
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

Верни только JSON. Никакого текста до или после."""

# --- Project folders ---
# `DATA_DIR` and `LOG_DIR` point to project-level folders (one level above `src/`)
# PROJECT_ROOT should point to repository root (one level above `src/`).
# config.py is in `src/core/`, so go up three levels from the file to reach repo root.
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
