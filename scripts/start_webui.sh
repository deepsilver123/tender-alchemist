#!/usr/bin/env bash
set -euo pipefail
IFS=$'\n\t'

# Lightweight start/stop/restart/status script for the web UI.
# Usage:
#   scripts/start_webui.sh start [--host HOST] [--port PORT] [--no-daemon]
#   scripts/start_webui.sh stop
#   scripts/start_webui.sh restart
#   scripts/start_webui.sh status

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$REPO_ROOT/.venv"
REQ_FILE="$REPO_ROOT/requirements.txt"
RUNNER_SCRIPT="$REPO_ROOT/scripts/run_webui.py"
ROOT_RUNNER="$REPO_ROOT/run_webui.py"
ENV_FILE="$REPO_ROOT/.env"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs}"
PIDFILE="$LOG_DIR/webui.pid"
OUTLOG="$LOG_DIR/webui.out"

# defaults (can be overridden by .env, environment or CLI)
HOST_DEFAULT="${WEBUI_HOST:-0.0.0.0}"
PORT_DEFAULT="${WEBUI_PORT:-8000}"

# parse args
CMD="start"
HOST="$HOST_DEFAULT"
PORT="$PORT_DEFAULT"
NO_DAEMON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    start|stop|restart|status) CMD="$1"; shift ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --no-daemon) NO_DAEMON=1; shift ;;
    --help|-h) echo "Usage: $0 [start|stop|restart|status] [--host HOST] [--port PORT] [--no-daemon]"; exit 0 ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

log() { echo "[$(date +'%Y-%m-%d %H:%M:%S')] $*"; }

ensure_venv() {
  if [ ! -d "$VENV_DIR" ]; then
    log "Creating virtualenv at $VENV_DIR"
    python3 -m venv "$VENV_DIR"
  fi
  # shellcheck disable=SC1090
  source "$VENV_DIR/bin/activate"
  pip install -U pip setuptools wheel
  if [ -f "$REQ_FILE" ]; then
    log "Installing Python requirements from $REQ_FILE"
    pip install -r "$REQ_FILE"
  fi
  log "Ensuring uvicorn[standard], websockets and wsproto are installed"
  pip install -U "uvicorn[standard]" websockets wsproto
  mkdir -p "$LOG_DIR"
}

start_server() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" >/dev/null 2>&1; then
      log "Server already running (PID $PID)"
      return 0
    else
      log "Removing stale PID file"
      rm -f "$PIDFILE"
    fi
  fi
  ensure_venv
  # choose runner (scripts/run_webui.py preferred)
  if [ -f "$RUNNER_SCRIPT" ]; then
    RUN_ARR=("$VENV_DIR/bin/python" "$RUNNER_SCRIPT" "--host" "$HOST" "--port" "$PORT")
  elif [ -f "$ROOT_RUNNER" ]; then
    RUN_ARR=("$VENV_DIR/bin/python" "$ROOT_RUNNER")
    export WEBUI_HOST="$HOST"
    export WEBUI_PORT="$PORT"
  else
    log "No runner script found (scripts/run_webui.py or run_webui.py)"
    return 1
  fi

  if [ "$NO_DAEMON" -eq 1 ]; then
    log "Starting server in foreground: ${RUN_ARR[*]}"
    exec "${RUN_ARR[@]}"
  else
    log "Starting server (daemon) with command: ${RUN_ARR[*]}"
    nohup "${RUN_ARR[@]}" > "$OUTLOG" 2>&1 &
    echo $! > "$PIDFILE"
    sleep 0.5
    if kill -0 "$(cat "$PIDFILE")" >/dev/null 2>&1; then
      log "Server started (PID $(cat "$PIDFILE")), logs: $OUTLOG"
      return 0
    else
      log "Failed to start server; see logs: $OUTLOG"
      return 1
    fi
  fi
}

stop_server() {
  if [ ! -f "$PIDFILE" ]; then
    log "No PID file, server not running?"
    return 0
  fi
  PID=$(cat "$PIDFILE")
  if kill -0 "$PID" >/dev/null 2>&1; then
    log "Stopping server (PID $PID)"
    kill "$PID"
    for i in {1..10}; do
      if kill -0 "$PID" >/dev/null 2>&1; then
        sleep 1
      else
        break
      fi
    done
    if kill -0 "$PID" >/dev/null 2>&1; then
      log "PID still alive; sending SIGKILL"
      kill -9 "$PID"
    fi
  else
    log "Process $PID not running"
  fi
  rm -f "$PIDFILE"
  log "Stopped"
}

status_server() {
  if [ -f "$PIDFILE" ]; then
    PID=$(cat "$PIDFILE")
    if kill -0 "$PID" >/dev/null 2>&1; then
      echo "Running (PID $PID)"
      return 0
    else
      echo "Stale PID file ($PID)"
      return 1
    fi
  else
    echo "Not running (no PID file)"
    return 3
  fi
}

case "$CMD" in
  start) start_server ;;
  stop) stop_server ;;
  restart) stop_server; start_server ;;
  status) status_server ;;
  *) echo "Unknown command: $CMD"; exit 1 ;;
esac

exit 0
