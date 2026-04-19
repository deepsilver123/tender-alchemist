import json
import re
from typing import Optional, List
from pathlib import Path

from .config import DATA_DIR


TERMS_PATH = DATA_DIR / "product_terms.json"


def _load_terms():
    # Robust loader: try common encodings and tolerate trailing commas
    try:
        data_bytes = TERMS_PATH.read_bytes()
    except Exception:
        return []

    for enc in ("utf-8", "cp1251"):
        try:
            txt = data_bytes.decode(enc)
        except Exception:
            continue
        try:
            return json.loads(txt)
        except Exception:
            try:
                cleaned = re.sub(r",\s*(?=[}\]])", "", txt)
                return json.loads(cleaned)
            except Exception:
                continue
    return []


def _normalize_text(s: str) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r"[^\w\dа-яё]+", " ", s, flags=re.IGNORECASE)
    s = s.replace('_', ' ')
    return " ".join(s.split())


_TERMS_CACHE = None


def _get_terms():
    global _TERMS_CACHE
    if _TERMS_CACHE is None:
        _TERMS_CACHE = _load_terms()
    return _TERMS_CACHE


def match_term(text: str) -> Optional[dict]:
    norm = _normalize_text(text)
    if not norm:
        return None

    text_words = norm.split()
    best = None
    best_score = (0, 0, 0)

    for term in _get_terms():
        for alias in term.get("aliases", []):
            if not alias:
                continue
            a_norm = _normalize_text(alias)
            if not a_norm:
                continue
            a_words = a_norm.split()

            matched = 0
            for aw in a_words:
                for tw in text_words:
                    if aw == tw:
                        matched += 1
                        break
            if matched == 0:
                continue

            contiguous = 1 if f" {a_norm} " in f" {norm} " else 0
            length_score = len(a_norm)
            score = (contiguous, matched, length_score)
            if score > best_score:
                best_score = score
                best = term

    if best:
        return {"id": best.get("id"), "name": best.get("name")}
    return None


def normalize_products(parsed: dict) -> dict:
    if not isinstance(parsed, dict):
        return parsed
    products = parsed.get("products")
    if not products or not isinstance(products, list):
        return parsed

    for p in products:
        raw = p.get("product_name", "")
        if "original_product_name" not in p:
            p["original_product_name"] = raw
        stored_orig = p.get("original_product_name") or ""
        term = match_term(raw)
        # If the original document name differs from the LLM product name, try to
        # classify it too. Override the LLM classification only when an alias
        # appears as an exact substring in the original name (reliable signal).
        if stored_orig and stored_orig != raw:
            orig_term = match_term(stored_orig)
            if orig_term and (term is None or orig_term["id"] != term["id"]):
                orig_norm = _normalize_text(stored_orig)
                for t in _get_terms():
                    if t.get("id") == orig_term["id"]:
                        if any(
                            _normalize_text(a) and
                            f" {_normalize_text(a)} " in f" {orig_norm} "
                            for a in t.get("aliases", [])
                        ):
                            term = orig_term
                        break
        if term:
            p["product_name"] = term["name"]
            p["type_id"] = term["id"]
            p["type_score"] = 100
            orig_name = p.get("original_product_name", "")
            tr = p.get("technical_requirements") or {}
            orig_norm = _normalize_text(orig_name)
            if p.get("type_id") == "cpu":
                if any(k in orig_norm for k in ("кулер", "вентилятор", "радиатор", "охлаждение")):
                    for t in _get_terms():
                        if t.get("id") == "cooling.cpu":
                            p["product_name"] = t.get("name")
                            p["type_id"] = t.get("id")
                            p["type_score"] = 100
                            break
        else:
            p["type_id"] = None
            p["type_score"] = 0
            tr = p.get("technical_requirements") or {}

            def _has_indicator(indicators: List[str]) -> bool:
                raw_s = _normalize_text(raw)
                orig_name = p.get("original_product_name") or ""
                orig_s = _normalize_text(orig_name)
                for ind in indicators:
                    ind_norm = _normalize_text(ind)
                    if f" {ind_norm} " in f" {raw_s} " or f" {ind_norm} " in f" {orig_s} ":
                        return True
                return False

            if _has_indicator(["ssd"]):
                p["type_id"] = "storage.ssd"
                p["type_score"] = 100
            elif _has_indicator(["hdd", "жесткий", "винчестер"]):
                p["type_id"] = "storage.hdd"
                p["type_score"] = 100

            if p.get("type_id") is None and _has_indicator(["ddr", "оперативная память", "модуль памяти", "ram", "озу"]):
                p["type_id"] = "memory.ram"
                p["type_score"] = 100

            if p.get("type_id") is None and _has_indicator(["кулер", "вентилятор", "радиатор"]):
                p["type_id"] = "cooling.cpu"
                p["type_score"] = 100

    counts = {}
    for p in products:
        name = p.get("product_name", "")
        counts[name] = counts.get(name, 0) + 1

    seq = {}
    for p in products:
        name = p.get("product_name", "")
        if counts.get(name, 0) > 1:
            seq[name] = seq.get(name, 0) + 1
            p["product_name"] = f"{name} {seq[name]}"

    return parsed
