import requests
from typing import Any


def _normalize_embedding_response(data: Any) -> list[list[float]]:
    if isinstance(data, dict):
        if "data" in data and isinstance(data["data"], list):
            normalized = []
            for item in data["data"]:
                if isinstance(item, dict) and "embedding" in item:
                    normalized.append(item["embedding"])
                elif isinstance(item, list):
                    normalized.append(item)
            if normalized:
                return normalized
        if "embeddings" in data and isinstance(data["embeddings"], list):
            if data["embeddings"] and isinstance(data["embeddings"][0], dict):
                return [item.get("embedding") for item in data["embeddings"] if item.get("embedding") is not None]
            return [item for item in data["embeddings"] if isinstance(item, list)]
        if "result" in data and isinstance(data["result"], list):
            if data["result"] and isinstance(data["result"][0], list):
                return data["result"]
            if data["result"] and isinstance(data["result"][0], dict) and "embedding" in data["result"][0]:
                return [item["embedding"] for item in data["result"]]
    if isinstance(data, list):
        if data and isinstance(data[0], list):
            return data
        if data and isinstance(data[0], dict) and "embedding" in data[0]:
            return [item["embedding"] for item in data]
    raise ValueError(f"Не удалось распознать формат ответа Ollama: {type(data)}")


def _try_embed(url: str, payload: dict[str, Any], timeout: int = 60) -> list[list[float]]:
    response = requests.post(url, json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    return _normalize_embedding_response(data)


def embed_texts(texts: list[str], model: str, base_url: str, timeout: int = 60) -> list[list[float]]:
    if not texts:
        return []

    import time
    payload = {"model": model, "input": texts}
    endpoints = ["/embed", "/api/embed"]
    last_error: Exception | None = None
    for endpoint in endpoints:
        url = f"{base_url.rstrip('/')}{endpoint}"
        for attempt in range(3):
            try:
                return _try_embed(url, payload, timeout=timeout)
            except requests.exceptions.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 404:
                    try:
                        body = exc.response.text
                    except Exception:
                        body = "<no response body>"
                    last_error = RuntimeError(f"404 from {url}: {body}")
                    break  # 404 — не ретраим, идём к следующему endpoint
                if exc.response is not None and exc.response.status_code == 500 and attempt < 2:
                    time.sleep(2 ** attempt)  # 1s, 2s
                    continue
                last_error = exc
                break
            except Exception as exc:
                last_error = exc
                break
    raise RuntimeError(f"Failed to call Ollama embed endpoint: {last_error}")


def llm_build_query(product_name: str, chars: dict, base_url: str, model: str = "qwen2.5:1.5b") -> str | None:
    """
    Просит LLM сформулировать поисковый запрос для товара в стиле названий интернет-магазина.
    Возвращает строку запроса или None при ошибке.
    """
    if not chars:
        return None

    # Компактно сериализуем характеристики
    chars_lines = "\n".join(f"- {k}: {v}" for k, v in list(chars.items())[:20])

    prompt = (
        f"Товар: {product_name}\n"
        f"Характеристики из тендера:\n{chars_lines}\n\n"
        "Задача: напиши ОДНУ строку поискового запроса для поиска этого товара в интернет-магазине.\n"
        "Формат — как в каталоге: категория, ключевые характеристики через запятую.\n"
        "Включи только самые важные характеристики (3-6 штук), которые реально встречаются в названиях товаров.\n"
        "Не пиши ничего кроме самого запроса. Никаких пояснений."
    )

    try:
        url = f"{base_url.rstrip('/')}/api/generate"
        response = requests.post(
            url,
            json={"model": model, "prompt": prompt, "stream": False},
            timeout=30,
        )
        response.raise_for_status()
        text = response.json().get("response", "").strip()
        # Берём только первую строку (на случай если модель добавила лишнее)
        first_line = text.split("\n")[0].strip().strip('"').strip()
        return first_line if first_line else None
    except Exception:
        return None
