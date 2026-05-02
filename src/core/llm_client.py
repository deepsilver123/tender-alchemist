import asyncio
import logging
from functools import partial
from typing import Optional, List, Dict, Any

import requests

from .config import (
    LLM_URL,
    LLM_API_KEY,
    LLM_MODEL,
    LLM_TEMPERATURE,
    LLM_NUM_CTX,
    LLM_NUM_PREDICT,
)

logger = logging.getLogger("tender")


def _extract_content(resp_json: Dict[str, Any]) -> Optional[str]:
    if not isinstance(resp_json, dict):
        return None
    # Форматы ответа Ollama/Ministral: {"message": {"content": "..."}} или {"choices": [{"message": {...}}]}
    if "message" in resp_json and isinstance(resp_json["message"], dict):
        return resp_json["message"].get("content")
    choices = resp_json.get("choices")
    if isinstance(choices, list) and choices:
        first = choices[0]
        if isinstance(first, dict):
            if "message" in first and isinstance(first["message"], dict):
                return first["message"].get("content")
            return first.get("text") or first.get("content")
    return None


def call_ollama(
    prompt: str,
    model: str = "ministral-3:3b",
    base_url: str = "http://localhost:3000/ollama/api",
    api_key: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    temperature: float = 0.1,
    num_ctx: int = 16384,
    num_predict: int = 8192,
) -> Optional[str]:
    """Синхронный вызов Ollama/Ministral-совместимого эндпоинта.

    Возвращает строку ответа ассистента при успехе или None при ошибке.
    """
    url = f"{base_url.rstrip('/')}/chat"

    headers: Dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if messages is None:
        messages = [{"role": "user", "content": prompt}]

    def build_payload(ctx: int) -> Dict[str, Any]:
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": ctx,
            },
        }

    try:
        resp = requests.post(url, headers=headers, json=build_payload(num_ctx), timeout=(30, 600))
        resp.raise_for_status()
        result = resp.json()
        return _extract_content(result)
    except requests.exceptions.RequestException as e:
        try:
            logger.error("Ошибка соединения с Ollama: %s", e)
        except Exception:
            pass

        # Если сервер упал из-за нехватки памяти при большом контексте — повторяем с меньшим
        resp_obj = getattr(e, "response", None)
        if resp_obj is not None:
            try:
                error_text = (resp_obj.text or "").lower()
            except Exception:
                error_text = ""
            if resp_obj.status_code == 500 and "signal: killed" in error_text and num_ctx > 8192:
                fallback_ctx = max(8192, num_ctx // 2)
                try:
                    logger.info("[Ollama] Повтор с уменьшенным ctx: %s -> %s", num_ctx, fallback_ctx)
                except Exception:
                    pass
                try:
                    retry_resp = requests.post(url, headers=headers, json=build_payload(fallback_ctx), timeout=(30, 600))
                    retry_resp.raise_for_status()
                    retry_result = retry_resp.json()
                    return _extract_content(retry_result)
                except Exception:
                    try:
                        logger.error("[Ollama] Повторный запрос не удался")
                    except Exception:
                        pass
        return None
    except ValueError:
        try:
            logger.error("Ошибка парсинга JSON от Ollama")
        except Exception:
            pass
        return None


def call_ministral(
    prompt: str,
    api_key: Optional[str] = None,
    model: str = "ministral-3:3b",
    base_url: str = "http://localhost:3000/ollama/api",
    messages: Optional[List[dict]] = None,
    temperature: float = 0.1,
    num_ctx: int = 16384,
    num_predict: int = 8192,
) -> Optional[str]:
    """Обёртка совместимости для Ministral; использует Ollama-совместимый эндпоинт."""
    return call_ollama(
        prompt,
        model=model,
        base_url=base_url,
        api_key=api_key,
        messages=messages,
        temperature=temperature,
        num_ctx=num_ctx,
        num_predict=num_predict,
    )


def call_llm(
    prompt: str,
    model: str = "ministral-3:3b",
    base_url: str = "http://localhost:3000/ollama/api",
    api_key: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    temperature: float = 0.1,
    num_ctx: int = 16384,
    num_predict: int = 8192,
) -> Optional[str]:
    """Устаревшая обёртка совместимости для call_llm."""
    return call_ministral(
        prompt,
        api_key=api_key,
        model=model,
        base_url=base_url,
        messages=messages,
        temperature=temperature,
        num_ctx=num_ctx,
        num_predict=num_predict,
    )


async def call_model(
    prompt: str,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    messages: Optional[List[dict]] = None,
    temperature: Optional[float] = None,
    num_ctx: Optional[int] = None,
    num_predict: Optional[int] = None,
) -> str:
    """Асинхронная обёртка для пайплайнов анализа. Блокирующий HTTP-вызов запускается в потоке.

    Возвращает строку ответа ассистента при успехе или пустую строку при ошибке.
    """
    model = model or LLM_MODEL
    api_key = api_key or LLM_API_KEY
    base_url = base_url or LLM_URL
    temperature = temperature if temperature is not None else LLM_TEMPERATURE
    num_ctx = num_ctx or LLM_NUM_CTX
    num_predict = num_predict or LLM_NUM_PREDICT

    try:
        func = partial(
            call_ministral,
            prompt,
            api_key=api_key,
            model=model,
            base_url=base_url,
            messages=messages,
            temperature=temperature,
            num_ctx=num_ctx,
            num_predict=num_predict,
        )
        result = await asyncio.to_thread(func)
        if result is None:
            return ""
        return str(result)
    except Exception as e:
        try:
            logger.error("call_model error: %s", e)
        except Exception:
            pass
        return ""
