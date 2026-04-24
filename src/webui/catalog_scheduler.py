"""Планировщик автоматического обновления каталога e2e4.

Каждую субботу в полночь (по часовому поясу из переменной TZ):
  1. Скачивает свежий ZIP с сайта e2e4 (один из регионов, задаётся через
     E2E4_CATALOG_URL).
  2. Распаковывает и разворачивает XLSX в плоский CSV.
  3. Переиндексирует коллекцию Qdrant (с пересозданием коллекции).

Переменные окружения:
  E2E4_CATALOG_URL  — прямая ссылка на ZIP-файл с прайс-листом
                      (по умолчанию — Иркутский регион e2e4).
  QDRANT_URL        — адрес Qdrant REST API.
  TZ                — часовой пояс (используется APScheduler через tzlocal).
"""
from __future__ import annotations

import logging
import sys
import time
from pathlib import Path

log = logging.getLogger("tender.scheduler")

# Путь к корню проекта (src/../)
_SRC_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = _SRC_DIR.parent
_SCRIPTS_DIR = _ROOT_DIR / "scripts"

# Добавляем scripts/ в sys.path чтобы импортировать e2e4_ingest и index_catalog
if str(_SRC_DIR) not in sys.path:
    sys.path.insert(0, str(_SRC_DIR))
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

_DEFAULT_URL = "https://e2e4online.ru/ws/excel/irkutsk.e2e4online.ru.zip"
_DEFAULT_CSV = _ROOT_DIR / "data" / "catalogs" / "e2e4_flat.csv"


def run_catalog_update() -> None:
    """Полный цикл: скачать → распаковать → сплющить → переиндексировать."""
    import os

    url = os.environ.get("E2E4_CATALOG_URL", _DEFAULT_URL)
    zip_dest = _ROOT_DIR / "data" / "downloads" / Path(url).name
    out_csv = _DEFAULT_CSV

    log.info("[scheduler] Начало обновления каталога e2e4. URL: %s", url)
    t0 = time.time()

    try:
        # 1. Импортируем утилиты из scripts/e2e4_ingest.py
        import e2e4_ingest as ingest  # type: ignore[import]

        # 2. Скачиваем ZIP (force=True чтобы получить свежую версию)
        zip_path = ingest.download(url, zip_dest, force=True)
        log.info("[scheduler] ZIP скачан: %s (%.1f МБ)", zip_path, zip_path.stat().st_size / 1_048_576)

        # 3. Определяем XLSX внутри архива и разворачиваем
        import zipfile
        with zipfile.ZipFile(zip_path) as z:
            xlsx_names = [n for n in z.namelist() if n.lower().endswith((".xlsx", ".xls")) and not n.startswith("__")]
        if not xlsx_names:
            raise RuntimeError("В ZIP нет XLSX-файлов.")

        # Распаковываем во временную папку
        extract_dir = _ROOT_DIR / "data" / "e2e4_extracted"
        ingest.safe_extract(zip_path, extract_dir, overwrite=True)

        # Разворачиваем первый найденный XLSX
        xlsx_path = extract_dir / xlsx_names[0]
        rows = ingest.flatten_workbook(xlsx_path, out_csv, sample_limit=None)
        log.info("[scheduler] CSV обновлён: %s строк → %s", rows, out_csv)

        # 4. Переиндексируем Qdrant
        from core.qdrant_indexer import TenderMVPQdrant  # type: ignore[import]
        indexer = TenderMVPQdrant()

        written = [0]

        def _log(msg: str) -> None:
            log.info("[scheduler] %s", msg)

        def _progress(done: int, total: int) -> None:
            if total and done % max(1, total // 10) == 0:
                log.info("[scheduler] Индексация %d/%d (%.0f%%)", done, total, done * 100 / total)

        count = indexer.process_file(str(out_csv), log_cb=_log, progress_cb=_progress)
        elapsed = time.time() - t0
        log.info("[scheduler] Готово: %d записей за %.1f с.", count, elapsed)

    except Exception as exc:  # noqa: BLE001
        log.exception("[scheduler] Ошибка обновления каталога: %s", exc)


def start_scheduler() -> None:
    """Запускает APScheduler: обновление каталога каждую субботу в 00:00."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("[scheduler] APScheduler не установлен — автообновление каталога отключено.")
        return

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_catalog_update,
        trigger=CronTrigger(day_of_week="sat", hour=0, minute=0, second=0),
        id="e2e4_weekly_update",
        name="Еженедельное обновление каталога e2e4",
        replace_existing=True,
        misfire_grace_time=3600,  # допускаем опоздание до 1 ч (если сервер был выключен)
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "[scheduler] Запущен. Следующее обновление каталога e2e4: %s",
        scheduler.get_job("e2e4_weekly_update").next_run_time,
    )
