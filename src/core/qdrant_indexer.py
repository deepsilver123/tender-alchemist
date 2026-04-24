from __future__ import annotations

import json
import re
import uuid
import zipfile
from collections.abc import Callable

import pint
import pandas as pd
import requests
import qdrant_client
from qdrant_client.models import (
    Distance, VectorParams, SparseVectorParams, SparseIndexParams,
    PointStruct, Filter, FieldCondition, MatchValue,
    SparseVector, Prefetch, FusionQuery, Fusion,
)

from core.config import QDRANT_URL, LLM_BASE_URL, LLM_MODEL, DATA_DIR
from core.text_utils import _load_terms

# --- FastEmbed models (загружаются лениво при первом вызове) ---
_DENSE_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_SPARSE_MODEL_NAME = "Qdrant/bm42-all-minilm-l6-v2-attentions"
_DENSE_DIM = 384

# Слова, указывающие на б/у состояние товара
_USED_MARKERS = frozenset({
    'б/у', 'после ремонта', 'восстановленный', 'восстановлен',
    'следы эксплуатации', 'потертости', 'ремонт шлейфа',
})

# Загружаем термины продуктов и строим мапу id -> name из product_terms.json
_TYPE_ID_TO_CATEGORY = {term['id']: term['name'] for term in _load_terms()}

# Категории ПК-устройств, для которых актуальна ОС в поисковом запросе
_PC_CATEGORIES: frozenset[str] = frozenset(filter(None, (
    _TYPE_ID_TO_CATEGORY.get('computer.desktop'),
    _TYPE_ID_TO_CATEGORY.get('computer.laptop'),
    _TYPE_ID_TO_CATEGORY.get('computer.allinone'),
    _TYPE_ID_TO_CATEGORY.get('computer.mini'),
    _TYPE_ID_TO_CATEGORY.get('computer.thinclient'),
)))

# Маппинг подстрок ОС -> компактный токен (от длинного к короткому!)
_OS_TOKENS: list[tuple[str, str]] = [
    ('windows 11 pro', 'W11Pro'),
    ('windows 11', 'W11'),
    ('windows 10 pro', 'W10Pro'),
    ('windows 10', 'W10'),
]


def _normalize_os_token(value: str) -> str | None:
    """Возвращает компактный токен ОС или None если не распознана."""
    vl = value.lower()
    for substring, token in _OS_TOKENS:
        if substring in vl:
            return token
    return None


_GENERIC_QUERY_STOPWORDS = frozenset({
    'или', 'эквивалент', 'не', 'менее', 'более', 'для', 'по', 'на', 'с', 'без',
    'в', 'сборе', 'собран',
    'блок', 'питания', 'диск', 'накопитель', 'твердотельный', 'жесткий',
    'монитор', 'ноутбук', 'моноблок', 'мышь', 'мышка', 'клавиатура',
    'роутер', 'маршрутизатор', 'мфу', 'принтер', 'факс', 'корпус',
    'системный', 'устройство', 'товар', 'модель', 'тип', 'дюймов',
    'дюйма', 'дюйм', 'память', 'модуль',
})

_LOW_SIGNAL_ALPHA_TOKENS = frozenset({
    'gold', 'silver', 'bronze', 'white', 'black', 'plus', 'retail', 'bulk',
    'oem', 'rgb', 'ips', 'va', 'tn', 'usb', 'sata', 'nvme', 'wifi',
    'цветной', 'черный', 'черная', 'черное', 'белый', 'белая', 'лазерный',
})

# --- Скомпилированные шаблоны регулярных выражений ---
RE_CAPACITY_SKIP_TERM = re.compile(r'\d+(Gb|Mb|Tb|MHz)$', re.IGNORECASE)
RE_LAPTOP_DIAGONAL = re.compile(r'\d+(?:[.,]\d+)?\s*("|дюйм(?:а|ов)?|inch)', re.IGNORECASE)
RE_LAPTOP_RESOLUTION = re.compile(r'\d{3,4}\s*[xх×]\s*\d{3,4}|\d{3,4}p\b|full\s*hd\b|fhd\b|qhd|2k|4k|uhd|ultra\s*hd\b', re.IGNORECASE)
RE_LAPTOP_FREQUENCY = re.compile(r'\d+(?:[.,]\d+)?\s*(ghz|мгц|mhz|hz)\b', re.IGNORECASE)
RE_MEMORY_TYPE = re.compile(r'\b(?:ddr[45]|dimm|sodimm|udimm|lrdimm|rdimm)\b', re.IGNORECASE)
RE_MB_ONLY = re.compile(r'\b\d+(?:[.,]\d+)?\s*mb\b', re.IGNORECASE)
RE_EXTRACT_CAPACITY = re.compile(r'(\d+(?:[.,]\d+)?)\s*(tb|gb|mb|тб|гб|мб)\b', re.IGNORECASE)
RE_EXTRACT_POWER = re.compile(r'(\d+(?:[.,]\d+)?)\s*(w|вт)\b', re.IGNORECASE)
RE_EXTRACT_FREQ = re.compile(r'(\d+(?:[.,]\d+)?)\s*(ghz|mhz|hz|ггц|мгц|гц)\b', re.IGNORECASE)
RE_EXTRACT_DIAGONAL = re.compile(r'(\d+(?:[.,]\d+)?)\s*(?:"|дюйм(?:а|ов)?|inch)', re.IGNORECASE)
RE_JSON = re.compile(r'\{[^{}]+\}', re.DOTALL)

# --- Инициализация и настройка реестра единиц измерения Pint ---
ureg = pint.UnitRegistry()
# Добавляем синонимы для русского языка (приводим к эталону Pint)
ureg.define("gigabyte = 1000 * megabyte = gb = гб = гигабайт = гигабайта = гигабайтов = Gb = Гб")
ureg.define("terabyte = 1000 * gigabyte = tb = тб = терабайт = терабайта = терабайтов = Tb = Тб")
ureg.define("megabyte = 1000 * kilobyte = mb = мб = мегабайт = мегабайта = мегабайтов = Mb = Мб")
ureg.define("gigahertz = 1000 * megahertz = ghz = ггц = GHz = ГГц")
ureg.define("megahertz = 1000 * kilohertz = mhz = мгц = MHz = МГц")
ureg.define("hertz = hz = гц = Hz = Гц")
ureg.define("watt = w = вт = W = Вт")
ureg.define("inch = 2.54 * centimeter = дюйм = дюйма = дюймов")

