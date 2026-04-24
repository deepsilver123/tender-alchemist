from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

MONTH_NAMES_RU = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

def format_russian_datetime(dt: datetime) -> str:
    return f"{dt.day} {MONTH_NAMES_RU[dt.month - 1]} {dt.year}, {dt:%H:%M}"

from fastapi import Body, FastAPI, File, Form, Request, UploadFile, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import logging
import requests
from core.config import DATA_DIR, LOG_DIR, LLM_URL, DOCLING_BASE_URL
import shutil


@dataclass
class TaskState:
    id: str
    status: str = "created"
    logs: list[str] = field(default_factory=list)
    parsed: Any = None
    search_results: Any = None
    result_path: str | None = None
    prompt_path: str | None = None
    raw_path: str | None = None
    error: str | None = None
    files: list[str] = field(default_factory=list)
    title: str = ""
    created_at: str = ""
    llm_url: str | None = None
    llm_model: str | None = None
    docling_base: str | None = None
    progress_current: int | None = None
    progress_total: int | None = None
    cancel_requested: bool = False


MAIN_LOOP: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
    _load_state()
    from webui.catalog_scheduler import start_scheduler
    start_scheduler()
    yield


app = FastAPI(title="Tender Alchemist Web", lifespan=_lifespan)

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Ensure data and log directories exist and configure logging to file
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_ROOT = DATA_DIR / "uploads"
UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)

# Configure 'tender' logger to write to LOG_DIR/webui.log
try:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    tender_logger = logging.getLogger("tender")
    tender_logger.setLevel(logging.INFO)
    # Avoid adding duplicate handlers if module reloaded
    if not any(isinstance(h, logging.FileHandler) and str((LOG_DIR / 'webui.log')) in getattr(h, 'baseFilename', '') for h in tender_logger.handlers):
        fh = logging.FileHandler(LOG_DIR / "webui.log", encoding='utf-8')
        fh.setLevel(logging.INFO)
        fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        fh.setFormatter(fmt)
        tender_logger.addHandler(fh)
except Exception:
    # If logging setup fails, fall back silently (do not break startup)
    try:
        logging.getLogger("tender").exception("Не удалось настроить файл логов")
    except Exception:
        pass

TASKS: Dict[str, TaskState] = {}
WS_CLIENTS: Dict[str, set[WebSocket]] = {}
# session_id -> set(task_id)
SESSIONS: Dict[str, set[str]] = {}

STATE_FILE = DATA_DIR / "state.json"


def _save_state() -> None:
    """Persist TASKS metadata and SESSIONS to disk (JSON)."""
    try:
        tasks_data = {}
        for tid, st in TASKS.items():
            tasks_data[tid] = {
                "id": st.id,
                "status": st.status,
                "files": st.files,
                "title": st.title,
                "error": st.error,
                "created_at": st.created_at,
                "llm_url": st.llm_url,
                "llm_model": st.llm_model,
                "docling_base": st.docling_base,
                "progress_current": st.progress_current,
                "progress_total": st.progress_total,
                "cancel_requested": st.cancel_requested,
            }
        sessions_data = {sid: sorted(tids) for sid, tids in SESSIONS.items()}
        payload = {"tasks": tasks_data, "sessions": sessions_data}
        # atomic write via temp file
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception:
        logging.getLogger("tender").exception("Failed to save state")


