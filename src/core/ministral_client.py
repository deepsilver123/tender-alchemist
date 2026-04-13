import asyncio
import requests
import json
import logging
from typing import Optional, List

from .config import (
    MINISTRAL_URL,
    MINISTRAL_API_KEY,
    MINISTRAL_MODEL,
    MINISTRAL_TEMPERATURE,
    MINISTRAL_NUM_CTX,
    MINISTRAL_NUM_PREDICT,
)


logger = logging.getLogger("tender")


def call_ollama(prompt: str,
                model: str = "ministral-3:3b",
                base_url: str = "http://localhost:3000/ollama/api",
                api_key: Optional[str] = None,
                messages: Optional[List[dict]] = None,
                temperature: float = 0.1,
                num_ctx: int = 16384,
                num_predict: int = 8192) -> Optional[str]:
    """Synchronous call to an Ollama-compatible endpoint.

    Returns response text or None on failure.
    """
    url = f"{base_url.rstrip('/')}/chat"

    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if messages is None:
        messages = [{"role": "user", "content": prompt}]

    def build_payload(ctx: int) -> dict:
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": ctx,
            }
        }

    payload = build_payload(num_ctx)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=(30, 600))
        resp.raise_for_status()
        result = resp.json()
        if isinstance(result, dict) and "message" in result:
            return result["message"].get("content", "")
        try:
            logger.error("Неожиданный формат ответа Ollama: %s", result)
        except Exception:
            pass
        return None
    except requests.exceptions.RequestException as e:
        try:
            logger.error(f"Ошибка соединения с Ollama: {e}")
        except Exception:
            pass
        # try a retry strategy for large context OOM-like errors
        if hasattr(e, 'response') and e.response is not None:
            try:
                error_text = (e.response.text or '').lower()
            except Exception:
                error_text = ''
            if e.response.status_code == 500 and 'signal: killed' in error_text and num_ctx > 8192:
                fallback_ctx = max(8192, num_ctx // 2)
                try:
                    logger.info(f"[Ollama] Retry with reduced ctx: {num_ctx} -> {fallback_ctx}")
                except Exception:
                    pass
                try:
                    retry_resp = requests.post(url, headers=headers, json=build_payload(fallback_ctx), timeout=(30, 600))
                    retry_resp.raise_for_status()
                    retry_result = retry_resp.json()
                    if isinstance(retry_result, dict) and "message" in retry_result:
                        return retry_result["message"].get("content", "")
                except Exception:
                    try:
                        logger.error("[Ollama] Retry failed")
                    except Exception:
                        pass
        return None
    except json.JSONDecodeError as e:
        try:
            logger.error(f"Ошибка парсинга JSON от Ollama: {e}")
        except Exception:
            pass
        return None


def call_ministral(prompt: str,
                   api_key: Optional[str] = None,
                   model: str = "ministral-3:3b",
                   base_url: str = "http://localhost:3000/ollama/api",
                   messages: Optional[List[dict]] = None,
                   temperature: float = 0.1,
                   num_ctx: int = 16384,
                   num_predict: int = 8192) -> Optional[str]:
    """Compatibility wrapper; currently uses Ollama-style API."""
    return call_ollama(prompt, model=model, base_url=base_url, api_key=api_key, messages=messages,
                       temperature=temperature, num_ctx=num_ctx, num_predict=num_predict)


async def call_model(prompt: str,
                     model: Optional[str] = None,
                     api_key: Optional[str] = None,
                     base_url: Optional[str] = None,
                     messages: Optional[List[dict]] = None,
                     temperature: Optional[float] = None,
                     num_ctx: Optional[int] = None,
                     num_predict: Optional[int] = None) -> str:
    """Async wrapper used by analysis pipelines. Runs blocking HTTP call in thread.

    Returns response text or empty string on failure.
    """
    model = model or MINISTRAL_MODEL
    api_key = api_key or MINISTRAL_API_KEY
    base_url = base_url or MINISTRAL_URL
    temperature = temperature if temperature is not None else MINISTRAL_TEMPERATURE
    num_ctx = num_ctx or MINISTRAL_NUM_CTX
    num_predict = num_predict or MINISTRAL_NUM_PREDICT

    try:
        result = await asyncio.to_thread(
            call_ministral,
            prompt,
            api_key,
            model,
            base_url,
            messages,
            temperature,
            num_ctx,
            num_predict,
        )
        if result is None:
            return ''
        return str(result)
    except Exception as e:
        try:
            logger.error(f"call_model error: {e}")
        except Exception:
            pass
        return ''
import asyncio


async def call_model(prompt_text: str) -> str:
    """Placeholder async model call.

    In the real project this wraps Ministral/OpenAI/etc. Here it echoes
    a JSON skeleton for tests.
    """
    await asyncio.sleep(0.05)
    # Return a JSON with products list for demo
    return '{"products": [{"product_name": "Пример", "specs": "..."}]}'
# ministral_client (moved into core)
import requests
import json
import logging
from typing import Optional


def call_ollama(prompt: str, model: str = "ministral-3:3b",
                base_url: str = "http://localhost:3000/ollama/api",
                api_key: Optional[str] = None,
                messages: Optional[list] = None,
                temperature: float = 0.1,
                num_ctx: int = 16384,
                num_predict: int = 8192) -> Optional[str]:
    url = f"{base_url}/chat"
    
    headers = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    
    if messages is None:
        messages = [{"role": "user", "content": prompt}]
    
    def build_payload(ctx: int) -> dict:
        return {
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
                "num_ctx": ctx,
            }
        }

    payload = build_payload(num_ctx)
    
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=(30, 600))
        response.raise_for_status()
        result = response.json()
        
        if "message" in result:
            return result["message"].get("content", "")
        else:
            logger = logging.getLogger("tender")
            try:
                logger.error("Неожиданный формат ответа Ollama: %s", result)
            except Exception:
                pass
            return None
            
    except requests.exceptions.RequestException as e:
        logger = logging.getLogger("tender")
        try:
            logger.error(f"Ошибка соединения с Ollama: {e}")
        except Exception:
            pass
        if hasattr(e, 'response') and e.response is not None:
            try:
                logger.error(f"Статус: {e.response.status_code}")
                logger.error(f"Ответ: {e.response.text}")
            except Exception:
                pass
            error_text = (e.response.text or "").lower()
            if e.response.status_code == 500 and "signal: killed" in error_text and num_ctx > 8192:
                fallback_ctx = max(8192, num_ctx // 2)
                try:
                    logger.info(f"[Ollama] Повтор запроса с уменьшенным num_ctx: {num_ctx} -> {fallback_ctx}")
                except Exception:
                    pass
                try:
                    retry_response = requests.post(
                        url,
                        headers=headers,
                        json=build_payload(fallback_ctx),
                        timeout=(30, 600)
                    )
                    retry_response.raise_for_status()
                    retry_result = retry_response.json()
                    if "message" in retry_result:
                        return retry_result["message"].get("content", "")
                    try:
                        logger.error("Неожиданный формат ответа Ollama (retry): %s", retry_result)
                    except Exception:
                        pass
                    return None
                except requests.exceptions.RequestException as retry_e:
                    try:
                        logger.error(f"[Ollama] Повторный запрос неуспешен: {retry_e}")
                    except Exception:
                        pass
                    if hasattr(retry_e, 'response') and retry_e.response is not None:
                        try:
                            logger.error(f"[Ollama] Retry статус: {retry_e.response.status_code}")
                            logger.error(f"[Ollama] Retry ответ: {retry_e.response.text}")
                        except Exception:
                            pass
                    return None
                except json.JSONDecodeError as retry_json_error:
                    try:
                        logger.error(f"[Ollama] Ошибка парсинга JSON на retry: {retry_json_error}")
                    except Exception:
                        pass
                    return None
        return None
    except json.JSONDecodeError as e:
        logger = logging.getLogger("tender")
        try:
            logger.error(f"Ошибка парсинга JSON: {e}")
        except Exception:
            pass
        return None


def call_ministral(prompt: str, api_key: Optional[str] = None,
                        model: str = "ministral-3:3b",
                        base_url: str = "http://localhost:3000/ollama/api",
                        messages: Optional[list] = None,
                        temperature: float = 0.1,
                        num_ctx: int = 16384,
                        num_predict: int = 8192) -> Optional[str]:
    return call_ollama(prompt, model=model, base_url=base_url, api_key=api_key, messages=messages,
                        temperature=temperature, num_ctx=num_ctx, num_predict=num_predict)
