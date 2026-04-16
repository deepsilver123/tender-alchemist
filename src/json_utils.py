"""Compatibility shims for tests importing `src.json_utils`.

These re-export the actual implementations from `src/core/`.
"""
from .core.json_utils import extract_json_from_text

__all__ = ["extract_json_from_text"]
