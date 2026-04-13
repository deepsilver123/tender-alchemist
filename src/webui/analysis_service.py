"""Compatibility wrapper: delegate to `core.analysis_service.analyze_files`.

Many parts of the codebase import `webui_app.analysis_service.analyze_files`.
This module preserves a compatible API under `webui.analysis_service` while
delegating to the new core implementation.
"""

import asyncio
import uuid
from typing import Callable

from core.analysis_service import analyze_files as core_analyze  # type: ignore


def analyze_files(file_paths: list[str], log: Callable[[str], None], ministral_url: str | None = None, ministral_model: str | None = None, docling_base: str | None = None) -> dict:
    task_id = uuid.uuid4().hex
    # run the core async analyzer synchronously for compatibility
    return asyncio.run(core_analyze(task_id, file_paths, log, ministral_url, ministral_model, docling_base))
