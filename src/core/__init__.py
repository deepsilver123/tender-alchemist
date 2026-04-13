"""Core analysis package exports.

Expose main analysis entrypoints for UIs to import.
"""
from __future__ import annotations

from .analysis_service import analyze_files
from .name_normalizer import normalize_products
from .json_utils import extract_json_from_text

__all__ = ["analyze_files", "normalize_products", "extract_json_from_text"]
