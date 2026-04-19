#!/usr/bin/env python3
"""
Simple runner for the web UI that works on Linux and Windows.
Usage:
    python run_webui.py
It configures PYTHONPATH so `src/` is on sys.path and runs uvicorn.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import os
import uvicorn

if __name__ == "__main__":
    # default host/port can be overridden with env vars WEBUI_HOST/WEBUI_PORT
    try:
        # prefer explicit env vars, otherwise use config defaults
        from core import config as core_config
        host = os.environ.get("WEBUI_HOST", core_config.WEBUI_HOST)
        port = int(os.environ.get("WEBUI_PORT", core_config.WEBUI_PORT))
    except Exception:
        host = os.environ.get("WEBUI_HOST", "0.0.0.0")
        port = int(os.environ.get("WEBUI_PORT", 8000))

    uvicorn.run("webui.app_impl:app", host=host, port=port)
