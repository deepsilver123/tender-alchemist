#!/bin/sh
set -e

MODEL="${OLLAMA_MODEL:-ministral-3:3b}"
TARGET_DIR="/root/.ollama"
PRELOADED_DIR="/preloaded/.ollama"

if [ ! -d "$TARGET_DIR" ] || [ -z "$(ls -A "$TARGET_DIR" 2>/dev/null)" ]; then
  echo "[ollama-entrypoint] /root/.ollama is empty, initializing from image cache..."
  mkdir -p "$TARGET_DIR"
  cp -a "$PRELOADED_DIR"/. "$TARGET_DIR"/
  echo "[ollama-entrypoint] Initialization complete."
else
  echo "[ollama-entrypoint] /root/.ollama already contains data, preserving existing models."
fi

exec ollama serve