def _parse_quantity(value_str: str, unit_str: str) -> pint.Quantity | None:
    try:
        val = float(value_str.replace(',', '.'))
        return ureg.Quantity(val, unit_str)
    except (ValueError, pint.errors.PintError):
        return None


# ------------------------------------------------------------------ #
#  Шаблон промпта для LLM-генерации поискового запроса               #
# ------------------------------------------------------------------ #
_LLM_QUERY_PROMPT = """\
Ты генератор поисковых запросов для каталога IT-оборудования.
Твоя задача — составить одну строку запроса, используя ТОЛЬКО данные из полей "Категория", "Название из ТЗ" и "Характеристики из ТЗ", которые указаны ниже.
НЕ используй данные из примеров формата — это только шаблоны структуры, не источник значений.

Форматы строк каталога по категориям:
  МФУ           → МФУ [лазерный|струйный] <Модель>, <A4|A3>, <ч/б|цветной>[, <N> стр/мин]
  Принтер       → Принтер [лазерный|струйный|матричный] <Модель>, <A4|A3>, <ч/б|цветной>[, <N> стр/мин]
  Ноутбук       → Ноутбук <Серия> <Диагональ>", <CPU>, <N>Gb RAM, <N>Gb SSD[, <ОС>]
  Монитор       → Монитор <Диагональ>" [<Матрица>][, <Разрешение>]
  Системный блок → Системный блок [,] <CPU> <N> ГГц, <N>Gb RAM, <N>Gb SSD[, <ОС>]
  Моноблок      → Моноблок <Серия> <Диагональ>" <Разрешение>, <CPU> <N> ГГц, <N>Gb RAM, <N>Gb SSD
  SSD-Диск      → Твердотельный накопитель (SSD) <N>Gb[, <2.5"|M.2>][, <SATA3|NVMe>]
  HDD-Диск      → Жесткий диск <N>Tb[, <3.5"|2.5">][, SATA3]
  Оперативная память → Оперативная память <N>Gb <DDR4|DDR5>[ <N>МГц][, <DIMM|SODIMM>][, ECC]
  Блок питания  → Блок питания <N> Вт <Модель>[, 80 Plus <Grade>]
  Материнская плата → Материнская плата <Модель>, <Сокет>, <Чипсет>
  Процессор     → Процессор <Модель>[, <Сокет>]
  Коммутатор    → Коммутатор <Модель>[, <управляемый|неуправляемый>][, <NxСкорость>]
  Маршрутизатор → Маршрутизатор <Модель>
  ИБП           → Источник бесперебойного питания <Модель>[, <N> ВА]
  Мышь          → Мышь <Модель>[, <N>dpi][, <оптическая|лазерная>][, <USB|беспроводная>]
  Клавиатура    → Клавиатура <Модель>[, <мембранная|механическая>][, <USB|беспроводная>]
  Факс          → Факс <Модель>
  Сканер        → Сканер <Модель>[, <A4|A3>][, <N> стр/мин]
  IP-телефон    → IP-телефон <Модель>
  Картридж      → Картридж [лазерный|струйный] <Артикул>[, <цвет>][, <N> страниц]

Правила подстановки значений:
- Квадратные скобки [ ] в шаблонах обозначают необязательные части. Подставляй только значение, без самих скобок. Если значение неизвестно — пропусти этот фрагмент целиком.
- Угловые скобки < > обозначают место для конкретного значения из ТЗ. Подставляй только значение, без самих скобок.
- Вертикальная черта | означает выбор одного из вариантов. Выбери подходящий вариант и подставь его без скобок.
- Бренд и модель: берёшь из поля "Название из ТЗ" или "Характеристики из ТЗ". Если не указаны — не подставляй ничего.
- Объёмы памяти/накопителей: 16Gb, 512Gb, 1Tb (числа из ТЗ, единица Gb или Tb).
- ОС: "W10Pro" если Windows 10 Pro, "W11Pro" если Windows 11 Pro, "W11" если Windows 11, пропусти если нет.
- МФУ/Принтер без указания типа печати → "лазерный".
- Если значение поля неизвестно — пропусти его, не придумывай.

Категория: {category}
Название из ТЗ: {original}
Характеристики из ТЗ: {specs}
Поисковый запрос (одна строка, без кавычек, без пояснений):"""