def _load_state() -> None:
    """Restore TASKS and SESSIONS from disk on startup."""
    if not STATE_FILE.exists():
        return
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        logging.getLogger("tender").exception("Failed to read state file")
        return

    for tid, meta in data.get("tasks", {}).items():
        status = meta.get("status", "done")
        created_at = meta.get("created_at", "")
        # Treat tasks that were running when server stopped as failed
        if status in ("created", "running"):
            status = "failed"
            meta["error"] = meta.get("error") or "Сервер был перезапущен"
        # Restore logs from processing.log
        logs: list[str] = []
        log_file = LOG_DIR / tid / "processing.log"
        if log_file.exists():
            try:
                logs = log_file.read_text(encoding="utf-8").splitlines()
            except Exception:
                pass
        # Restore parsed result from result.json
        parsed = None
        result_file = LOG_DIR / tid / "result.json"
        if result_file.exists():
            try:
                parsed = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        raw_path_file = LOG_DIR / tid / "raw_answer.log"
        # Restore search results
        search_results = None
        search_results_file = LOG_DIR / tid / "search_results.json"
        if search_results_file.exists():
            try:
                search_results = json.loads(search_results_file.read_text(encoding="utf-8"))
            except Exception:
                pass
        TASKS[tid] = TaskState(
            id=tid,
            status=status,
            logs=logs,
            parsed=parsed,
            search_results=search_results,
            error=meta.get("error"),
            files=meta.get("files", []),
            title=meta.get("title", ""),
            created_at=created_at,
            llm_url=meta.get("llm_url") or meta.get("ministral_url"),
            llm_model=meta.get("llm_model") or meta.get("ministral_model"),
            docling_base=meta.get("docling_base"),
            progress_current=meta.get("progress_current"),
            progress_total=meta.get("progress_total"),
            cancel_requested=bool(meta.get("cancel_requested", False)),
            raw_path=str(raw_path_file) if raw_path_file.exists() else None,
        )

    for sid, tids in data.get("sessions", {}).items():
        # Only keep task IDs that were actually restored
        valid = {t for t in tids if t in TASKS}
        if valid:
            SESSIONS[sid] = valid


def _find_active_catalog_task() -> TaskState | None:
    """Return the current active catalog upload task, if any."""
    for task in reversed(TASKS.values()):
        if task.status in ("created", "running", "cancelling"):
            if task.title.startswith("Индекс e2e4:"):
                return task
    return None


async def _broadcast(task_id: str, payload: dict[str, Any]) -> None:
    clients = WS_CLIENTS.get(task_id, set()).copy()
    for ws in clients:
        try:
            await ws.send_text(json.dumps(payload, ensure_ascii=False))
        except Exception:
            try:
                WS_CLIENTS.get(task_id, set()).discard(ws)
            except Exception:
                pass


def _schedule_broadcast(task_id: str, payload: dict[str, Any]) -> None:
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(_broadcast(task_id, payload))
        return
    except RuntimeError:
        pass

    if MAIN_LOOP is not None and not MAIN_LOOP.is_closed():
        coro = _broadcast(task_id, payload)
        try:
            asyncio.run_coroutine_threadsafe(coro, MAIN_LOOP)
        except Exception:
            coro.close()


def _append_log(task_id: str, line: str) -> None:
    state = TASKS[task_id]
    # avoid broadcasting consecutive duplicate lines
    if state.logs and state.logs[-1] == line:
        return
    # keep in-memory copy for UI
    state.logs.append(line)
    # broadcast to websocket clients
    _schedule_broadcast(task_id, {"type": "log", "text": line})

    # persist into per-task processing.log
    try:
        task_log_dir = LOG_DIR / task_id
        task_log_dir.mkdir(parents=True, exist_ok=True)
        with open(task_log_dir / "processing.log", "a", encoding="utf-8") as fh:
            fh.write(line.rstrip("\n") + "\n")
    except Exception:
        # never allow logging failures to break processing
        pass


def _update_progress(task_id: str, current: int, total: int) -> None:
    state = TASKS[task_id]
    state.progress_current = current
    state.progress_total = total
    _schedule_broadcast(task_id, {"type": "progress", "current": current, "total": total})


