# utils.py
import json
import re

def extract_json(text: str) -> str | None:
    if not text:
        return None

    # Блоки ```json ... ```
    pattern = r'```(?:json)?\s*([\s\S]*?)\s*```'
    matches = re.findall(pattern, text)
    for candidate in matches:
        candidate = candidate.strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            continue

    # От первой '{' до последней '}'
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        candidate = text[start:end+1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            candidate = re.sub(r'[\x00-\x1F\x7F]', '', candidate)
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass
    return None

def format_json_for_display(json_str: str) -> str:
    try:
        parsed = json.loads(json_str)
        return json.dumps(parsed, ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return json_str