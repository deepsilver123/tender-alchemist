import re
import json
from typing import Any


def extract_json_from_text(text: str) -> Any:
    """Extract the first JSON object or array from text and return a Python object.

    Handles:
    - Fenced ```json ... ``` blocks
    - JS-style // line comments inside the block
    - Trailing commas
    - Smart quotes

    Returns a parsed dict/list on success, or an empty dict on failure.
    """
    if not text:
        return {}

    # 1. Try fenced block: capture everything between ``` opening and ``` closing
    fenced = re.search(r'```(?:json)?\s*\n(.*?)\n?\s*```', text, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else None

    # 2. Fallback: greedy match for outermost { } or [ ]
    if not candidate:
        m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        candidate = m.group(1) if m else None

    if not candidate:
        return {}

    # 3. Strip JS-style // line comments (not valid JSON)
    candidate = re.sub(r'//[^\n]*', '', candidate)

    # 4. Try to parse as-is
    try:
        return json.loads(candidate)
    except Exception:
        pass

    # 5. Fix trailing commas: ,} or ,]
    try:
        cleaned = re.sub(r',\s*([}\]])', r'\1', candidate)
        return json.loads(cleaned)
    except Exception:
        pass

    # 6. Fix smart/curly quotes
    try:
        candidate2 = (
            candidate
            .replace('\u201c', '"').replace('\u201d', '"')
            .replace('\u2018', "'").replace('\u2019', "'")
        )
        cleaned2 = re.sub(r',\s*([}\]])', r'\1', candidate2)
        return json.loads(cleaned2)
    except Exception:
        pass

    return {}

