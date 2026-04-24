#!/usr/bin/env python3
"""
Индексирует CSV-каталог e2e4 в Qdrant.
Использование:
  python scripts/index_catalog.py
  python scripts/index_catalog.py --csv data/catalogs/e2e4_flat.csv
"""
import sys
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from core.qdrant_indexer import TenderMVPQdrant


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default="data/catalogs/e2e4_flat.csv")
    args = parser.parse_args()

    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"❌ Файл не найден: {csv_path}")
        sys.exit(1)

    print(f"📂 Файл: {csv_path}")
    indexer = TenderMVPQdrant()

    start = time.time()
    last_progress = [0]

    def log(msg):
        print(msg)

    def progress(done, total):
        pct = done * 100 // total
        if pct // 10 != last_progress[0] // 10:
            elapsed = time.time() - start
            rate = done / elapsed if elapsed > 0 else 0
            eta = (total - done) / rate if rate > 0 else 0
            print(f"  {pct}% ({done}/{total}) | {rate:.0f} записей/сек | ETA {eta:.0f}с")
            last_progress[0] = pct

    count = indexer.process_file(str(csv_path), log_cb=log, progress_cb=progress)
    elapsed = time.time() - start
    print(f"\n✅ Готово: {count} записей за {elapsed:.1f}с")


if __name__ == "__main__":
    main()
