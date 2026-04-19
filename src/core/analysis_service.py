import asyncio
import json
import os
import sys
import logging
from pathlib import Path

from .json_utils import extract_json_from_text
from .ministral_client import call_model
from .config import LOG_DIR


async def analyze_files(task_id: str, files: list, send_log, ministral_url: str | None = None, ministral_model: str | None = None, docling_base: str | None = None):
    """Process uploaded files and produce JSON result.

    Args:
        task_id: unique id for session
        files: list of file paths
        send_log: callable to send logs (synchronous or async)
    Returns:
        dict: parsed JSON
    """
    await _maybe_await(send_log(f"Начинаю обработку {len(files)} файлов"))

    # Install temporary logging handler to forward messages from lower-level
    # modules (file_reader, docx_parser, ministral_client) into send_log.
    import logging
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    class _ForwardHandler(logging.Handler):
        def emit(self, record):
            try:
                try:
                    msg = self.format(record)
                except Exception:
                    msg = str(record)
                try:
                    res = send_log(msg)
                    if asyncio.iscoroutine(res):
                        if loop is not None:
                            try:
                                asyncio.run_coroutine_threadsafe(res, loop)
                            except Exception as e:
                                # fallback to stderr to avoid recursive logging
                                try:
                                    sys.stderr.write(f"[ForwardHandler] run_coroutine_threadsafe failed: {e}\n")
                                except Exception:
                                    pass
                    else:
                        if loop is not None:
                            try:
                                loop.call_soon_threadsafe(send_log, msg)
                            except Exception as e:
                                try:
                                    sys.stderr.write(f"[ForwardHandler] call_soon_threadsafe failed: {e}\n")
                                except Exception:
                                    pass
                except Exception as e:
                    # avoid using the 'tender' logger here to prevent recursion into this handler
                    try:
                        sys.stderr.write(f"[ForwardHandler] send_log failed: {e}\n")
                    except Exception:
                        pass
            except Exception:
                # last-resort: avoid raising from emit
                try:
                    sys.stderr.write("[ForwardHandler] unexpected emit failure\n")
                except Exception:
                    pass

    logger = logging.getLogger("tender")
    fh = _ForwardHandler()
    fh.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(fh)

    try:
        combined_text_parts = []
        for file_path in files:
            await _maybe_await(send_log(f"Читаю {file_path}"))
            # simple heuristic: call pandoc/docx parser based on extension
            ext = Path(file_path).suffix.lower()
            if ext in ('.docx',):
                try:
                    from .docx_parser import extract_from_docx
                    text = extract_from_docx(file_path)
                except Exception as e:
                    logging.getLogger("tender").exception("Ошибка парсинга DOCX %s: %s", file_path, e)
                    text = ''
            else:
                try:
                    with open(file_path, 'r', encoding='utf-8') as fh:
                        text = fh.read()
                except Exception as e:
                    logging.getLogger("tender").exception("Ошибка чтения файла %s: %s", file_path, e)
                    text = ''

            combined_text_parts.append(text)

        combined_text = '\n'.join(p for p in combined_text_parts if p)
        await _maybe_await(send_log("Сформирован общий текст, вызываю модель"))

        # Ensure results folder for this task exists and save prompt there
        out_dir = Path('results') / task_id
        os.makedirs(out_dir, exist_ok=True)
        prompt_file = out_dir / 'prompt.txt'
        try:
            prompt_file.write_text(combined_text, encoding='utf-8')
            await _maybe_await(send_log(f"📁 prompt сохранён: {prompt_file}"))
        except Exception as e:
            logging.getLogger("tender").exception("Ошибка записи prompt в results: %s", e)

        # Also save prompt copy to LOG_DIR/<task_id>/prompt.html
        try:
            task_log_dir = LOG_DIR / task_id
            task_log_dir.mkdir(parents=True, exist_ok=True)
            (task_log_dir / 'prompt.html').write_text(combined_text, encoding='utf-8')
            await _maybe_await(send_log(f"📁 prompt скопирован в лог: {task_log_dir / 'prompt.html'}"))
        except Exception as e:
            logging.getLogger("tender").exception("Ошибка записи prompt в лог: %s", e)

        try:
            model_resp = await call_model(combined_text)
        except Exception as e:
            logging.getLogger("tender").exception("Ошибка вызова модели: %s", e)
            await _maybe_await(send_log(f"Ошибка вызова модели: {e}"))
            model_resp = ''

        await _maybe_await(send_log("Извлекаю JSON из ответа модели"))
        parsed_raw = extract_json_from_text(model_resp)
        # Support both raw JSON string (returned by extractor) and already-parsed objects.
        if isinstance(parsed_raw, str):
            try:
                parsed = json.loads(parsed_raw)
            except Exception as e:
                logging.getLogger("tender").exception("Ошибка парсинга JSON из ответа модели: %s", e)
                parsed = {}
        elif parsed_raw is None:
            parsed = {}
        else:
            parsed = parsed_raw

        # save raw model response into results and copy to logs
        raw_file = None
        if model_resp:
            raw_file = out_dir / 'raw.txt'
            try:
                raw_file.write_text(model_resp, encoding='utf-8')
            except Exception as e:
                logging.getLogger("tender").exception("Ошибка записи raw.txt: %s", e)
                raw_file = None
            else:
                # copy to per-task log folder
                try:
                    task_log_dir = LOG_DIR / task_id
                    task_log_dir.mkdir(parents=True, exist_ok=True)
                    (task_log_dir / 'raw_answer.txt').write_text(model_resp, encoding='utf-8')
                except Exception as e:
                    logging.getLogger("tender").exception("Ошибка записи raw в лог: %s", e)

        out_file = out_dir / 'result.json'
        try:
            with open(out_file, 'w', encoding='utf-8') as fh:
                json.dump(parsed, fh, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.getLogger("tender").exception("Ошибка записи result.json: %s", e)
        else:
            # copy result to per-task log folder (as JSON in result.json)
            try:
                task_log_dir = LOG_DIR / task_id
                task_log_dir.mkdir(parents=True, exist_ok=True)
                with open(task_log_dir / 'result.json', 'w', encoding='utf-8') as fh:
                    json.dump(parsed, fh, ensure_ascii=False, indent=2)
            except Exception as e:
                logging.getLogger("tender").exception("Ошибка записи result в лог: %s", e)

        await _maybe_await(send_log(f"Готово, сохранено в {out_file}"))
        return {
            "parsed": parsed,
            "result_path": str(out_file),
            "prompt_path": str(prompt_file) if 'prompt_file' in locals() else None,
            "raw_path": str(raw_file) if raw_file is not None else None,
        }
    finally:
        try:
            logger.removeHandler(fh)
        except Exception as e:
            try:
                sys.stderr.write(f"[analysis_service] failed to remove handler: {e}\n")
            except Exception:
                pass


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