async def _run_task(task_id: str, file_paths: list[str], llm_url: str | None, llm_model: str | None, docling_base: str | None) -> None:
    state = TASKS[task_id]
    state.status = "running"
    await _broadcast(task_id, {"type": "status", "status": state.status})
    try:
        # Run the analyzer in a background thread so the FastAPI event loop
        # stays free to deliver WebSocket broadcasts in real time.
        loop = asyncio.get_running_loop()

        # Thread-safe send_log: appends the line to state and immediately
        # schedules a WS broadcast on the MAIN loop via run_coroutine_threadsafe.
        # This is the key to real-time streaming: we do NOT use asyncio.run() inside
        # the thread (which would create a nested loop and break WS delivery).
        def send_log_threadsafe(line: str) -> None:
            # Use module-level _append_log which handles in-memory, broadcast
            # and persistent write. It is thread-safe because _schedule_broadcast
            # will schedule the coroutine on the MAIN_LOOP when called from
            # a worker thread.
            try:
                _append_log(task_id, line)
            except Exception:
                # fallback to best-effort behaviour
                try:
                    TASKS[task_id].logs.append(line)
                except Exception:
                    pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(task_id, {"type": "log", "text": line}),
                        loop,
                    )
                except Exception:
                    pass

        # Preflight: check external services (LLM, Docling). If unreachable, abort.
        tender_logger = logging.getLogger("tender")
        effective_llm = llm_url or LLM_URL
        effective_docling = docling_base or DOCLING_BASE_URL

        def _service_up(url: str) -> tuple[bool, str]:
            try:
                resp = requests.get(url, timeout=5)
                # treat server errors (5xx) and connection failures as down; other responses mean service reachable
                if resp.status_code >= 500:
                    return False, f"HTTP {resp.status_code}"
                return True, f"HTTP {resp.status_code}"
            except requests.RequestException as e:
                return False, str(e)

        unavailable = []
        if effective_llm:
            ok, detail = _service_up(effective_llm)
            if not ok:
                unavailable.append(("LLM", effective_llm, detail))
        if effective_docling:
            ok, detail = _service_up(effective_docling)
            if not ok:
                unavailable.append(("Docling", effective_docling, detail))

        if unavailable:
            msg = "; ".join(f"{n}({u}): {d}" for n, u, d in unavailable)
            state.status = "failed"
            state.error = f"Сервисы недоступны: {msg}"
            _append_log(task_id, f"❌ {state.error}")
            _save_state()
            await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
            tender_logger.error("Preflight failed: %s", state.error)
            return

        from .worker import run_analysis as web_run

        result = await loop.run_in_executor(
            None,
            lambda: web_run(task_id, file_paths, send_log_threadsafe, llm_url, llm_model, effective_docling),
        )

        state.status = "done"
        state.parsed = result.get("parsed")
        state.search_results = result.get("search_results")
        # We no longer use a separate 'results' folder; parsed result saved under LOG_DIR/<task_id>/result.json
        state.result_path = None
        state.prompt_path = None
        state.raw_path = result.get("raw_path")
        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status})
        try:
            await _broadcast(task_id, {"type": "result_data", "json": state.parsed})
        except Exception:
            pass
        if state.search_results:
            try:
                await _broadcast(task_id, {"type": "search_results", "json": state.search_results})
            except Exception:
                pass
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        _append_log(task_id, f"❌ Ошибка: {e}")
        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
    finally:
        # Keep uploaded files until task deletion so repeat is possible
        pass


async def _run_qdrant_task(task_id: str, file_path: str) -> None:
    state = TASKS[task_id]
    state.status = "running"
    state.progress_current = 0
    state.progress_total = 0
    await _broadcast(task_id, {"type": "status", "status": state.status})
    await _broadcast(task_id, {"type": "progress", "current": 0, "total": 0})
    try:
        loop = asyncio.get_running_loop()

        def send_log_threadsafe(line: str) -> None:
            try:
                _append_log(task_id, line)
            except Exception:
                try:
                    TASKS[task_id].logs.append(line)
                except Exception:
                    pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(task_id, {"type": "log", "text": line}),
                        loop,
                    )
                except Exception:
                    pass

        def send_progress_threadsafe(current: int, total: int) -> None:
            try:
                _update_progress(task_id, current, total)
            except Exception:
                try:
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(task_id, {"type": "progress", "current": current, "total": total}),
                        loop,
                    )
                except Exception:
                    pass

        def cancel_check() -> bool:
            return TASKS[task_id].cancel_requested

        from core.qdrant_indexer import TenderMVPQdrant
        indexer = TenderMVPQdrant(qdrant_path=str(DATA_DIR / "qdrant_db"))
        count = await loop.run_in_executor(
            None,
            lambda: indexer.process_file(
                file_path,
                log_cb=send_log_threadsafe,
                progress_cb=send_progress_threadsafe,
                task_id=task_id,
                cancel_cb=cancel_check,
            ),
        )

        if TASKS[task_id].cancel_requested:
            state.status = "cancelled"
            state.error = "Обработка отменена"
            _append_log(task_id, "⚠️ Обработка отменена пользователем")
        else:
            state.status = "done"
            state.error = None
            state.progress_current = state.progress_total = count
            _append_log(task_id, f"✅ Индексация завершена: {count} товаров добавлено в Qdrant")

        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
        await _broadcast(task_id, {"type": "progress", "current": state.progress_current or 0, "total": state.progress_total or 0})
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        _append_log(task_id, f"❌ Ошибка индексации: {e}")
        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
    finally:
        pass


