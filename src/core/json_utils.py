import re
import json
from typing import Any, Optional


def extract_json_from_text(text: str) -> Any:
    """Extract the first JSON object/array from text.

    If parsing succeeds, this function returns the parsed Python object.
    If no JSON can be found or parsing fails, it returns None.
    """
    if not text:
        return None

    def extract_fenced_block(src: str) -> Optional[str]:
        m = re.search(r'```(?:json)?\s*[\r\n]+(.*?)[\r\n]*```', src, re.DOTALL | re.IGNORECASE)
        return m.group(1).strip() if m else None

    def extract_bracket_json(src: str) -> Optional[str]:
        start = None
        stack = []
        in_string = False
        escape = False
        for idx, ch in enumerate(src):
            if escape:
                escape = False
                continue
            if ch == '\\':
                escape = True
                continue
            if ch == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if ch in '{[':
                if start is None:
                    start = idx
                stack.append(ch)
                continue
            if ch in '}]' and stack:
                open_ch = stack[-1]
                if (open_ch == '{' and ch == '}') or (open_ch == '[' and ch == ']'):
                    stack.pop()
                    if not stack and start is not None:
                        return src[start:idx + 1]
                else:
                    return None
        return None

    candidate = extract_fenced_block(text) or extract_bracket_json(text)
    if not candidate:
        return None

    candidate = re.sub(r'//[^\n]*', '', candidate)
    candidate = candidate.replace('\u201c', '"').replace('\u201d', '"')
    candidate = candidate.replace('\u2018', "'").replace('\u2019', "'")

    def _repair_json_text(s: str) -> str:
        lines = s.splitlines()
        repaired = []
        for line in lines:
            # Fix lines like: "key": "value": "extra"
            m = re.match(r'^(\s*"[^"]+"\s*:\s*"[^"]*")\s*:\s*"[^"]*"(,?)\s*$', line)
            if m:
                line = f"{m.group(1)}{m.group(2)}"
            repaired.append(line)

        final_lines = []
        for idx, line in enumerate(repaired):
            stripped = line.rstrip()
            next_line = repaired[idx + 1].lstrip() if idx + 1 < len(repaired) else ''
            if (
                stripped.endswith('"')
                and next_line.startswith('"')
                and not stripped.endswith(',')
                and not next_line.startswith('}')
                and not next_line.startswith(']')
            ):
                stripped += ','
            final_lines.append(stripped)

        repaired_text = '\n'.join(final_lines)
        repaired_text = re.sub(r',\s*([}\]])', r'\1', repaired_text)
        return repaired_text

    def _try_parse(s: str) -> Optional[Any]:
        try:
            return json.loads(s)
        except Exception:
            pass
        repaired = _repair_json_text(s)
        try:
            return json.loads(repaired)
        except Exception:
            pass
        try:
            cleaned = re.sub(r',\s*([}\]])', r'\1', repaired)
            return json.loads(cleaned)
        except Exception:
            pass
        return None

    return _try_parse(candidate)

