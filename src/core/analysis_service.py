import asyncio
import json
import os
from pathlib import Path

from .json_utils import extract_json_from_text
from .ministral_client import call_model


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
    except Exception:
        loop = None

    class _ForwardHandler(logging.Handler):
        def emit(self, record):
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
                        except Exception:
                            pass
                else:
                    if loop is not None:
                        try:
                            loop.call_soon_threadsafe(send_log, msg)
                        except Exception:
                            pass
            except Exception:
                try:
                    if loop is not None:
                        loop.call_soon_threadsafe(send_log, msg)
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
                except Exception:
                    text = ''
            else:
                try:
                    with open(file_path, 'r', encoding='utf-8') as fh:
                        text = fh.read()
                except Exception:
                    text = ''

            combined_text_parts.append(text)

        combined_text = '\n'.join(p for p in combined_text_parts if p)
        await _maybe_await(send_log("Сформирован общий текст, вызываю модель"))

        try:
            model_resp = await call_model(combined_text)
        except Exception as e:
            await _maybe_await(send_log(f"Ошибка вызова модели: {e}"))
            model_resp = ''

        await _maybe_await(send_log("Извлекаю JSON из ответа модели"))
        parsed = extract_json_from_text(model_resp)

        # persist to task folder
        out_dir = Path('results') / task_id
        os.makedirs(out_dir, exist_ok=True)
        # save raw model response
        raw_file = None
        if model_resp:
            raw_file = out_dir / 'raw.txt'
            try:
                raw_file.write_text(model_resp, encoding='utf-8')
            except Exception:
                raw_file = None

        out_file = out_dir / 'result.json'
        with open(out_file, 'w', encoding='utf-8') as fh:
            json.dump(parsed, fh, ensure_ascii=False, indent=2)

        await _maybe_await(send_log(f"Готово, сохранено в {out_file}"))
        return {
            "parsed": parsed,
            "result_path": str(out_file),
            "prompt_path": None,
            "raw_path": str(raw_file) if raw_file is not None else None,
        }
    finally:
        try:
            logger.removeHandler(fh)
        except Exception:
            pass


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value
