import re
import json
from typing import Optional


def extract_json_from_text(response: str) -> Optional[str]:
    """Try to extract a JSON object/array from a text blob and return it as a
    compact JSON string (utf-8, non-ascii preserved). Returns None when no
    parseable JSON is found.
    """
    if not response:
        return None
    fenced_match = re.search(r'```json\s*(\{.*?\}|\[.*?\])\s*```', response, re.DOTALL)
    candidate = fenced_match.group(1) if fenced_match else response
    try:
        parsed = json.loads(candidate)
        return json.dumps(parsed, ensure_ascii=False)
    except Exception:
        match = re.search(r'(\{(?:.|\n)*\}|\[(?:.|\n)*\])', response, re.DOTALL)
        if match:
            try:
                parsed = json.loads(match.group(1))
                return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                return match.group(1)
    return None
