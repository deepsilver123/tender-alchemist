"""Web search module for tender product matching.

Pipeline:
1. generate_search_queries()  — Ministral (lightweight model) generates 2-3 search queries
2. search_searxng()           — SearXNG JSON API returns raw results
3. match_products()           — Ministral analyses results against technical_requirements

Designed to be called from analysis_worker.py as Stage 5.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import requests

from core.config import (
    MINISTRAL_BASE_URL,
    SEARCH_MAX_RESULTS,
    SEARCH_MODEL,
    SEARCH_NUM_CTX,
    SEARCH_NUM_PREDICT,
    SEARCH_PROMPT,
    MATCH_PROMPT,
    SEARXNG_URL,
    SEARXNG_TIMEOUT,
    MINISTRAL_TEMPERATURE,
)

logger = logging.getLogger("tender")

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _call_model(prompt: str, model: str, num_ctx: int, num_predict: int) -> str | None:
    """Minimal synchronous Ollama call (no retry logic needed for short tasks)."""
    url = f"{MINISTRAL_BASE_URL}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {
            "temperature": MINISTRAL_TEMPERATURE,
            "num_ctx": num_ctx,
            "num_predict": num_predict,
        },
    }
    try:
        resp = requests.post(url, json=payload, timeout=(10, 120))
        if resp.status_code != 200:
            logger.warning("[WebSearch] Ollama HTTP %s: %s", resp.status_code, resp.text[:200])
            return None
        data = resp.json()
        # Handle Ollama format
        msg = data.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if content:
            return content
        # Handle OpenAI-compat format
        choices = data.get("choices") or []
        if choices:
            return (choices[0].get("message") or {}).get("content")
    except Exception as exc:
        logger.warning("[WebSearch] Ollama call failed: %s", exc)
    return None


def _extract_json_list(text: str) -> list:
    """Extract the first JSON array from model output."""
    if not text:
        return []
    text = text.strip()
    # Find first [ ... ]
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1 or end <= start:
        return []
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.debug("[WebSearch] JSON parse failed for: %s", text[start : end + 1][:200])
        return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_search_queries(
    product: dict[str, Any],
    log_cb=None,
) -> list[str]:
    """Generate 2-3 search queries for a product using the lightweight model."""
    product_json = json.dumps(product, ensure_ascii=False, indent=2)
    prompt = SEARCH_PROMPT.replace("{product_json}", product_json)

    t0 = time.time()
    raw = _call_model(prompt, SEARCH_MODEL, SEARCH_NUM_CTX, SEARCH_NUM_PREDICT)
    elapsed = time.time() - t0

    queries = _extract_json_list(raw or "")
    queries = [q for q in queries if isinstance(q, str) and q.strip()]

    msg = f"[WebSearch] Запросы сгенерированы за {elapsed:.1f} сек: {queries}"
    logger.info(msg)
    if callable(log_cb):
        log_cb(msg)

    return queries


def search_searxng(
    queries: list[str],
    max_results: int = SEARCH_MAX_RESULTS,
    log_cb=None,
) -> list[dict[str, Any]]:
    """Search SearXNG for each query and return deduplicated results."""
    seen_urls: set[str] = set()
    combined: list[dict[str, Any]] = []

    for query in queries:
        try:
            resp = requests.get(
                f"{SEARXNG_URL}/search",
                params={
                    "q": query,
                    "format": "json",
                    "categories": "general",
                    "language": "ru",
                },
                timeout=SEARXNG_TIMEOUT,
            )
            if resp.status_code != 200:
                logger.warning("[WebSearch] SearXNG HTTP %s for query: %s", resp.status_code, query)
                continue
            results = resp.json().get("results", [])
            for r in results:
                url = r.get("url", "")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    combined.append({
                        "title": r.get("title", ""),
                        "url": url,
                        "content": r.get("content", ""),
                        "engine": r.get("engine", ""),
                    })
        except Exception as exc:
            logger.warning("[WebSearch] SearXNG error for query '%s': %s", query, exc)

    results_trimmed = combined[:max_results]
    msg = f"[WebSearch] SearXNG: найдено {len(combined)} результатов, используем {len(results_trimmed)}"
    logger.info(msg)
    if callable(log_cb):
        log_cb(msg)

    return results_trimmed


def match_products(
    product: dict[str, Any],
    search_results: list[dict[str, Any]],
    log_cb=None,
) -> list[dict[str, Any]]:
    """Use Ministral to match search results against technical requirements."""
    if not search_results:
        return []

    # Format results as numbered list for the prompt
    formatted = "\n\n".join(
        f"{i+1}. {r['title']}\nURL: {r['url']}\n{r['content']}"
        for i, r in enumerate(search_results)
    )

    technical_requirements = json.dumps(
        product.get("technical_requirements", {}), ensure_ascii=False, indent=2
    )
    price_per_unit = product.get("commercial_terms", {}).get("price_per_unit", "не указана")

    prompt = (
        MATCH_PROMPT
        .replace("{technical_requirements}", technical_requirements)
        .replace("{search_results}", formatted)
        .replace("{price_per_unit}", str(price_per_unit))
    )

    # Use the main model for verification — it handles complex reasoning better
    from core.config import MINISTRAL_MODEL, MINISTRAL_NUM_CTX, MINISTRAL_NUM_PREDICT
    t0 = time.time()
    raw = _call_model(prompt, MINISTRAL_MODEL, MINISTRAL_NUM_CTX, MINISTRAL_NUM_PREDICT)
    elapsed = time.time() - t0

    matches = _extract_json_list(raw or "")
    matches = [m for m in matches if isinstance(m, dict)]

    msg = f"[WebSearch] Сопоставление завершено за {elapsed:.1f} сек, найдено моделей: {len(matches)}"
    logger.info(msg)
    if callable(log_cb):
        log_cb(msg)

    return matches


def find_matching_models(
    product: dict[str, Any],
    log_cb=None,
) -> list[dict[str, Any]]:
    """Full pipeline: generate queries → search → match. Returns list of matched models."""
    product_name = product.get("product_name", "продукт")

    if callable(log_cb):
        log_cb(f"🔍 Поиск моделей для: {product_name}")

    queries = generate_search_queries(product, log_cb=log_cb)
    if not queries:
        if callable(log_cb):
            log_cb(f"⚠️ Не удалось сгенерировать поисковые запросы для: {product_name}")
        return []

    results = search_searxng(queries, log_cb=log_cb)
    if not results:
        if callable(log_cb):
            log_cb(f"⚠️ SearXNG не вернул результатов для: {product_name}")
        return []

    matches = match_products(product, results, log_cb=log_cb)

    if callable(log_cb):
        if matches:
            log_cb(f"✅ {product_name}: найдено {len(matches)} подходящих моделей")
        else:
            log_cb(f"❌ {product_name}: подходящих моделей в бюджете не найдено")

    return matches
