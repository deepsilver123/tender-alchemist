from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict

from fastapi import FastAPI, File, Form, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import logging
from core import analyze_files
import requests
from core.config import DATA_DIR, LOG_DIR, MINISTRAL_URL, DOCLING_URL
import shutil


@dataclass
class TaskState:
    id: str
    status: str = "created"
    logs: list[str] = field(default_factory=list)
    parsed: Any = None
    result_path: str | None = None
    prompt_path: str | None = None
    raw_path: str | None = None
    error: str | None = None
    files: list[str] = field(default_factory=list)


MAIN_LOOP: asyncio.AbstractEventLoop | None = None


@asynccontextmanager
async def _lifespan(app: FastAPI):
    global MAIN_LOOP
    MAIN_LOOP = asyncio.get_running_loop()
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


async def _run_task(task_id: str, file_paths: list[str], ministral_url: str | None, ministral_model: str | None, docling_base: str | None) -> None:
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

        # Preflight: check external services (Ministral, Docling). If unreachable, abort.
        tender_logger = logging.getLogger("tender")
        effective_ministral = ministral_url or MINISTRAL_URL
        effective_docling = docling_base or DOCLING_URL

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
        if effective_ministral:
            ok, detail = _service_up(effective_ministral)
            if not ok:
                unavailable.append(("Ministral", effective_ministral, detail))
        if effective_docling:
            ok, detail = _service_up(effective_docling)
            if not ok:
                unavailable.append(("Docling", effective_docling, detail))

        if unavailable:
            msg = "; ".join(f"{n}({u}): {d}" for n, u, d in unavailable)
            state.status = "failed"
            state.error = f"Сервисы недоступны: {msg}"
            _append_log(task_id, f"❌ {state.error}")
            await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
            tender_logger.error("Preflight failed: %s", state.error)
            return

        from .analysis_worker import run_analysis as web_run

        result = await loop.run_in_executor(
            None,
            lambda: web_run(task_id, file_paths, send_log_threadsafe, ministral_url, ministral_model, docling_base),
        )

        state.status = "done"
        state.parsed = result.get("parsed")
        # We no longer use a separate 'results' folder; parsed result saved under LOG_DIR/<task_id>/result.json
        state.result_path = None
        state.prompt_path = None
        state.raw_path = result.get("raw_path")
        await _broadcast(task_id, {"type": "status", "status": state.status})
        try:
            await _broadcast(task_id, {"type": "result_data", "json": state.parsed})
        except Exception:
            pass
    except Exception as e:
        state.status = "failed"
        state.error = str(e)
        _append_log(task_id, f"❌ Ошибка: {e}")
        await _broadcast(task_id, {"type": "status", "status": state.status, "error": state.error})
    finally:
        # Clean up uploaded files to free disk space
        try:
            upload_dir = UPLOAD_ROOT / task_id
            if upload_dir.exists():
                shutil.rmtree(upload_dir)
                tender_logger.info("Removed upload dir %s", upload_dir)
        except Exception:
            try:
                tender_logger.exception("Failed to remove upload dir %s", upload_dir)
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
        my_tasks.append({"id": tid, "status": st.status if st else "unknown", "files": st.files if st else []})

    context: dict[str, Any] = {"request": request, "my_tasks": my_tasks}

    if task_id:
        # Only show task if it belongs to this session
        if task_id not in SESSIONS.get(session_id, set()):
            context.update(task_id=task_id, status="not_found", initial_logs="", initial_json="", download_url="", error="Task not found")
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
            # raw availability: check persisted log file directly
            raw_path = LOG_DIR / task_id / 'raw_answer.log'
            context.update(
                task_id=task_id,
                status=state.status,
                initial_logs=initial_logs,
                initial_json=initial_json,
                raw_available=raw_path.exists(),
                error=state.error or "",
                task_files=state.files,
            )

    response = TEMPLATES.TemplateResponse(request=request, name="index.html", context=context)
    if created_new_session:
        response.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return response


@app.post("/analyze")
async def start_analyze(
    request: Request,
    files: list[UploadFile] = File(...),
    ministral_url: str = Form(default=""),
    ministral_model: str = Form(default=""),
    docling_base: str = Form(default="http://localhost:5001"),
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

    TASKS[task_id] = TaskState(id=task_id, files=[f.filename or "file" for f in files])
    asyncio.create_task(_run_task(task_id, saved, ministral_url or None, ministral_model or None, docling_base or None))
    # Associate task with session
    session_id = request.cookies.get("tender_session")
    created_new_session = False
    if not session_id:
        session_id = uuid.uuid4().hex
        created_new_session = True
    SESSIONS.setdefault(session_id, set()).add(task_id)

    redirect = RedirectResponse(url=f"/?task_id={task_id}", status_code=303)
    if created_new_session:
        redirect.set_cookie("tender_session", session_id, httponly=True, samesite="lax")
    return redirect


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

        while True:
            await websocket.receive_text()
    except Exception:
        pass
    finally:
        WS_CLIENTS.get(task_id, set()).discard(websocket)





@app.get("/raw/{task_id}")
async def raw_response(request: Request, task_id: str):
    # Enforce session-based access to raw AI response
    session_id = request.cookies.get("tender_session")
    if not session_id or task_id not in SESSIONS.get(session_id, set()):
        return HTMLResponse("Not found", status_code=404)

    # Read the persisted raw response from the logs folder (LOG_DIR/<task_id>/raw_answer.log)
    raw_path = LOG_DIR / task_id / 'raw_answer.log'
    if not raw_path.exists():
        return HTMLResponse("Raw response not available", status_code=404)

    try:
        text = raw_path.read_text(encoding="utf-8")
    except Exception:
        return HTMLResponse("Failed to read raw file", status_code=500)

    return HTMLResponse(content=text, media_type="text/plain")