def _cleanup_upload_dir(task_id: str) -> None:
    upload_dir = UPLOAD_ROOT / task_id
    try:
        if upload_dir.exists():
            shutil.rmtree(upload_dir, ignore_errors=True)
            logging.getLogger("tender").info("Removed upload dir %s", upload_dir)
    except Exception:
        try:
            logging.getLogger("tender").exception("Failed to remove upload dir %s", upload_dir)
        except Exception:
            pass


@app.get("/", response_class=HTMLResponse)
async def index(request: Request, task_id: str | None = None):
    # Manage session cookie
    session_id = request.cookies.get("tender_session")
    created_new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        SESSIONS.setdefault(session_id, set())
        created_new_session = True

    # Build user's task list for the template
    my_task_ids = list(SESSIONS.get(session_id, set()))
    my_tasks = []
    for tid in my_task_ids:
        st = TASKS.get(tid)
        my_tasks.append({
            "id": tid,
            "status": st.status if st else "unknown",
            "files": st.files if st else [],
            "title": st.title if st and st.title else "Без названия",
        })

    context: dict[str, Any] = {"request": request, "my_tasks": my_tasks}

    if task_id:
        # Only show task if it belongs to this session
        if task_id not in SESSIONS.get(session_id, set()):
            context.update(task_id=task_id, status="not_found", initial_logs="", initial_json="", error="Task not found")
            response = TEMPLATES.TemplateResponse(request=request, name="index.html", context=context)
            if created_new_session:
                response.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
            return response

        state = TASKS.get(task_id)
        if state:
            initial_logs = "\n".join(state.logs)
            try:
                initial_json = json.dumps(state.parsed, ensure_ascii=False, indent=2) if state.parsed is not None else ""
            except Exception:
                initial_json = ""
            try:
                initial_search_results = json.dumps(state.search_results, ensure_ascii=False, indent=2) if state.search_results is not None else ""
            except Exception:
                initial_search_results = ""
            context.update(
                task_id=task_id,
                status=state.status,
                initial_logs=initial_logs,
                initial_json=initial_json,
                initial_search_results=initial_search_results,
                error=state.error or "",
                task_files=state.files,
                task_title=state.title or "",
            )

    response = TEMPLATES.TemplateResponse(request=request, name="index.html", context=context)
    if created_new_session:
        response.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return response


@app.post("/analyze")
async def start_analyze(
    request: Request,
    files: list[UploadFile] = File(...),
    llm_url: str = Form(default=""),
    llm_model: str = Form(default=""),
    docling_base: str = Form(default=""),
):
    task_id = uuid.uuid4().hex
    task_dir = UPLOAD_ROOT / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    saved: list[str] = []
    for f in files:
        safe_name = (f.filename or "file").replace("/", "_").replace("\\", "_")
        out_path = task_dir / safe_name
        content = await f.read()
        out_path.write_bytes(content)
        saved.append(str(out_path))

    TASKS[task_id] = TaskState(
        id=task_id,
        files=[f.filename or "file" for f in files],
        title="",
        created_at=format_russian_datetime(datetime.now().astimezone()),
        llm_url=llm_url or None,
        llm_model=llm_model or None,
        docling_base=docling_base or None,
    )
    asyncio.create_task(_run_task(task_id, saved, llm_url or None, llm_model or None, docling_base or None))
    # Associate task with session
    session_id = request.cookies.get("tender_session")
    created_new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        created_new_session = True
    SESSIONS.setdefault(session_id, set()).add(task_id)
    _save_state()

    redirect = RedirectResponse(url=f"/?task_id={task_id}", status_code=303)
    if created_new_session:
        redirect.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return redirect


