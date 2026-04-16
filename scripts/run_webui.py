"""Run the lightweight Python web UI.

Usage:
  python scripts/run_webui.py
  python scripts/run_webui.py --host 0.0.0.0 --port 8010
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn


ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from webui.app_impl import app
import os

# Determine defaults with precedence: CLI args > ENV > core.config > hardcoded
default_host = "127.0.0.1"
default_port = 8001
try:
    from core import config as core_config
    default_host = os.environ.get("WEBUI_HOST", getattr(core_config, "WEBUI_HOST", default_host))
    try:
        default_port = int(os.environ.get("WEBUI_PORT", getattr(core_config, "WEBUI_PORT", default_port)))
    except (TypeError, ValueError):
        default_port = int(os.environ.get("WEBUI_PORT", default_port))
except Exception:
    # core.config may be unavailable in some contexts; fall back to env/hardcoded
    default_host = os.environ.get("WEBUI_HOST", default_host)
    try:
        default_port = int(os.environ.get("WEBUI_PORT", default_port))
    except (TypeError, ValueError):
        default_port = 8001


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default=default_host)
    parser.add_argument("--port", default=default_port, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
