#!/usr/bin/env python3
"""Простой тестовый скрипт для проверки веб-поиска по готовому JSON с товаром."""

from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from core.web_search import find_matching_models
from core.config import SEARXNG_URL, MINISTRAL_BASE_URL, SEARCH_MODEL

PRODUCT =     {
      "product_name": "Блок питания 1",
      "technical_requirements": {
        "Мощность": "Не менее 750 Вт",
        "Форм-фактор": "ATX",
        "Сертификат 80 PLUS": "Не ниже GOLD",
        "Мощность по линии 12 В": "Не менее 750 Вт",
        "Ток по линии +12 В": "Не менее 12V1 62.5A",
        "Ток по линии +3.3 В": "Не менее 20 А",
        "Ток по линии +5 В": "Не менее 20 А",
        "Отстегивающиеся кабели": "полностью модульный",
        "Оплетка проводов": "индивидуальная тканевая оплетка",
        "Разъемы для питания видеокарты": "3 x 6+2 pin, 16 pin (12V-2x6)",
        "Длина кабеля питания процессора": "Не менее 700 мм",
        "Совместим с закупаемыми": "материнская плата, блок питания 2, видеокарта, кулер для процессора, модуль памяти 2"
      },
      "commercial_terms": {
        "quantity": 1,
        "unit": "шт",
        "price_per_unit": 10173.67,
        "currency": "RUB",
        "total_amount": 10173.67
      }
    }


def main() -> int:
    print("=== Проверка модуля web_search ===")
    print(f"SEARXNG_URL={SEARXNG_URL}")
    print(f"MINISTRAL_BASE_URL={MINISTRAL_BASE_URL}")
    print(f"SEARCH_MODEL={SEARCH_MODEL}")
    print()

    matches = find_matching_models(PRODUCT, log_cb=print)

    print()
    print("=== Результат ===")
    print(json.dumps(matches, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
