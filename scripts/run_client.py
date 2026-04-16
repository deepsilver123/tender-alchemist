"""Simple CLI runner that calls core.analyze_files.

Usage:
  python scripts/run_client.py FILE1 [FILE2 ...] [--out result.json]

This is a lightweight scaffold for the "client" UI that re-uses the `core`
adapter package.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from core import analyze_files  # type: ignore


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", help="Paths to files to analyze")
    parser.add_argument("--ministral-url", default="", help="Override Ministral URL")
    parser.add_argument("--ministral-model", default="", help="Override Ministral model")
    parser.add_argument("--docling-base", default="http://localhost:5001", help="Docling base URL")
    parser.add_argument("--out", default=None, help="Path to write result JSON")
    args = parser.parse_args()

    if analyze_files is None:
        print("Error: core.analyze_files is not available. Make sure the project is importable.")
        sys.exit(2)

    def log(msg: str) -> None:
        print(msg, flush=True)

    result = analyze_files(
        args.files,
        log,
        ministral_url=args.ministral_url or None,
        ministral_model=args.ministral_model or None,
        docling_base=args.docling_base,
    )

    parsed = result.get("parsed")
    out_path = Path(args.out) if args.out else Path("logs") / "result_cli.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(parsed, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Result written to {out_path}")


if __name__ == "__main__":
    main()
