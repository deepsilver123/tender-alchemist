# ministral_client_local.py
# Клиент для локальной модели Ministral через Open WebUI / Ollama с поддержкой API-ключа.

import requests
import json
from typing import Optional

def call_ollama(prompt: str, model: str = "ministral-3:3b",
                base_url: str = "http://localhost:3000/ollama/api",
                api_key: Optional[str] = None,
                messages: Optional[list] = None,
                temperature: float = 0.1,
                num_ctx: int = 16384,
                num_predict: int = 8192) -> Optional[str]:
    """
    Вызов локальной модели через Ollama API, проксируемый Open WebUI.

    :param prompt: Текст запроса (уже с инструкциями и документами)
    :param model: Имя модели в Ollama (по умолчанию ministral-3:3b)
    :param base_url: Базовый URL прокси (по умолчанию http://localhost:3000/ollama/api)
    :param api_key: Опциональный API-ключ для аутентификации (если требуется)
    :param messages: Список сообщений для чата (если None, использует prompt как user message)
    :return: Ответ модели в виде строки или None при ошибке
    """
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
        
        # Ollama возвращает ответ в поле message.content
        if "message" in result:
            return result["message"].get("content", "")
        else:
            print("Неожиданный формат ответа Ollama:", result)
            return None
            
    except requests.exceptions.RequestException as e:
        print(f"Ошибка соединения с Ollama: {e}")
        if hasattr(e, 'response') and e.response is not None:
            print(f"Статус: {e.response.status_code}")
            print(f"Ответ: {e.response.text}")
            # Частая причина 500: llama runner killed (OOM). Пробуем один раз с меньшим контекстом.
            error_text = (e.response.text or "").lower()
            if e.response.status_code == 500 and "signal: killed" in error_text and num_ctx > 8192:
                fallback_ctx = max(8192, num_ctx // 2)
                print(f"[Ollama] Повтор запроса с уменьшенным num_ctx: {num_ctx} -> {fallback_ctx}")
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
                    print("Неожиданный формат ответа Ollama (retry):", retry_result)
                    return None
                except requests.exceptions.RequestException as retry_e:
                    print(f"[Ollama] Повторный запрос неуспешен: {retry_e}")
                    if hasattr(retry_e, 'response') and retry_e.response is not None:
                        print(f"[Ollama] Retry статус: {retry_e.response.status_code}")
                        print(f"[Ollama] Retry ответ: {retry_e.response.text}")
                    return None
                except json.JSONDecodeError as retry_json_error:
                    print(f"[Ollama] Ошибка парсинга JSON на retry: {retry_json_error}")
                    return None
        return None
    except json.JSONDecodeError as e:
        print(f"Ошибка парсинга JSON: {e}")
        return None


def call_ministral(prompt: str, api_key: Optional[str] = None,
                        model: str = "ministral-3:3b",
                        base_url: str = "http://localhost:3000/ollama/api",
                        messages: Optional[list] = None,
                        temperature: float = 0.1,
                        num_ctx: int = 16384,
                        num_predict: int = 8192) -> Optional[str]:
    """
    Функция, совместимая по использованию с gui.py, вызывающая локальную модель.
    Параметр api_key опционален (если локальный Ollama не требует ключа).

    :param prompt: Полный промпт с документами
    :param api_key: API-ключ (если нужен)
    :param model: Имя модели в Ollama
    :param base_url: Базовый URL прокси
    :param messages: Список сообщений для чата
    :return: Ответ модели
    """
    return call_ollama(prompt, model=model, base_url=base_url, api_key=api_key, messages=messages,
                        temperature=temperature, num_ctx=num_ctx, num_predict=num_predict)


