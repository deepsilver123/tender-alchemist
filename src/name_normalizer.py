"""Compatibility shim exposing `normalize_products` at `src.name_normalizer`.

This keeps existing test imports working while core implementation lives in
`src/core/name_normalizer.py`.
"""
from .core.name_normalizer import normalize_products, match_term

__all__ = ["normalize_products", "match_term"]
