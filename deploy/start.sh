#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Tender Alchemist — quick deploy script
#
# Usage:
#   chmod +x deploy/start.sh
#   ./deploy/start.sh            # build & start all services
#   ./deploy/start.sh --pull     # pull model after start
#   ./deploy/start.sh --down     # stop & remove containers
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail
cd "$(dirname "$0")/.."

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC}  $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }

# ── Check prerequisites ─────────────────────────────────────────────────────
for cmd in docker; do
    if ! command -v "$cmd" &>/dev/null; then
        error "$cmd not found. Please install Docker first."
        exit 1
    fi
done

# Check docker compose (v2 plugin or standalone)
if docker compose version &>/dev/null; then
    COMPOSE="docker compose"
elif command -v docker-compose &>/dev/null; then
    COMPOSE="docker-compose"
else
    error "docker compose not found. Please install Docker Compose."
    exit 1
fi

# ── Handle --down ────────────────────────────────────────────────────────────
if [[ "${1:-}" == "--down" ]]; then
    info "Stopping all services..."
    $COMPOSE down
    info "Done."
    exit 0
fi

# ── Create .env if missing ───────────────────────────────────────────────────
if [ ! -f .env ]; then
    info "Creating .env from .env.example..."
    cp .env.example .env
fi

# ── Build & start ────────────────────────────────────────────────────────────
info "Building and starting services..."
$COMPOSE up -d --build

info "Waiting for Ollama to become healthy..."
timeout=120
elapsed=0
while ! docker inspect --format='{{.State.Health.Status}}' ta-ollama 2>/dev/null | grep -q healthy; do
    sleep 2
    elapsed=$((elapsed + 2))
    if [ "$elapsed" -ge "$timeout" ]; then
        warn "Ollama did not become healthy in ${timeout}s — continuing anyway"
        break
    fi
done

# ── Pull Ministral model if requested ────────────────────────────────────────
if [[ "${1:-}" == "--pull" ]]; then
    MODEL="${MINISTRAL_MODEL:-ministral:3b}"
    info "Pulling model '${MODEL}' in Ollama..."
    docker exec ta-ollama ollama pull "$MODEL"
    info "Model ready."
fi

# ── Summary ──────────────────────────────────────────────────────────────────
WEBUI_PORT=$(grep -oP 'WEBUI_PORT=\K\d+' .env 2>/dev/null || echo 8000)
echo ""
info "═══════════════════════════════════════════════════════════════"
info " Tender Alchemist is running!"
info ""
info " Web UI:   http://localhost:${WEBUI_PORT}"
info " Ollama:   http://localhost:$(grep -oP 'OLLAMA_PORT=\K\d+' .env 2>/dev/null || echo 11434)"
info " Docling:  http://localhost:$(grep -oP 'DOCLING_PORT=\K\d+' .env 2>/dev/null || echo 5001)"
info ""
info " Logs:     docker compose logs -f webui"
info " Stop:     ./deploy/start.sh --down"
info "═══════════════════════════════════════════════════════════════"