@app.post("/search_json")
async def start_search_json(
    request: Request,
    manual_json: str = Form(default=""),
    json_file: UploadFile | None = File(default=None),
    llm_url: str = Form(default=""),
    llm_model: str = Form(default=""),
):
    task_id = uuid.uuid4().hex
    task_dir = UPLOAD_ROOT / task_id
    task_dir.mkdir(parents=True, exist_ok=True)

    source = None
    source_name = "manual_input.json"
    if json_file and json_file.filename:
        safe_name = (json_file.filename or "input.json").replace("/", "_").replace("\\", "_")
        source_name = safe_name
        out_path = task_dir / safe_name
        content = await json_file.read()
        out_path.write_bytes(content)
        try:
            source = content.decode("utf-8", errors="ignore")
        except Exception:
            source = ""
    elif manual_json.strip():
        source = manual_json
        out_path = task_dir / source_name
        out_path.write_text(source, encoding="utf-8")
    else:
        return JSONResponse({"error": "Укажите JSON вручную или загрузите JSON-файл."}, status_code=400)

    try:
        parsed = json.loads(source)
    except Exception as exc:
        return JSONResponse({"error": f"Неверный JSON: {exc}"}, status_code=400)

    TASKS[task_id] = TaskState(
        id=task_id,
        status="created",
        files=[source_name],
        title="Поиск по JSON",
        created_at=format_russian_datetime(datetime.now().astimezone()),
        llm_url=llm_url or None,
        llm_model=llm_model or None,
    )

    asyncio.create_task(_run_json_task(task_id, parsed, llm_url or None, llm_model or None))

    session_id = request.cookies.get("tender_session")
    created_new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        created_new_session = True
    SESSIONS.setdefault(session_id, set()).add(task_id)
    _save_state()

    response = JSONResponse({"status": "ok", "task_id": task_id})
    if created_new_session:
        response.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return response


async def _run_json_task(
    task_id: str,
    parsed_json: dict,
    llm_url: str | None,
    llm_model: str | None,
) -> None:
    state = TASKS[task_id]
    state.status = "running"
    await _broadcast(task_id, {"type": "status", "status": state.status})
    try:
        loop = asyncio.get_running_loop()

        def send_log_threadsafe(line: str) -> None:
            try:
                _append_log(task_id, line)
            except Exception:
                try:
                    TASKS[task_id].logs.append(line)
                except Exception:
                    pass
                try:
                    asyncio.run_coroutine_threadsafe(
                        _broadcast(task_id, {"type": "log", "text": line}),
                        loop,
                    )
                except Exception:
                    pass

        from .worker import run_search_json as web_run

        result = await loop.run_in_executor(
            None,
            lambda: web_run(task_id, parsed_json, send_log_threadsafe, llm_url, llm_model),
        )

        state.status = "done"
        state.parsed = result.get("parsed")
        state.search_results = result.get("search_results")
        state.result_path = None
        state.prompt_path = None
        state.raw_path = result.get("raw_path")
        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status})
        try:
            await _broadcast(task_id, {"type": "result_data", "json": state.parsed})
        except Exception:
            pass
        if state.search_results:
            try:
                await _broadcast(task_id, {"type": "search_results", "json": state.search_results})
            except Exception:
                pass
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        _append_log(task_id, f"❌ Ошибка: {e}")
        _save_state()
        await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
    finally:
        pass


@app.post("/task/{task_id}/rename")
async def rename_task(request: Request, task_id: str, payload: dict[str, str] = Body(...)):
    session_id = request.cookies.get("tender_session")
    if not session_id or task_id not in SESSIONS.get(session_id, set()) or task_id not in TASKS:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    title = (payload.get("title") or "").strip()
    TASKS[task_id].title = title
    _save_state()
    return JSONResponse({"status": "ok", "title": title or "Без названия"})


@app.delete("/task/{task_id}")
async def delete_task(request: Request, task_id: str):
    session_id = request.cookies.get("tender_session")
    if not session_id or task_id not in SESSIONS.get(session_id, set()) or task_id not in TASKS:
        return JSONResponse({"error": "Task not found"}, status_code=404)
    TASKS.pop(task_id, None)
    for tids in SESSIONS.values():
        tids.discard(task_id)
    try:
        shutil.rmtree(LOG_DIR / task_id, ignore_errors=True)
    except Exception:
        pass
    try:
        shutil.rmtree(UPLOAD_ROOT / task_id, ignore_errors=True)
    except Exception:
        pass
    _save_state()
    return JSONResponse({"status": "ok"})


