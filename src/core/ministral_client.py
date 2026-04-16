import asyncio
import logging
from functools import partial
from typing import Optional, List, Dict, Any

import requests

from .config import (
    MINISTRAL_URL,
    MINISTRAL_API_KEY,
    MINISTRAL_MODEL,
    MINISTRAL_TEMPERATURE,
    MINISTRAL_NUM_CTX,
    MINISTRAL_NUM_PREDICT,
)

logger = logging.getLogger("tender")


def _extract_content(resp_json: Dict[str, Any]) -> Optional[str]:
    if not isinstance(resp_json, dict):
        return None
    # Common ollama/ministral formats: {"message": {"content": "..."}} or {"choices": [{"message": {...}}]}
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
    """Synchronous call to an Ollama/Ministral-compatible endpoint.

    Returns the assistant content string on success or ``None`` on failure.
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

        # If server OOM'd on large context, try a smaller ctx as a retry
        resp_obj = getattr(e, "response", None)
        if resp_obj is not None:
            try:
                error_text = (resp_obj.text or "").lower()
            except Exception:
                error_text = ""
            if resp_obj.status_code == 500 and "signal: killed" in error_text and num_ctx > 8192:
                fallback_ctx = max(8192, num_ctx // 2)
                try:
                    logger.info("[Ollama] Retry with reduced ctx: %s -> %s", num_ctx, fallback_ctx)
                except Exception:
                    pass
                try:
                    retry_resp = requests.post(url, headers=headers, json=build_payload(fallback_ctx), timeout=(30, 600))
                    retry_resp.raise_for_status()
                    retry_result = retry_resp.json()
                    return _extract_content(retry_result)
                except Exception:
                    try:
                        logger.error("[Ollama] Retry failed")
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
    """Compatibility wrapper for Ministral; currently uses the Ollama-compatible endpoint."""
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
    """Async wrapper used by analysis pipelines. Runs blocking HTTP call in a thread.

    Returns the assistant string on success or an empty string on failure.
    """
    model = model or MINISTRAL_MODEL
    api_key = api_key or MINISTRAL_API_KEY
    base_url = base_url or MINISTRAL_URL
    temperature = temperature if temperature is not None else MINISTRAL_TEMPERATURE
    num_ctx = num_ctx or MINISTRAL_NUM_CTX
    num_predict = num_predict or MINISTRAL_NUM_PREDICT

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
