"""Synchronous analysis pipeline for the web UI.

Must be called from run_in_executor so the FastAPI event loop stays free.
All file I/O and HTTP is synchronous — no asyncio inside this module.
`send_log` is called directly and must be thread-safe (scheduled on main loop
via asyncio.run_coroutine_threadsafe by the caller in app_impl).
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
    ministral_url: Optional[str] = None,
    ministral_model: Optional[str] = None,
    docling_base: Optional[str] = None,
) -> dict[str, Any]:
    """Synchronous analysis pipeline. Run via run_in_executor from app_impl.

    send_log is called directly from this thread — the caller (app_impl) wraps
    it with asyncio.run_coroutine_threadsafe so WS broadcasts reach the client
    in real time while this function runs.
    """
    from core.config import (
        MINISTRAL_PROMPT,
        MINISTRAL_URL,
        MINISTRAL_MODEL,
        MINISTRAL_API_KEY,
        MINISTRAL_TEMPERATURE,
        MINISTRAL_NUM_CTX,
        MINISTRAL_NUM_PREDICT,
        LOG_DIR,
    )
    from core.ministral_client import call_ministral
    from core.json_utils import extract_json_from_text

    ministral_url = ministral_url or MINISTRAL_URL
    ministral_model = ministral_model or MINISTRAL_MODEL

    # ── Этап 1: чтение файлов ────────────────────────────────────────────
    send_log(f"📌 Этап 1/5: чтение {len(files)} файлов")
    try:
        from core.file_reader import extract_text_from_file
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
    send_log("📌 Этап 2/4: сборка итогового prompt")
    full_prompt = f"{MINISTRAL_PROMPT}\n\n{combined_text}"
    send_log(f"✅ Этап 2/4 завершён: длина prompt={len(full_prompt)} символов")

    prompt_file: Optional[Path] = None
    try:
        (task_log_dir / "prompt.html").write_text(full_prompt, encoding="utf-8")
        send_log(f"📁 prompt сохранён в лог: {task_log_dir / 'prompt.html'}")
    except Exception as e:
        send_log(f"⚠️ Не удалось сохранить prompt в лог: {e}")

    # ── Этап 3: вызов модели ─────────────────────────────────────────────
    send_log("📌 Этап 3/4: отправка prompt в Ministral API")
    send_log(f"🧠 Модель: {ministral_model}; URL: {ministral_url}")

    ai_start = time.time()
    model_resp: Optional[str] = None
    raw_file: Optional[Path] = None
    try:
        model_resp = call_ministral(
            full_prompt,
            model=ministral_model,
            base_url=ministral_url,
            api_key=MINISTRAL_API_KEY,
            temperature=MINISTRAL_TEMPERATURE,
            num_ctx=MINISTRAL_NUM_CTX,
            num_predict=MINISTRAL_NUM_PREDICT,
        )
    except Exception as e:
        send_log(f"❌ Ошибка вызова модели: {e}")

    ai_time = time.time() - ai_start
    parsed: dict = {}

    if not model_resp:
        send_log(f"❌ Этап 3/4: AI не вернул ответ ({ai_time:.2f} сек) — проверьте URL и модель")
    else:
        send_log(f"✅ Этап 3/4 завершён: ответ получен за {ai_time:.2f} сек")
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
    send_log("📌 Этап 4/4: нормализация и сохранение результата")
    try:
        from core import normalize_products
        parsed = normalize_products(parsed)
    except Exception:
        pass

    try:
        with open(task_log_dir / "result.json", "w", encoding="utf-8") as fh:
            json.dump(parsed, fh, ensure_ascii=False, indent=2)
        send_log(f"✅ Результат сохранён в лог: {task_log_dir / 'result.json'}")
    except Exception as e:
        send_log(f"❌ Ошибка сохранения результата в лог: {e}")

    return {
        "parsed": parsed,
        "result_path": None,
        "prompt_path": None,
        "raw_path": str(raw_file) if raw_file else None,
    }