@app.post("/task/{task_id}/repeat")
async def repeat_task(request: Request, task_id: str):
    session_id = request.cookies.get("tender_session")
    if not session_id or task_id not in SESSIONS.get(session_id, set()) or task_id not in TASKS:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    original = TASKS[task_id]
    upload_dir = UPLOAD_ROOT / task_id
    if not upload_dir.exists() or not any(upload_dir.iterdir()):
        return JSONResponse({"error": "Исходные файлы задачи недоступны. Повторите загрузку."}, status_code=404)

    new_task_id = uuid.uuid4().hex
    new_upload_dir = UPLOAD_ROOT / new_task_id
    new_upload_dir.mkdir(parents=True, exist_ok=True)
    saved_files: list[str] = []
    for child in sorted(upload_dir.iterdir()):
        if child.is_file():
            dest = new_upload_dir / child.name
            shutil.copy2(child, dest)
            saved_files.append(str(dest))

    TASKS[new_task_id] = TaskState(
        id=new_task_id,
        files=original.files.copy(),
        title=f"Повтор {original.title or original.id[:8]}",
        created_at=format_russian_datetime(datetime.now().astimezone()),
        llm_url=original.llm_url,
        llm_model=original.llm_model,
        docling_base=original.docling_base,
    )
    SESSIONS.setdefault(session_id, set()).add(new_task_id)
    _save_state()
    asyncio.create_task(_run_task(new_task_id, saved_files, original.llm_url, original.llm_model, original.docling_base))
    return JSONResponse({"status": "ok", "task_id": new_task_id})


@app.get("/task/{task_id}")
async def task_page(request: Request, task_id: str):
    # Redirect to index; index will enforce session ownership
    return RedirectResponse(url=f"/?task_id={task_id}", status_code=303)


@app.websocket("/ws/{task_id}")
async def ws_task(task_id: str, websocket: WebSocket):

    # Accept connections without enforcing the session cookie. This makes
    # the WS connection more robust across redirects and clients while the
    # task is running. If task doesn't exist, respond with not_found.
    await websocket.accept()
    if task_id not in TASKS:
        await websocket.send_text(json.dumps({"type": "status", "status": "not_found"}, ensure_ascii=False))
        await websocket.close()
        return

    WS_CLIENTS.setdefault(task_id, set()).add(websocket)
    state = TASKS[task_id]

    try:
        await websocket.send_text(json.dumps({"type": "status", "status": state.status, "error": state.error}, ensure_ascii=False))
        if state.parsed is not None:
            await websocket.send_text(json.dumps({"type": "result_data", "json": state.parsed}, ensure_ascii=False))
        if state.search_results is not None:
            await websocket.send_text(json.dumps({"type": "search_results", "json": state.search_results}, ensure_ascii=False))

        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        WS_CLIENTS.get(task_id, set()).discard(websocket)





@app.get("/history", response_class=HTMLResponse)
async def history_page(request: Request):
    session_id = request.cookies.get("tender_session")
    created_new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        SESSIONS.setdefault(session_id, set())
        created_new_session = True

    my_task_ids = list(SESSIONS.get(session_id, set()))
    # Build rich task list sorted by most-recent first (uuid4 hex is unordered; use insertion order via TASKS dict)
    all_task_ids_ordered = list(TASKS.keys())
    my_task_ids_ordered = [t for t in reversed(all_task_ids_ordered) if t in set(my_task_ids)]

    tasks_detail = []
    for tid in my_task_ids_ordered:
        st = TASKS.get(tid)
        if not st:
            continue
        # Load logs from file if not in memory
        logs_text = "\n".join(st.logs)
        if not logs_text:
            log_file = LOG_DIR / tid / "processing.log"
            if log_file.exists():
                try:
                    logs_text = log_file.read_text(encoding="utf-8")
                except Exception:
                    logs_text = ""
        tasks_detail.append({
            "id": tid,
            "status": st.status,
            "files": st.files,
            "error": st.error or "",
            "logs": logs_text,
            "created_at": st.created_at,
        })

    my_tasks = [{"id": t["id"], "status": t["status"], "files": t["files"]} for t in tasks_detail]
    context: dict[str, Any] = {
        "request": request,
        "my_tasks": my_tasks,
        "tasks_detail": tasks_detail,
    }
    response = TEMPLATES.TemplateResponse(request=request, name="history.html", context=context)
    if created_new_session:
        response.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return response


