import re
import json
from typing import Any


def extract_json_from_text(text: str) -> Any:
    """Extract the first JSON object/array from text and return the raw JSON string.

    Behavior:
    - If a JSON block is found and can be parsed, return the cleaned JSON string.
    - If no JSON is found or parsing fails, return None.

    This function is conservative: callers that need a Python object should
    call ``json.loads()`` on the returned string. Some callers in the
    codebase may also accept a dict/list; other code handles both forms.
    """
    if not text:
        return None

    # 1. Try fenced block: capture everything between ``` opening and ``` closing
    fenced = re.search(r'```(?:json)?\s*\n(.*?)\n?\s*```', text, re.DOTALL | re.IGNORECASE)
    candidate = fenced.group(1).strip() if fenced else None

    # 2. Fallback: greedy match for outermost { } or [ ]
    if not candidate:
        m = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
        candidate = m.group(1) if m else None

    if not candidate:
        return None

    # 3. Strip JS-style // line comments (not valid JSON)
    candidate = re.sub(r'//[^\n]*', '', candidate)

    # Helper: try to clean candidate and return the cleaned string if parseable
    def _try_clean_and_return(s: str) -> Optional[str]:
        try:
            # try as-is
            json.loads(s)
            return s
        except Exception:
            pass
        try:
            # fix trailing commas
            cleaned = re.sub(r',\s*([}\]])', r'\1', s)
            json.loads(cleaned)
            return cleaned
        except Exception:
            pass
        try:
            # fix smart/curly quotes and trailing commas
            candidate2 = (
                s.replace('\u201c', '"').replace('\u201d', '"')
                 .replace('\u2018', "'").replace('\u2019', "'")
            )
            cleaned2 = re.sub(r',\s*([}\]])', r'\1', candidate2)
            json.loads(cleaned2)
            return cleaned2
        except Exception:
            return None

    return _try_clean_and_return(candidate)

