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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=8001, type=int)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()

    uvicorn.run(app, host=args.host, port=args.port, reload=args.reload)


if __name__ == "__main__":
    main()