class TenderMVPQdrant:
    def __init__(
        self,
        collection_name: str = "e2e4_catalog",
        qdrant_path: str = "data/qdrant_db",
        qdrant_url: str | None = None,
    ):
        self.collection_name = collection_name
        self.qdrant_url = (qdrant_url or QDRANT_URL).rstrip("/") if (qdrant_url or QDRANT_URL) else None
        if self.qdrant_url:
            self.client = qdrant_client.QdrantClient(url=self.qdrant_url)
        else:
            self.client = qdrant_client.QdrantClient(path=qdrant_path)

        self._dense_model = None
        self._sparse_model = None

    # ------------------------------------------------------------------ #
    #  FastEmbed helpers (ленивая инициализация)                           #
    # ------------------------------------------------------------------ #

    def _get_dense_model(self):
        if self._dense_model is None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from fastembed import TextEmbedding
                self._dense_model = TextEmbedding(
                    model_name=_DENSE_MODEL_NAME,
                    providers=["CPUExecutionProvider"],
                    cuda=False,
                )
        return self._dense_model

    def _get_sparse_model(self):
        if self._sparse_model is None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from fastembed.sparse import SparseTextEmbedding
                self._sparse_model = SparseTextEmbedding(
                    model_name=_SPARSE_MODEL_NAME,
                    providers=["CPUExecutionProvider"],
                    cuda=False,
                )
        return self._sparse_model

    def _embed_dense(self, texts: list[str]) -> list[list[float]]:
        return [emb.tolist() for emb in self._get_dense_model().embed(texts)]

    def _embed_sparse(self, texts: list[str]) -> list[SparseVector]:
        results = []
        for emb in self._get_sparse_model().embed(texts):
            results.append(SparseVector(
                indices=emb.indices.tolist(),
                values=emb.values.tolist(),
            ))
        return results

    # ------------------------------------------------------------------ #
    #  Коллекция Qdrant                                                    #
    # ------------------------------------------------------------------ #

    def _ensure_collection(self, recreate: bool = False) -> None:
        collections = self.client.get_collections().collections
        exists = any(c.name == self.collection_name for c in collections)
        if exists and recreate:
            self.client.delete_collection(collection_name=self.collection_name)
            exists = False
        if not exists:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config={
                    "dense": VectorParams(size=_DENSE_DIM, distance=Distance.COSINE),
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(on_disk=False)
                    ),
                },
            )

    # ------------------------------------------------------------------ #
    #  Построение поискового запроса                                       #
    # ------------------------------------------------------------------ #

    def _llm_build_query(self, product_dict: dict) -> str | None:
        """Генерирует поисковый запрос через LLM по шаблонам именования каталога e2e4.
        Возвращает строку запроса или None при ошибке / пустом ответе."""
        name = str(product_dict.get("product_name", "")).strip()
        original = str(product_dict.get("original_product_name", name)).strip()
        chars = product_dict.get("technical_requirements") or product_dict.get("characteristics") or {}
        if not isinstance(chars, dict):
            chars = {}

        # Убираем "или эквивалент" из названия
        for phrase in (" или эквивалент", "или эквивалент"):
            original = original.replace(phrase, "").strip()
            name = name.replace(phrase, "").strip()

        category = self._infer_query_category(product_dict, name, chars)

        # Формируем строку характеристик (не более 25 полей)
        spec_lines = "; ".join(f"{k}: {v}" for k, v in list(chars.items())[:25]) if chars else "(не указаны)"

        prompt = _LLM_QUERY_PROMPT.format(
            category=category,
            original=original or name or "(не указано)",
            specs=spec_lines,
        )

        try:
            base = (LLM_BASE_URL or "http://localhost:11434").rstrip("/")
            resp = requests.post(
                f"{base}/api/generate",
                json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
                timeout=45,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()

            # Берём первую непустую строку, снимаем обрамляющие кавычки
            for line in text.splitlines():
                line = line.strip()
                # Убираем вводные слова вроде "Поисковый запрос:" / "Ответ:"
                line = re.sub(r'^(поисковый запрос\s*:|ответ\s*:)\s*', '', line, flags=re.IGNORECASE)
                line = re.sub(r'^["«»\'`]+|["«»\'`]+$', '', line).strip()
                if line and len(line) > 4:
                    return line
        except Exception:
            pass
        return None

    def build_query_text(self, product_dict: dict) -> str:
        """Строит поисковый запрос: сначала через LLM (по шаблонам каталога), иначе rule-based."""
        # Пробуем LLM-генерацию
        llm_query = self._llm_build_query(product_dict)
        if llm_query:
            return llm_query

        # Fallback: rule-based построение запроса
        return self._rule_based_query(product_dict)

    def _rule_based_query(self, product_dict: dict) -> str:
        """Rule-based fallback для построения поискового запроса."""
        name = str(product_dict.get("product_name", "")).strip()
        original = str(product_dict.get("original_product_name", "")).strip()
        chars = product_dict.get("technical_requirements") or product_dict.get("characteristics") or {}
        if not isinstance(chars, dict):
            chars = {}

        # Убираем "или эквивалент"
        for phrase in (" или эквивалент", "или эквивалент"):
            name = name.replace(phrase, "").strip()
            original = original.replace(phrase, "").strip()

        category = self._infer_query_category(product_dict, name, chars)
        ranked_terms = self._collect_query_terms(product_dict, category, original, chars)

        if category == 'Ноутбук':
            ranked_terms = self._prioritize_laptop_terms(ranked_terms)

        parts = [category]
        for item in ranked_terms[:7]:
            term = item["term"]
            
            # Игнорируем емкости и частоты памяти для не-модулей памяти,
            # чтобы не искать процессоры или мат.платы по кэшу или макс. объему ОЗУ.
            if category in ('Процессор', 'Материнская плата', 'Системный блок', 'Кулер', 'Блок питания', 'Коммутатор', 'Принтер', 'МФУ', 'Монитор'):
                if RE_CAPACITY_SKIP_TERM.search(term):
                    continue

            if any(self._normalize_term_key(term) == self._normalize_term_key(existing) for existing in parts):
                continue
            parts.append(term)

        return " ".join(part for part in parts if part).strip()

    def _infer_query_category(self, product_dict: dict, product_name: str, chars: dict) -> str:
        """Возвращает категорию из product_terms.json по type_id, иначе — product_name из JSON."""
        type_id = str(product_dict.get('type_id') or '').strip().lower()
        if type_id in _TYPE_ID_TO_CATEGORY:
            return _TYPE_ID_TO_CATEGORY[type_id]
        return product_name

    @staticmethod
    def _laptop_term_priority(term: str) -> int:
        lower = term.lower()
        if RE_LAPTOP_DIAGONAL.search(lower):
            return 1
        if RE_LAPTOP_RESOLUTION.search(lower):
            return 2
        if RE_LAPTOP_FREQUENCY.search(lower):
            return 3
        if RE_MEMORY_TYPE.search(lower):
            return 7
        capacity = re.search(r'\b(\d+(?:[.,]\d+)?)\s*(gb|tb)\b', lower)
        if capacity:
            qty = _parse_quantity(capacity.group(1), capacity.group(2))
            if qty and qty <= _parse_quantity('64', 'gb'):
                return 4
            return 5
        if any(tok in lower for tok in ('ssd', 'hdd', 'nvme', 'sata', 'm.2', 'm2')):
            return 6
        if 'ips' in lower or 'va' in lower or 'tn' in lower:
            return 7
        if RE_MB_ONLY.search(lower) and 'gb' not in lower and 'tb' not in lower:
            return 8
        if 'usb 3.2' in lower or 'usb-c' in lower or 'usb 3.0' in lower or 'usb 2.0' in lower:
            return 9
        return 10

    def _prioritize_laptop_terms(self, ranked_terms: list[dict]) -> list[dict]:
        return sorted(
            ranked_terms,
            key=lambda item: (
                self._laptop_term_priority(item['term']),
                item['index'],
            ),
        )

    @staticmethod
    @staticmethod
    def _normalize_term_key(text: str) -> str:
        return re.sub(r'[^a-zа-я0-9]+', '', text.lower())

    @staticmethod
    def _normalize_capacity_token(value: str, unit: str) -> str:
        # Теперь перекладываем математику на утилиту Pint
        qty = _parse_quantity(value, unit)
        if not qty:
            raw = value.replace(',', '.').strip()
            if raw.endswith('.0'):
                raw = raw[:-2]
            return f"{raw}{unit}"

        # Нормализация емкости: возвращаем строку в лучшем формате для индекса (Gb или Tb или Mb)
        mg = qty.to('megabyte').magnitude
        gb = qty.to('gigabyte').magnitude
        tb = qty.to('terabyte').magnitude
        
        unit_lower = unit.lower()
        if unit_lower in {'гб', 'gb', 'гигабайт', 'гигабайта', 'гигабайтов'}:
            if gb >= 1000 and gb % 1000 == 0:
                return f"{int(gb // 1000)}Tb"
            if gb >= 1024 and gb % 1024 == 0:
                return f"{int(gb // 1024)}Tb"
            return f"{int(gb) if gb.is_integer() else gb}Gb"
        
        if unit_lower in {'тб', 'tb', 'терабайт', 'терабайта', 'терабайтов'}:
            return f"{int(tb) if tb.is_integer() else tb}Tb"
            
        if unit_lower in {'мб', 'mb', 'мегабайт', 'мегабайта', 'мегабайтов'}:
            return f"{int(mg) if mg.is_integer() else mg}Mb"
            
        raw = value.replace(',', '.').strip()
        if raw.endswith('.0'):
            raw = raw[:-2]
        return f"{raw}{unit}"

    @staticmethod
    def _capacity_tokens(capacity_hint: str) -> set[str]:
        lower = capacity_hint.lower()
        if lower.endswith('tb'):
            num = lower[:-2]
            try:
                count = int(float(num))
            except ValueError:
                return {lower}
            return {
                f'{num}tb', f'{num} тб',
                f'{count * 1000}gb', f'{count * 1000} гб',
                f'{count * 1024}gb', f'{count * 1024} гб',
            }
        if lower.endswith('gb'):
            num = lower[:-2]
            return {f'{num}gb', f'{num} гб'}
        if lower.endswith('mb'):
            num = lower[:-2]
            return {f'{num}mb', f'{num} мб'}
        return {lower}

    @staticmethod
    def _extract_model_terms(text: str) -> list[str]:
        patterns = [
            r'\b[A-Za-z]{1,8}(?:-[A-Za-z0-9]{1,12})+\b',
            r'\b[A-Za-z]{1,8}\d[A-Za-z0-9-]{1,24}\b',
            r'\b\d+[A-Za-z][A-Za-z0-9-]{1,24}\b',
        ]
        seen: set[str] = set()
        results: list[str] = []
        for pattern in patterns:
            for match in re.findall(pattern, text):
                lower = match.lower()
                if re.fullmatch(r'\d+(?:gb|tb|mb|w|hz|mhz)', lower):
                    continue
                if lower in seen:
                    continue
                seen.add(lower)
                results.append(match)
        return results

    @staticmethod
    def _extract_brand_terms(text: str, category_words: set[str], model_terms: list[str] | None = None) -> list[str]:
        tokens = re.findall(r'[A-Za-zА-Яа-я][A-Za-zА-Яа-я-]{2,}', text)
        model_terms = model_terms or []
        model_norms = [term.lower() for term in model_terms]
        seen: set[str] = set()
        results: list[str] = []
        for token in tokens:
            lower = token.lower()
            if lower in category_words or lower in _GENERIC_QUERY_STOPWORDS or lower in _LOW_SIGNAL_ALPHA_TOKENS:
                continue
            if any(lower != model and lower in model for model in model_norms):
                continue
            if lower in seen:
                continue
            seen.add(lower)
            results.append(token)
            if len(results) >= 2:
                break
        return results

    @staticmethod
    def _extract_facet_terms(text: str) -> list[tuple[str, int, bool]]:
        lower = text.lower()
        found: list[tuple[str, int, bool]] = []
        seen: set[str] = set()

        def add(term: str, weight: int, hard: bool) -> None:
            norm = re.sub(r'[^a-zа-я0-9]+', '', term.lower())
            if not norm or norm in seen:
                return
            seen.add(norm)
            found.append((term, weight, hard))

        for a, b in re.findall(r'(\d{3,4})\s*[xх×]\s*(\d{3,4})', lower):
            add(f'{a}x{b}', 95, True)

        for value, unit in re.findall(r'(\d+(?:[.,]\d+)?)\s*(тб|tb|терабайт(?:а|ов)?|гб|gb|гигабайт(?:а|ов)?)\b', lower):
            add(TenderMVPQdrant._normalize_capacity_token(value, unit), 85, True)

        for value in re.findall(r'(\d{2,5})\s*(?:вт|w)\b', lower):
            add(f'{int(value)}W', 85, True)

        for value, unit in re.findall(r'(\d+(?:[.,]\d+)?)\s*(ггц|ghz|гц|hz|мгц|mhz)\b', lower):
            if unit in {'ггц', 'ghz'}:
                suffix = 'GHz'
            elif unit in {'мгц', 'mhz'}:
                suffix = 'MHz'
            else:
                suffix = 'Hz'
            num = value.replace(',', '.')
            if num.endswith('.0'):
                num = num[:-2]
            add(f'{num}{suffix}', 75, True)

        for value in re.findall(r'(\d{1,2}(?:[.,]\d+)?)\s*(?:"|дюйм(?:а|ов)?|inch)\b', lower):
            num = value.replace(',', '.')
            if num.endswith('.0'):
                num = num[:-2]
            add(f'{num}"', 75, True)

        for value in re.findall(r'\b(full\s*hd|fhd)\b', lower):
            add('Full HD', 95, True)
        for value in re.findall(r'\b(qhd|2k|4k|uhd|ultra\s*hd)\b', lower):
            norm = value.upper().replace(' ', '')
            add(norm, 95, True)
        for value in re.findall(r'(\d{3,4})\s*p\b', lower):
            add(f'{value}p', 95, True)

        for value in re.findall(r'(\d+)\s*(?:стр/мин|ppm)\b', lower):
            add(f'{int(value)}ppm', 65, False)

        for value in re.findall(r'(\d+)\s*(?:мбайт/сек|mb/s|мб/с)\b', lower):
            add(f'{int(value)}MB/s', 60, False)

        keyword_patterns = [
            (r'\b80\s*plus\s*(titanium|platinum|gold|silver|bronze|white)\b', lambda m: f"80 Plus {m.group(1).title()}", 90, True),
            (r'\bm\.?2\b', lambda _m: 'M.2', 90, True),
            (r'\bnvme\b', lambda _m: 'NVMe', 90, True),
            (r'\bssd\b', lambda _m: 'SSD', 90, True),
            (r'\bhdd\b', lambda _m: 'HDD', 90, True),
            (r'\bsata\s*3\b', lambda _m: 'SATA3', 88, True),
            (r'\bsata\b', lambda _m: 'SATA', 85, True),
            (r'\bpcie\s*([345](?:\.0)?)\b', lambda m: f"PCIe {m.group(1)}", 85, True),
            (r'\bddr\s*([345])\b', lambda m: f"DDR{m.group(1)}", 85, True),
            (r'\blga\s*(\d{3,4})\b', lambda m: f"LGA{m.group(1)}", 90, True),
            (r'\bam\s*([45])\b', lambda m: f"AM{m.group(1)}", 90, True),
            (r'\batx\b', lambda _m: 'ATX', 85, True),
            (r'\bmatx\b', lambda _m: 'mATX', 85, True),
            (r'\bmini-?itx\b', lambda _m: 'Mini-ITX', 85, True),
            (r'\bips\b', lambda _m: 'IPS', 80, True),
            (r'\bva\b', lambda _m: 'VA', 80, True),
            (r'\btn\b', lambda _m: 'TN', 80, True),
            (r'\bhdmi\b', lambda _m: 'HDMI', 80, True),
            (r'\bdisplayport\b|\bdp\b', lambda _m: 'DisplayPort', 80, True),
            (r'\busb\s*(3\.2|3\.1|3\.0|2\.0)\b', lambda m: f"USB {m.group(1)}", 80, True),
            (r'\busb-c\b|\btype[- ]?c\b', lambda _m: 'USB-C', 80, True),
            (r'\bwi[- ]?fi\s*6e\b', lambda _m: 'Wi-Fi 6E', 80, True),
            (r'\bwi[- ]?fi\s*6\b', lambda _m: 'Wi-Fi 6', 78, True),
            (r'\bwi[- ]?fi\s*5\b', lambda _m: 'Wi-Fi 5', 76, True),
            (r'\b3d\s*nand\b', lambda _m: '3D NAND', 65, False),
            (r'\ba([345])\b', lambda m: f"A{m.group(1)}", 75, True),
            (r'\bso-?dimm\b', lambda _m: 'SODIMM', 90, True),
            (r'\brdimm\b', lambda _m: 'RDIMM', 90, True),
            (r'\blrdimm\b', lambda _m: 'LRDIMM', 90, True),
            (r'\budimm\b', lambda _m: 'UDIMM', 90, True),
            (r'(?<!so-)\bdimm\b', lambda _m: 'DIMM', 90, True),
            (r'\becc\b', lambda _m: 'ECC', 90, True),
        ]
        for pattern, formatter, weight, hard in keyword_patterns:
            for match in re.finditer(pattern, lower):
                add(formatter(match), weight, hard)

        phrase_patterns = [
            (r'беспровод', 'беспроводная', 60, False),
            (r'оптическ', 'оптическая', 55, False),
            (r'механическ', 'механическая', 55, False),
            (r'мембран', 'мембранная', 55, False),
            (r'лазер', 'лазерный', 60, False),
            (r'струйн', 'струйный', 60, False),
            (r'ч/б|черно-бел', 'черно-белый', 60, False),
            (r'цветн', 'цветной', 60, False),
        ]
        for pattern, term, weight, hard in phrase_patterns:
            if re.search(pattern, lower):
                add(term, weight, hard)

        return found

    def _collect_query_terms(
        self,
        product_dict: dict,
        category: str,
        original: str,
        chars: dict,
    ) -> list[dict]:
        category_words = set(re.findall(r'[a-zа-я0-9]+', category.lower()))
        bucket: dict[str, dict] = {}
        counter = 0

        def add(term: str, weight: int, hard: bool) -> None:
            nonlocal counter
            norm = self._normalize_term_key(term)
            if not norm or norm in _GENERIC_QUERY_STOPWORDS:
                return
            if norm in {self._normalize_term_key(word) for word in category_words}:
                return
            entry = bucket.get(norm)
            if entry is None or weight > entry['weight']:
                bucket[norm] = {
                    'term': term.strip(),
                    'weight': weight,
                    'hard': hard,
                    'index': counter,
                }
            counter += 1

        model_terms = self._extract_model_terms(original)
        for term in model_terms:
            add(term, 110, False)
        for term in self._extract_brand_terms(original, category_words, model_terms):
            add(term, 100, False)
        for term, weight, hard in self._extract_facet_terms(original):
            add(term, weight, hard)
        for key, value in chars.items():
            text = f'{key}: {value}'
            key_lower = str(key).lower()
            value_lower = str(value).lower()

            if '80 plus' in key_lower:
                for grade in ('titanium', 'platinum', 'gold', 'silver', 'bronze', 'white'):
                    if grade in value_lower:
                        add(f'80 Plus {grade.title()}', 90, True)

            # Нормализуем ОС для ПК-категорий, только из поля "Операционная система"
            if category in _PC_CATEGORIES and key_lower == 'операционная система':
                os_token = _normalize_os_token(value_lower)
                if os_token:
                    add(os_token, 95, False)
                    continue  # не добавляем сырую строку ОС в запрос

            for term, weight, hard in self._extract_facet_terms(text):
                add(term, weight, hard)

        return sorted(
            bucket.values(),
            key=lambda item: (-item['weight'], item['index'], len(item['term'])),
        )

    def _title_matches_term(self, title_lower: str, term: str) -> bool:
        term_lower = term.lower()
        title_norm = self._normalize_term_key(title_lower)

        if term_lower in {'full hd', 'fhd'}:
            return bool(re.search(r'1920\s*[xх×]\s*1080|full\s*hd|fhd', title_lower))
        if term_lower in {'qhd', '2k'}:
            return bool(re.search(r'2560\s*[xх×]\s*1440|qhd|2k', title_lower))
        if term_lower in {'4k', 'uhd', 'ultrahd'}:
            return bool(re.search(r'3840\s*[xх×]\s*2160|4k|uhd|ultrahd', title_lower))

        # --- Pint numeric comparisons ---
        # 1. Capacity (ТВ, ГБ, МБ)
        if term_lower.endswith(('tb', 'gb', 'mb', 'тб', 'гб', 'мб')):
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(tb|gb|mb|тб|гб|мб)', term_lower)
            if match:
                req_qty = _parse_quantity(match.group(1), match.group(2))
                if req_qty:
                    for cap_value, cap_unit in RE_EXTRACT_CAPACITY.findall(title_lower):
                        act_qty = _parse_quantity(cap_value, cap_unit)
                        if act_qty and act_qty >= req_qty:
                            return True
            return any(token in title_lower for token in self._capacity_tokens(term))

        # 2. Power (ВТ, W)
        if term_lower.endswith(('w', 'вт')):
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(w|вт)', term_lower)
            if match:
                req_qty = _parse_quantity(match.group(1), match.group(2))
                if req_qty:
                    for p_value, p_unit in RE_EXTRACT_POWER.findall(title_lower):
                        act_qty = _parse_quantity(p_value, p_unit)
                        if act_qty and act_qty >= req_qty:
                            return True
                    num = match.group(1).replace(',', '.')
                    if num.endswith('.0'): num = num[:-2]
                    return any(token in title_lower for token in (f'{num}w', f'{num} вт', f'{num}вт'))

        # 3. Frequency (ГГЦ, МГЦ, ГЦ, GHZ, MHZ, HZ)
        if term_lower.endswith(('ghz', 'mhz', 'hz', 'ггц', 'мгц', 'гц')):
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*(ghz|mhz|hz|ггц|мгц|гц)', term_lower)
            if match:
                req_qty = _parse_quantity(match.group(1), match.group(2))
                if req_qty:
                    for f_value, f_unit in RE_EXTRACT_FREQ.findall(title_lower):
                        act_qty = _parse_quantity(f_value, f_unit)
                        if act_qty and act_qty >= req_qty:
                            return True

        # 4. Length (Дюймы)
        if term_lower.endswith(('"', 'дюйм', 'inch', 'дюйма', 'дюймов')):
            match = re.fullmatch(r'(\d+(?:[.,]\d+)?)\s*("|дюйм(?:а|ов)?|inch)', term_lower)
            if match:
                req_qty = _parse_quantity(match.group(1), 'inch')
                if req_qty:
                    for v_val in RE_EXTRACT_DIAGONAL.findall(title_lower):
                        act_qty = _parse_quantity(v_val, 'inch')
                        if act_qty and act_qty >= req_qty:
                            return True
                    num = match.group(1).replace(',', '.')
                    if num.endswith('.0'): num = num[:-2]
                    return any(token in title_lower for token in (f'{num}"', f'{num} "', f'{num} дюйм'))

        if term_lower == 'm.2':
            return 'm.2' in title_lower or 'm2' in title_lower
        if term_lower == 'usb-c':
            return any(token in title_lower for token in ('usb-c', 'usb c', 'type-c', 'type c'))
        if term_lower.startswith('wi-fi'):
            compact_term = term_lower.replace('-', '').replace(' ', '')
            compact_title = title_lower.replace('-', '').replace(' ', '')
            return compact_term in compact_title
        return self._normalize_term_key(term) in title_norm

    def _constraint_penalty(self, product_dict: dict, title: str) -> int:
        chars = product_dict.get('technical_requirements') or product_dict.get('characteristics') or {}
        if not isinstance(chars, dict):
            chars = {}

        category = self._infer_query_category(product_dict, str(product_dict.get('product_name', '')), chars)
        original = str(product_dict.get('original_product_name', '')).strip()
        hard_terms = [
            item['term']
            for item in self._collect_query_terms(product_dict, category, original, chars)
            if item['hard']
        ][:5]

        title_lower = title.lower()
        penalty = 0

        # Жёсткий фильтр по домену оборудования
        cat_lower = category.lower()
        hardware_domains = {
            'материнск': ['плата', 'материнская', 'mb', 'mainboard'],
            'процессор': ['процессор', 'cpu'],
            'кулер': ['кулер', 'охлажден', 'вентилятор', 'сжо'],
            'оперативная память': ['память', 'ram', 'dimm', 'sodimm'],
            'память': ['память', 'ram', 'dimm', 'sodimm'],
            'ssd': ['ssd', 'накопитель', 'твердотельн'],
            'hdd': ['hdd', 'накопитель', 'жесткий', 'диск'],
            'накопитель': ['ssd', 'hdd', 'накопитель', 'жесткий', 'диск', 'твердотельн'],
            'блок питания': ['блок питания', 'бп', 'psu'],
            'видеокарта': ['видеокарта', 'gpu'],
            'коммутатор': ['коммутатор', 'switch'],
            'корпус': ['корпус пк', 'корпус', 'chassis'],
            'системный блок': ['пк', 'компьютер', 'системный блок', 'desktop'],
            'моноблок': ['моноблок', 'aio', 'all-in-one'],
            'ноутбук': ['ноутбук', 'laptop'],
            'мфу': ['мфу', 'mfp', 'многофункциональн'],
            'принтер': ['принтер', 'printer'],
            'монитор': ['монитор', 'дисплей', 'экран'],
            'мышь': ['мышь', 'мышка', 'mouse'],
            'клавиатура': ['клавиатура', 'keyboard'],
        }
        for cat_key, required_tokens in hardware_domains.items():
            if cat_key in cat_lower:
                if not any(req in title_lower for req in required_tokens):
                    penalty += 50
                break

        for term in hard_terms:
            if not self._title_matches_term(title_lower, term):
                penalty += 4

        return penalty

    # ------------------------------------------------------------------ #
    #  LLM-судья: принимает список кандидатов, выбирает лучшего/дешевого  #
    # ------------------------------------------------------------------ #

    def _llm_judge_list(
        self,
        requirements: str,
        candidates: list[dict],
    ) -> dict | None:
        """
        Отправляет LLM весь список кандидатов. LLM выбирает лучший подходящий.
        Возвращает {"idx": N (1-based), "reason": "..."} или {"idx": 0} если ничего нет.
        """
        if not candidates:
            return None

        cand_lines = ""
        for i, c in enumerate(candidates, 1):
            used_str = " [б/у]" if c.get("is_used") else ""
            cand_lines += f"{i}. {c.get('title', '')}{used_str}\n"

        prompt = (
            f"Требования из технического задания: {requirements}\n\n"
            f"Список товаров из прайс-листа:\n{cand_lines}\n"
            "Задача:\n"
            "1. Найди товары, которые полностью соответствуют требованиям ТЗ.\n"
            "2. Если обязательный признак должен быть явно виден в названии товара (например: Gold, ATX, 2.5, SATA, 1Tb), но его нет в названии, считай товар неподходящим.\n"
            "3. Не додумывай отсутствующие характеристики по бренду, модели или общему классу товара.\n"
            "4. Товары с пометкой [б/у] — отклоняй, если ТЗ явно не разрешает б/у.\n"
            "5. Из подходящих товаров выбери наиболее точное совпадение с ТЗ.\n"
            "6. Если выбран товар, в поле reason кратко опиши, почему он соответствует.\n"
            "7. Если ни один товар не подходит, в поле reason укажи, что именно не совпало (например: нет IPS, недостаточно RAM, нет SSD 250 ГБ, частота CPU ниже, диагональ экрана меньше).\n"
            "8. Ответь ТОЛЬКО JSON без пояснений: "
            "{\"idx\": N, \"reason\": \"краткое обоснование\"} "
            "где N — номер товара в списке (от 1).\n"
            "Если ни один товар не подходит — {\"idx\": 0, \"reason\": \"причина\"}"
        )

        try:
            base = (LLM_BASE_URL or "http://localhost:11434").rstrip("/")
            resp = requests.post(
                f"{base}/api/generate",
                json={"model": LLM_MODEL, "prompt": prompt, "stream": False},
                timeout=90,
            )
            resp.raise_for_status()
            text = resp.json().get("response", "").strip()
            m = RE_JSON.search(text)
            if m:
                data = json.loads(m.group())
                return {"idx": int(data.get("idx", 0)), "reason": data.get("reason", "")}
        except Exception:
            pass
        return None

    # ------------------------------------------------------------------ #
    #  Поиск: гибридный (Dense + Sparse + RRF) + LLM re-ranking           #
    # ------------------------------------------------------------------ #

    def search_product(
        self,
        product_dict: dict,
        limit: int = 5,
        score_threshold: float = 0.0,
        use_llm_judge: bool = False,
    ) -> list[dict]:
        """
        Гибридный поиск товаров в Qdrant:
          1. Строим лаконичный запрос (только категория + бренд/модель).
          2. Получаем top-20 кандидатов через Dense + Sparse + RRF.
          3. Помечаем б/у товары (постфильтр по заголовку).
          4. LLM-судья анализирует весь список и выбирает лучшего кандидата.
        """
        query_text = self.build_query_text(product_dict)
        if not query_text:
            return []

        try:
            dense_vec = self._embed_dense([query_text])[0]
            sparse_vec = self._embed_sparse([query_text])[0]

            retrieval_limit = max(limit * 4, 20)
            search_result = self.client.query_points(
                collection_name=self.collection_name,
                prefetch=[
                    Prefetch(query=dense_vec, using="dense", limit=retrieval_limit),
                    Prefetch(query=sparse_vec, using="sparse", limit=retrieval_limit),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=retrieval_limit,
            )

            candidates: list[dict] = []
            for hit in search_result.points:
                pl = hit.payload or {}
                title = pl.get("title", "")
                title_lower = title.lower()
                is_used = any(marker in title_lower for marker in _USED_MARKERS)
                match_penalty = self._constraint_penalty(product_dict, title)
                candidates.append({
                    "score": float(hit.score or 0.0),
                    "rrf_score": float(hit.score or 0.0),
                    "title": title,
                    "price": pl.get("price"),
                    "link": pl.get("link"),
                    "sheet": pl.get("sheet"),
                    "vendor": pl.get("vendor", "e2e4"),
                    "query_text": query_text,
                    "is_used": is_used,
                    "match_penalty": match_penalty,
                    "llm_validated": False,
                })

            if not candidates:
                return []

            candidates.sort(
                key=lambda item: (
                    item["match_penalty"],
                    -(item["rrf_score"]),
                )
            )

            filtered_candidates = [item for item in candidates if item["match_penalty"] < 10]
            if filtered_candidates:
                candidates = filtered_candidates

            if use_llm_judge:
                # Предпочитаем новые товары, но если их нет — берём б/у тоже
                fresh = [c for c in candidates if not c["is_used"]]
                judge_pool = (fresh if fresh else candidates)[:10]

                chars = product_dict.get("technical_requirements") or {}
                req_lines = "; ".join(f"{k}: {v}" for k, v in list(chars.items())[:15])
                name = (
                    product_dict.get("original_product_name")
                    or product_dict.get("product_name", "")
                )
                requirements = f"{name}. {req_lines}" if req_lines else name

                result = self._llm_judge_list(
                    requirements=requirements,
                    candidates=judge_pool,
                )

                chosen_idx = (result.get("idx", 0) - 1) if result else -1
                if 0 <= chosen_idx < len(judge_pool):
                    chosen = judge_pool[chosen_idx]
                    chosen["llm_validated"] = True
                    chosen["llm_reason"] = result.get("reason", "") if result else ""
                    rest = [c for c in judge_pool if c is not chosen]
                    return [chosen] + rest[: max(0, limit - 1)]
                else:
                    # LLM не выбрала никого
                    if result and result.get("reason"):
                        judge_pool[0]["llm_reason"] = result.get("reason")
                    return judge_pool[:limit]

            return candidates[:limit]

        except Exception as e:
            return [{"error": str(e), "query_text": query_text}]

    # ------------------------------------------------------------------ #
    #  Индексация каталога                                                 #
    # ------------------------------------------------------------------ #

    def process_file(
        self,
        filepath: str,
        log_cb: Callable[[str], None] | None = None,
        progress_cb: Callable[[int, int], None] | None = None,
        task_id: str | None = None,
        cancel_cb: Callable[[], bool] | None = None,
    ):
        if filepath.endswith('.zip'):
            with zipfile.ZipFile(filepath, 'r') as z:
                csv_files = [f for f in z.namelist() if f.endswith('.csv')]
                if not csv_files:
                    raise ValueError("В архиве нет .csv")
                with z.open(csv_files[0]) as f:
                    df = pd.read_csv(f, sep=';', on_bad_lines='skip', low_memory=False)
        else:
            df = pd.read_csv(filepath, sep=';', on_bad_lines='skip', low_memory=False)

        # Выбор колонки с названиями
        title_col = None
        for cand in ['Name', 'Product_Name', 'Title', 'Item',
                     'Наименование', 'Название товара', 'Наименование товара', 'Товар']:
            if cand in df.columns:
                title_col = cand
                break
        if title_col is None:
            for cand in df.columns:
                if cand.lower() not in {'source_file', 'sheet'}:
                    title_col = cand
                    break

        titles = df[title_col].dropna().astype(str).tolist() if title_col else []
        if not titles:
            return 0

        total_titles = len(titles)
        if log_cb:
            log_cb(f"📦 Найдено {total_titles} позиций для индексации")

        # Дополнительные поля из CSV
        prices = df["Price"].tolist() if "Price" in df.columns else [None] * len(df)
        links = df["Link"].tolist() if "Link" in df.columns else [None] * len(df)
        sheets = df["sheet"].tolist() if "sheet" in df.columns else [None] * len(df)

        self._ensure_collection(recreate=True)

        batch_size = 128
        points_buffer: list[PointStruct] = []
        total_indexed = 0

        for batch_start in range(0, total_titles, batch_size):
            if cancel_cb and cancel_cb():
                if log_cb:
                    log_cb("⚠️ Обработка отменена")
                self._delete_task_points(task_id)
                return 0

            batch_end = min(batch_start + batch_size, total_titles)
            batch_titles = titles[batch_start:batch_end]

            dense_vecs = self._embed_dense(batch_titles)
            sparse_vecs = self._embed_sparse(batch_titles)

            for i, (title, d_vec, s_vec) in enumerate(zip(batch_titles, dense_vecs, sparse_vecs)):
                payload: dict = {"title": title, "vendor": "e2e4"}
                try:
                    p = prices[batch_start + i]
                    if p is not None and str(p).replace('.', '', 1).replace(',', '', 1).isdigit():
                        payload["price"] = float(str(p).replace(',', '.'))
                except Exception:
                    pass
                lnk = links[batch_start + i]
                if lnk and str(lnk) != 'nan':
                    payload["link"] = str(lnk)
                sht = sheets[batch_start + i]
                if sht and str(sht) != 'nan':
                    payload["sheet"] = str(sht)
                if task_id:
                    payload["task_id"] = task_id

                points_buffer.append(PointStruct(
                    id=str(uuid.uuid4()),
                    vector={"dense": d_vec, "sparse": s_vec},
                    payload=payload,
                ))

            if len(points_buffer) >= 512:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=points_buffer,
                )
                total_indexed += len(points_buffer)
                points_buffer = []

            if progress_cb:
                progress_cb(batch_end, total_titles)
            if log_cb and batch_end % 10000 == 0:
                log_cb(f"  ... проиндексировано {batch_end}/{total_titles}")

        if points_buffer:
            self.client.upsert(collection_name=self.collection_name, points=points_buffer)
            total_indexed += len(points_buffer)

        if progress_cb:
            progress_cb(total_titles, total_titles)
        if log_cb:
            log_cb(f"✅ Проиндексировано {total_indexed} позиций")

        return total_indexed

    def _delete_task_points(self, task_id: str | None) -> None:
        if not task_id:
            return
        try:
            self.client.delete(
                collection_name=self.collection_name,
                points_selector=Filter(
                    must=[FieldCondition(key="task_id", match=MatchValue(value=task_id))]
                ),
            )
        except Exception:
            pass