@app.get("/logs/{task_id}", response_class=HTMLResponse)
async def task_logs(request: Request, task_id: str):
    """Return raw log text for a task (session-scoped)."""
    session_id = request.cookies.get("tender_session")
    if not session_id or task_id not in SESSIONS.get(session_id, set()):
        return HTMLResponse("Not found", status_code=404)
    log_file = LOG_DIR / task_id / "processing.log"
    if not log_file.exists():
        state = TASKS.get(task_id)
        text = "\n".join(state.logs) if state else ""
    else:
        try:
            text = log_file.read_text(encoding="utf-8")
        except Exception:
            return HTMLResponse("Failed to read log file", status_code=500)
    return HTMLResponse(content=text, media_type="text/plain")


@app.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request, error_message: str | None = None):
    active_task = _find_active_catalog_task()
    return TEMPLATES.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "request": request,
            "active_task": active_task,
            "error_message": error_message,
        },
    )

@app.get("/admin/status/{task_id}", response_class=HTMLResponse)
async def admin_status(request: Request, task_id: str):
    state = TASKS.get(task_id)
    if state is None:
        return HTMLResponse("Task not found", status_code=404)

    return TEMPLATES.TemplateResponse(
        request=request,
        name="admin_status.html",
        context={
            "request": request,
            "task_id": task_id,
            "status": state.status,
            "logs": "\n".join(state.logs),
            "error": state.error or "",
            "progress_current": state.progress_current or 0,
            "progress_total": state.progress_total or 0,
            "filename": state.files[0] if state.files else "файл",
        },
    )

@app.post("/admin/cancel/{task_id}")
async def admin_cancel(request: Request, task_id: str):
    state = TASKS.get(task_id)
    if state is None:
        return JSONResponse({"error": "Task not found"}, status_code=404)

    if state.status in ("done", "failed", "cancelled"):
        return JSONResponse({"status": "ok", "message": "Задача уже завершена"})

    state.cancel_requested = True
    if state.status not in ("cancelled", "failed"):
        state.status = "cancelling"
    _append_log(task_id, "⚠️ Запрошена отмена обработки")
    _save_state()
    await _broadcast(task_id, {"type": "status", "status": state.status})
    return JSONResponse({"status": "ok"})

@app.post("/admin/upload_catalog")
async def admin_upload_catalog(request: Request, file: UploadFile = File(...)):
    from core.config import DATA_DIR
    import uuid

    active_task = _find_active_catalog_task()
    if active_task is not None:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="admin.html",
            context={
                "request": request,
                "active_task": active_task,
                "error_message": "Сейчас выполняется обработка прайс-листа. Новую загрузку нельзя запускать до её завершения.",
            },
        )

    task_id = uuid.uuid4().hex
    out_dir = DATA_DIR / "catalogs"
    out_dir.mkdir(parents=True, exist_ok=True)

    safe_name = f"{task_id}_{file.filename}"
    out_path = out_dir / safe_name
    content = await file.read()
    out_path.write_bytes(content)

    TASKS[task_id] = TaskState(
        id=task_id,
        status="created",
        files=[file.filename or "file"],
        title=f"Индекс e2e4: {file.filename}",
        created_at=format_russian_datetime(datetime.now().astimezone()),
        llm_url=None,
        llm_model=None,
        docling_base=None,
        progress_current=0,
        progress_total=0,
    )
    _save_state()
    asyncio.create_task(_run_qdrant_task(task_id, str(out_path)))

    return TEMPLATES.TemplateResponse(
        request=request,
        name="admin.html",
        context={
            "request": request,
            "active_task": TASKS[task_id],
            "error_message": None,
        },
    )

