# file_reader.py (moved into core)
import requests
import time
import tempfile
from pathlib import Path
import pandas as pd
from core.docx_parser import extract_from_docx  # fallback parser now in core
import subprocess
import logging
from bs4 import BeautifulSoup
import concurrent.futures
from typing import Callable, Iterable, List, Dict

DOCLING_URL_ASYNC = "http://localhost:5001/v1/convert/file/async"
DOCLING_STATUS_URL = "http://localhost:5001/v1/status/poll"
DOCLING_RESULT_URL = "http://localhost:5001/v1/result"
DOCLING_SYNC_URL = "http://localhost:5001/v1/convert/file"


def convert_docx_to_html(docx_path: str) -> str:
    file_path = Path(docx_path)
    if not file_path.exists():
        return f"❌ Файл не найден: {docx_path}"

    with tempfile.NamedTemporaryFile(suffix='.html', delete=False) as tmp:
        tmp_path = tmp.name

    try:
        subprocess.run(
            ['pandoc', str(file_path), '-o', tmp_path, '--to', 'html', '--wrap=none'],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )

        with open(tmp_path, 'r', encoding='utf-8') as f:
            content = f.read()

        return content

    except subprocess.CalledProcessError as e:
        return f"❌ Ошибка pandoc: {e.stderr}"
    except FileNotFoundError:
        return "❌ pandoc не установлен! Установите его: https://pandoc.org/installing.html"
    except subprocess.TimeoutExpired:
        return "❌ Таймаут: pandoc не ответил за 60 секунд"
    except Exception as e:
        return f"❌ Ошибка: {e}"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def clean_html_aggressive(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')

    for elem in soup.find_all('colgroup'):
        elem.decompose()
    for elem in soup.find_all('col'):
        elem.decompose()

    attrs_to_remove = {'style', 'width', 'height', 'border', 'cellpadding', 'cellspacing', 'class', 'id'}

    for tag in soup.find_all():
        for attr in list(tag.attrs.keys()):
            if attr in attrs_to_remove:
                del tag[attr]

    for tag in soup.find_all(['th', 'td']):
        for attr in ['colspan', 'rowspan']:
            if tag.has_attr(attr):
                try:
                    if int(tag[attr]) == 1:
                        del tag[attr]
                except (ValueError, TypeError):
                    pass

    for table in soup.find_all('table'):
        for tr in table.find_all('tr'):
            cells = tr.find_all(['td', 'th'])
            while cells and not cells[-1].get_text(strip=True):
                cells[-1].decompose()
                cells = tr.find_all(['td', 'th'])

    for table in soup.find_all('table'):
        rows_to_remove = []
        for tr in table.find_all('tr'):
            if not any(cell.get_text(strip=True) for cell in tr.find_all(['td', 'th'])):
                rows_to_remove.append(tr)
        for tr in rows_to_remove:
            tr.decompose()

    for table in list(soup.find_all('table')):
        if not table.get_text(strip=True):
            table.decompose()

    html_str = str(soup)
    lines = html_str.split('\n')
    result = []
    prev_empty = False

    for line in lines:
        if line.strip():
            result.append(line)
            prev_empty = False
        elif not prev_empty:
            result.append('')
            prev_empty = True

    return '\n'.join(result)


def extract_from_excel(file_path: Path) -> str:
    try:
        excel_file = pd.ExcelFile(file_path)
        all_tables = []
        
        for sheet_name in excel_file.sheet_names:
            df = pd.read_excel(file_path, sheet_name=sheet_name, header=None)
            df = df.dropna(how='all').fillna('')
            
            if df.empty:
                continue
            
            html_table = df.to_html(index=False, header=False, border=1, escape=False)
            all_tables.append(f"<h3>Лист: {sheet_name}</h3>\n{html_table}")
        
        if all_tables:
            return '\n\n'.join(all_tables)
        else:
            return f"[Нет данных в {file_path}]"
            
    except Exception as e:
        return f"[Ошибка чтения Excel: {e}]"


def process_with_docling(file_path: Path, from_format: str, docling_base_url: str = None, cancel_checker=None) -> str:
    _base = (docling_base_url or "http://localhost:5001").rstrip("/")
    _async_url = f"{_base}/v1/convert/file/async"
    _status_url = f"{_base}/v1/status/poll"
    _result_url = f"{_base}/v1/result"
    with open(file_path, 'rb') as f:
        files = {'files': (file_path.name, f, 'application/octet-stream')}
        data = {
            'from_formats': from_format,
            'to_formats': 'html',
            'target_type': 'inbody',
            'include_images': 'false',
            'image_export_mode': 'placeholder',
            'do_table_structure': 'true',
        }

        if from_format == 'pdf':
            data['do_ocr'] = 'true'
            data['force_ocr'] = 'true'
            data['ocr_lang'] = ['ru', 'en']
            data['ocr_engine'] = 'easyocr'
            data['images_scale'] = '3.0'
            data['table_cell_matching'] = 'false'
            data['table_mode'] = 'accurate'

        if callable(cancel_checker) and cancel_checker():
            return "[Отменено пользователем]"

        logger = logging.getLogger("tender")
        try:
            logger.info(f"[Docling] Отправка файла {file_path.name} (формат: {from_format})")
        except Exception as e:
            logging.getLogger("tender").exception("[Docling] Failed to emit send-log for %s: %s", file_path.name, e)
        response = requests.post(_async_url, files=files, data=data, timeout=30)
        if response.status_code != 200:
            logging.getLogger("tender").error(f"[Docling] Ошибка: {response.status_code} {response.text}", exc_info=True)
            return f"[Ошибка Docling: {response.status_code}] {response.text}"

        task_id = response.json().get('task_id')
        if not task_id:
            return f"[Ошибка: не получен task_id] {response.text}"

        max_wait = 180
        wait_time = 0
        while wait_time < max_wait:
            if callable(cancel_checker) and cancel_checker():
                return "[Отменено пользователем]"
            time.sleep(2)
            wait_time += 2
            status_resp = requests.get(f"{_status_url}/{task_id}", timeout=10)
            if status_resp.status_code != 200:
                logging.getLogger("tender").warning(f"[Docling] Статус {status_resp.status_code} при polling", exc_info=True)
                continue
            status_data = status_resp.json()
            task_status = status_data.get('task_status')
            if task_status == 'success':
                result_resp = requests.get(f"{_result_url}/{task_id}", timeout=10)
                if result_resp.status_code == 200:
                    result = result_resp.json()
                    html_content = result.get('document', {}).get('html_content', '')
                    if html_content:
                        return html_content
                    else:
                        logging.getLogger("tender").warning("[Docling] Предупреждение: Docling не вернул HTML", exc_info=True)
                        return "[Предупреждение: Docling не вернул HTML]"
                else:
                    logging.getLogger("tender").error(f"[Docling] Ошибка получения результата: {result_resp.status_code}", exc_info=True)
                    return f"[Ошибка получения результата: {result_resp.status_code}]"
            elif task_status == 'failure':
                error_msg = status_data.get('error_message', 'Неизвестная ошибка')
                logging.getLogger("tender").error(f"[Docling] Ошибка обработки: {error_msg}", exc_info=True)
                return f"[Ошибка обработки: {error_msg}]"
        return "[Ошибка: таймаут]"


def fix_pdf_rotation(input_path: Path) -> tuple:
    from pypdf import PdfReader, PdfWriter
    from pdf2image import convert_from_path
    import pytesseract

    import logging
    logger = logging.getLogger("tender")
    try:
        reader = PdfReader(str(input_path))
    except Exception as e:
        try:
            logger.warning(f"[PDF] Не удалось прочитать PDF для проверки поворота: {e}")
        except Exception:
            pass
        return input_path, False

    osd_rotations = [0] * len(reader.pages)

    try:
        preview_images = convert_from_path(str(input_path), dpi=200)
        for i, img in enumerate(preview_images[:len(osd_rotations)]):
            try:
                osd = pytesseract.image_to_osd(img, output_type=pytesseract.Output.DICT)
                conf = float(osd.get('orientation_conf', 0) or 0)
                rot = int(osd.get('rotate', 0) or 0)
                if rot in {90, 180, 270} and conf >= 1.5:
                    osd_rotations[i] = rot
            except Exception:
                pass
    except Exception as e:
        try:
            logger.warning(f"[PDF] OSD недоступен, поворот не меняю: {e}")
        except Exception:
            pass

    if not any(osd_rotations):
        return input_path, False

    try:
        logger.info("[PDF] Нормализую поворот страниц через pypdf (по OSD Tesseract)")
    except Exception:
        pass

    writer = PdfWriter()
    for i, page in enumerate(reader.pages):
        osd_rot = osd_rotations[i]
        if osd_rot:
            page.rotate(osd_rot)
        writer.add_page(page)

    tmp = tempfile.NamedTemporaryFile(suffix='.pdf', delete=False)
    tmp.close()
    with open(tmp.name, 'wb') as f:
        writer.write(f)
    try:
        logger.info(f"[PDF] Исправленный PDF сохранён во временный файл: {tmp.name}")
    except Exception:
        pass
    return Path(tmp.name), True


def extract_text_from_file(filepath: str, docling_base_url: str = None, cancel_checker=None) -> str:
    file_path = Path(filepath)
    if not file_path.exists():
        return f"[Ошибка: файл {filepath} не существует]"

    ext = file_path.suffix.lower()
    
    if callable(cancel_checker) and cancel_checker():
        return "[Отменено пользователем]"

    if ext == '.docx':
        logger = logging.getLogger("tender")
        try:
            logger.info(f"[DOCX] Конвертация через pandoc {file_path.name}...")
        except Exception:
            pass
        html_raw = convert_docx_to_html(str(file_path))
        if "❌" in html_raw:
            try:
                logger.warning(f"Ошибка pandoc, fallback на локальный парсинг: {html_raw.split('❌')[1]}")
            except Exception:
                pass
            return extract_from_docx(file_path)  # fallback
        html_clean = clean_html_aggressive(html_raw)
        try:
            logger.info(f"✓ Очищено: {len(html_raw)} → {len(html_clean)} байт ({100*len(html_clean)/len(html_raw):.1f}%)")
        except Exception:
            pass
        return html_clean
    
    if callable(cancel_checker) and cancel_checker():
        return "[Отменено пользователем]"

    if ext in ['.xls', '.xlsx']:
        logger = logging.getLogger("tender")
        try:
            logger.info(f"[Excel] Чтение {file_path.name}...")
        except Exception:
            pass
        return extract_from_excel(file_path)
    
    if callable(cancel_checker) and cancel_checker():
        return "[Отменено пользователем]"

    if ext == '.pdf':
        logger = logging.getLogger("tender")
        fixed_path, is_temp = fix_pdf_rotation(file_path)
        if is_temp:
            try:
                logger.info(f"[PDF] Временный файл сохранён: {fixed_path}")
            except Exception:
                pass
        return process_with_docling(fixed_path, 'pdf', docling_base_url, cancel_checker)
    
    from_format = {
        '.pptx': 'pptx',
        '.txt': 'md',
    }.get(ext, 'auto')
    
    with open(file_path, 'rb') as f:
        files = {'files': (file_path.name, f, 'application/octet-stream')}
        data = {
            'from_formats': from_format,
            'to_formats': 'html',
            'target_type': 'inbody',
            'include_images': 'false',
            'images_scale': '1.0',
        }
        try:
            if callable(cancel_checker) and cancel_checker():
                return "[Отменено пользователем]"
            _sync_url = f"{(docling_base_url or 'http://localhost:5001').rstrip('/')}/v1/convert/file"
            response = requests.post(_sync_url, files=files, data=data, timeout=120)
            if response.status_code == 200:
                result = response.json()
                html_content = result.get('document', {}).get('html_content', '')
                if html_content:
                    return html_content
                else:
                    return "[Предупреждение: Docling не вернул HTML]"
                
            else:
                return f"[Ошибка Docling: {response.status_code}] {response.text}"
        except Exception as e:
            return f"[Ошибка при обращении к Docling: {e}]"


def extract_texts_from_files(
    filepaths: Iterable[str],
    docling_base_url: str = None,
    cancel_checker: Callable[[], bool] | None = None,
    max_workers: int = 4,
    progress_cb: Callable[[str, str, float], None] | None = None,
) -> List[str]:
    """Extract text for multiple files in parallel using threads.

    Returns a list of extracted texts in the same order as `filepaths`.

    - `progress_cb(filepath, content, elapsed_sec)` is called for each completed file.
    - `cancel_checker()` is polled before scheduling and between completions; if it
      returns True the function will stop waiting for remaining tasks and return
      results collected so far (preserving original ordering where possible).
    """
    fps = list(filepaths)
    results: Dict[str, str] = {}
    if callable(cancel_checker) and cancel_checker():
        return ["[Отменено пользователем]" for _ in fps]

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        futures = {ex.submit(extract_text_from_file, fp, docling_base_url, cancel_checker): fp for fp in fps}

        for fut in concurrent.futures.as_completed(futures):
            fp = futures[fut]
            start = None
            try:
                # result may be long-running; capture it
                content = fut.result()
            except Exception as e:
                content = f"[Ошибка извлечения: {e}]"
            results[fp] = content
            if callable(progress_cb):
                try:
                    progress_cb(fp, content, 0.0)
                except Exception:
                    pass

            if callable(cancel_checker) and cancel_checker():
                # Stop processing further results
                break

    # Return in original order, using empty string when missing
    return [results.get(fp, "") for fp in fps]
