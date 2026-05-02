"""Синхронный пайплайн анализа для веб-интерфейса.

Должен вызываться через run_in_executor, чтобы event loop FastAPI оставался свободным.
Весь файловый и HTTP ввод-вывод синхронный — asyncio внутри модуля не используется.
`send_log` вызывается напрямую и должен быть потокобезопасным (планируется в основной
цикл через asyncio.run_coroutine_threadsafe на стороне вызывающего кода в app).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable, List, Optional


def run_analysis(
    task_id: str,
    files: List[str],
    send_log: Callable[[str], None],
    llm_url: Optional[str] = None,
    llm_model: Optional[str] = None,
    docling_base: Optional[str] = None,
) -> dict[str, Any]:
    """Synchronous analysis pipeline. Run via run_in_executor from app.

    send_log is called directly from this thread — the caller (app) wraps
    it with asyncio.run_coroutine_threadsafe so WS broadcasts reach the client
    in real time while this function runs.
    """
    from core.config import (
        TENDER_DOCUMENT_JSON_PROMPT,
        LLM_URL,
        LLM_MODEL,
        LLM_API_KEY,
        LLM_TEMPERATURE,
        LLM_NUM_CTX,
        LLM_NUM_PREDICT,
        LOG_DIR,
    )
    from core.llm_client import call_llm
    from core.json_utils import extract_json_from_text

    llm_url = llm_url or LLM_URL
    llm_model = llm_model or LLM_MODEL

    # ── Этап 1: чтение файлов ────────────────────────────────────────────
    send_log(f"📌 Этап 1/5: чтение {len(files)} файлов")
    try:
        from core.document_parser import extract_text_from_file
    except Exception:
        extract_text_from_file = None

    combined_parts: list[str] = []
    for fp in files:
        send_log(f"Читаю {fp}")
        text = ""
        try:
            if extract_text_from_file:
                text = extract_text_from_file(fp, docling_base, None)
            else:
                p = Path(fp)
                if p.suffix.lower() == ".docx":
                    try:
                        from core.docx_parser import extract_from_docx
                        text = extract_from_docx(fp)
                    except Exception:
                        text = ""
                else:
                    try:
                        text = Path(fp).read_text(encoding="utf-8", errors="ignore")
                    except Exception:
                        text = ""
        except Exception as e:
            send_log(f"❌ Ошибка при чтении {fp}: {e}")
            text = ""
        combined_parts.append(text)
        send_log(f"✅ {Path(fp).name}: {len(text)} символов")

    combined_text = "\n".join(p for p in combined_parts if p)

    # Persist task folder so prompt/raw/result can be written there.
    task_log_dir = LOG_DIR / task_id
    task_log_dir.mkdir(parents=True, exist_ok=True)
    send_log(f"✅ Этап 1/5 завершён: {len(combined_text)} символов суммарно")

    # ── Этап 2: сборка prompt ────────────────────────────────────────────
    send_log("📌 Этап 2/5: сборка итогового prompt")
    full_prompt = f"{TENDER_DOCUMENT_JSON_PROMPT}\n\n{combined_text}"
    send_log(f"✅ Этап 2/5 завершён: длина prompt={len(full_prompt)} символов")

    prompt_file: Optional[Path] = None
    try:
        (task_log_dir / "prompt.html").write_text(full_prompt, encoding="utf-8")
        send_log(f"📁 prompt сохранён в лог: {task_log_dir / 'prompt.html'}")
    except Exception as e:
        send_log(f"⚠️ Не удалось сохранить prompt в лог: {e}")

    # ── Этап 3: вызов модели ─────────────────────────────────────────────
    send_log("📌 Этап 3/5: отправка prompt в LLM API")
    send_log(f"🧠 Модель: {llm_model}; URL: {llm_url}")

    ai_start = time.time()
    model_resp: Optional[str] = None
    raw_file: Optional[Path] = None
    try:
        model_resp = call_llm(
            full_prompt,
            model=llm_model,
            base_url=llm_url,
            api_key=LLM_API_KEY,
            temperature=LLM_TEMPERATURE,
            num_ctx=LLM_NUM_CTX,
            num_predict=LLM_NUM_PREDICT,
        )
    except Exception as e:
        send_log(f"❌ Ошибка вызова модели: {e}")

    ai_time = time.time() - ai_start
    parsed: dict = {}

    if not model_resp:
        send_log(f"❌ Этап 3/5: AI не вернул ответ ({ai_time:.2f} сек) — проверьте URL и модель")
    else:
        send_log(f"✅ Этап 3/5 завершён: ответ получен за {ai_time:.2f} сек")
        try:
            (task_log_dir / "raw_answer.log").write_text(model_resp, encoding="utf-8")
            raw_file = task_log_dir / "raw_answer.log"
            send_log(f"📁 Raw сохранён в лог: {raw_file}")
        except Exception as e:
            raw_file = None
            send_log(f"⚠️ Не удалось сохранить raw в лог: {e}")

        send_log("Извлекаю JSON из ответа модели")
        parsed = extract_json_from_text(model_resp) or {}
        if not parsed:
            send_log("⚠️ Не удалось извлечь JSON из ответа модели")

    # ── Этап 4: нормализация и сохранение ───────────────────────────────
    send_log("📌 Этап 4/5: нормализация и сохранение результата")
    try:
        from core import normalize_products
        parsed = normalize_products(parsed)
    except Exception:
        pass

    try:
        with open(task_log_dir / "result.json", "w", encoding="utf-8") as fh:
            json.dump(parsed, fh, ensure_ascii=False, indent=2)
        send_log(f"✅ Этап 4/5 завершён: результат сохранён в лог: {task_log_dir / 'result.json'}")
    except Exception as e:
        send_log(f"❌ Ошибка сохранения результата в лог: {e}")

    # ── Этап 5: Поиск в векторной базе Qdrant ────────────────────────────
    send_log("📌 Этап 5/5: точный поиск товаров в Qdrant (по прайс-листам)")
    search_results: dict = {}
    products_list = parsed.get("products", []) if isinstance(parsed, dict) else []
    
    if not products_list:
        send_log("⚠️ Этап 5/5: нет извлечённых товаров — пропускаем")
    else:
        try:
            from core.qdrant_indexer import TenderMVPQdrant
            qdrant = TenderMVPQdrant()  # использует конфиги по-умолчанию (QDRANT_URL, etc.)
            
            for product in products_list:
                name = product.get("product_name") or "Неизвестный товар"
                send_log(f"🔍 Ищем записи в Qdrant для: {name}")

                query_text = qdrant.build_query_text(product)
                if query_text:
                    send_log(f"🔎 Search query: {query_text}")
                else:
                    send_log("🔎 Search query: пустой запрос")
                
                # Ищем совпадения в базе (берём до 3 самых релевантных совпадений)
                matches = qdrant.search_product(product, limit=3, use_llm_judge=True)
                search_results[name] = matches
                
                if matches and "error" not in matches[0]:
                    send_log(f"📚 Кандидатов найдено: {len(matches)}")
                    for index, match in enumerate(matches, 1):
                        validated = match.get('llm_validated', False)
                        price_str = f", цена: {match['price']:.0f} руб." if match.get('price') else ""
                        judge_str = " ✅ LLM: подходит" if validated else " ❌ LLM: не подходит"
                        reason_str = f" ({match.get('llm_reason')})" if match.get('llm_reason') and not validated else ""
                        penalty_str = f", penalty: {match.get('match_penalty', 0)}" if match.get('match_penalty') is not None else ""
                        send_log(
                            f"   {index}. {match['title']} "
                            f"(RRF: {match.get('rrf_score', match.get('score', 0.0)):.4f}{price_str}{penalty_str}{judge_str}{reason_str})"
                        )

                    best = matches[0]
                    if best.get('llm_validated', False):
                        send_log(f"🎯 Выбран: {best['title']}")
                    else:
                        if best.get('llm_reason'):
                            send_log(f"⚠️ LLM: не нашла подходящий товар ({best.get('llm_reason')})")
                        else:
                            send_log("⚠️ Среди top-кандидатов LLM не нашла полностью подходящий товар")
                elif matches and "error" in matches[0]:
                    send_log(f"❌ Ошибка поиска для '{name}': {matches[0]['error']}")
                else:
                    send_log(f"⚠️ Совпадений в Qdrant не найдено")
                    
        except Exception as e:
            send_log(f"❌ Ошибка при работе с векторной базой данных: {e}")

    if search_results:
        try:
            with open(task_log_dir / "search_results.json", "w", encoding="utf-8") as fh:
                json.dump(search_results, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            send_log(f"⚠️ Не удалось сохранить результаты поиска: {e}")

    send_log("✅ Этап 5/5 завершён")

    return {
        "parsed": parsed,
        "search_results": search_results,
        "result_path": None,
        "prompt_path": None,
        "raw_path": str(raw_file) if raw_file else None,
    }


def _search_products(parsed: dict, send_log: Any, llm_url: str | None, llm_model: str | None) -> dict[str, Any]:
    from core.qdrant_indexer import TenderMVPQdrant

    search_results: dict = {}
    products_list = parsed.get("products", []) if isinstance(parsed, dict) else []

    if not products_list:
        send_log("⚠️ Этап 3/3: нет товаров в JSON — поиск по Qdrant пропущен")
        return {"parsed": parsed, "search_results": search_results}

    send_log("📌 Этап 3/3: поиск товаров в Qdrant (по прайс-листам)")
    try:
        qdrant = TenderMVPQdrant()
        for product in products_list:
            name = product.get("product_name") or "Неизвестный товар"
            send_log(f"🔍 Ищем записи в Qdrant для: {name}")

            query_text = qdrant.build_query_text(product)
            if query_text:
                send_log(f"🔎 Search query: {query_text}")
            else:
                send_log("🔎 Search query: пустой запрос")

            matches = qdrant.search_product(product, limit=3, use_llm_judge=True)
            search_results[name] = matches

            if matches and "error" not in matches[0]:
                send_log(f"📚 Кандидатов найдено: {len(matches)}")
                for index, match in enumerate(matches, 1):
                    validated = match.get('llm_validated', False)
                    price_str = f", цена: {match['price']:.0f} руб." if match.get('price') else ""
                    judge_str = " ✅ LLM: подходит" if validated else " ❌ LLM: не подходит"
                    penalty_str = f", penalty: {match.get('match_penalty', 0)}" if match.get('match_penalty') is not None else ""
                    send_log(
                        f"   {index}. {match['title']} "
                        f"(RRF: {match.get('rrf_score', match.get('score', 0.0)):.4f}{price_str}{penalty_str}{judge_str})"
                    )

                best = matches[0]
                if best.get('llm_validated', False):
                    send_log(f"🎯 Выбран: {best['title']}")
                else:
                    send_log("⚠️ Среди top-кандидатов LLM не нашла полностью подходящий товар")
            elif matches and "error" in matches[0]:
                send_log(f"❌ Ошибка поиска для '{name}': {matches[0]['error']}")
            else:
                send_log(f"⚠️ Совпадений в Qdrant не найдено")
    except Exception as e:
        send_log(f"❌ Ошибка при работе с векторной базой данных: {e}")

    return {"parsed": parsed, "search_results": search_results}


def run_search_json(
    task_id: str,
    parsed_json: dict,
    send_log: Any,
    llm_url: str | None = None,
    llm_model: str | None = None,
) -> dict[str, Any]:
    from core.config import LOG_DIR

    task_log_dir = LOG_DIR / task_id
    task_log_dir.mkdir(parents=True, exist_ok=True)

    send_log("📌 Этап 1/3: подготовка JSON")
    try:
        with open(task_log_dir / "input.json", "w", encoding="utf-8") as fh:
            json.dump(parsed_json, fh, ensure_ascii=False, indent=2)
        send_log("✅ Этап 1/3 завершён: JSON сохранён")
    except Exception as e:
        send_log(f"⚠️ Не удалось сохранить JSON: {e}")

    send_log("📌 Этап 2/3: нормализация результата")
    parsed = parsed_json if isinstance(parsed_json, dict) else {}
    try:
        from core import normalize_products
        parsed = normalize_products(parsed)
    except Exception:
        pass

    try:
        with open(task_log_dir / "result.json", "w", encoding="utf-8") as fh:
            json.dump(parsed, fh, ensure_ascii=False, indent=2)
        send_log("✅ Этап 2/3 завершён: результат сохранён")
    except Exception as e:
        send_log(f"❌ Ошибка сохранения результата в лог: {e}")

    result = _search_products(parsed, send_log, llm_url, llm_model)

    if result.get('search_results'):
        try:
            with open(task_log_dir / "search_results.json", "w", encoding="utf-8") as fh:
                json.dump(result['search_results'], fh, ensure_ascii=False, indent=2)
        except Exception as e:
            send_log(f"⚠️ Не удалось сохранить результаты поиска: {e}")

    send_log("✅ Этап 3/3 завершён")

    return {
        "parsed": result.get("parsed", parsed),
        "search_results": result.get("search_results", {}),
        "result_path": None,
        "prompt_path": None,
        "raw_path": None,
    }
