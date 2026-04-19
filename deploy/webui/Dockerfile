# ─────────────────────────────────────────────────────────────────────────────
# Tender Alchemist — Web UI production image
# Python 3.13 + pandoc + poppler + tesseract + JRE (for aspose-cells via jpype)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ── System dependencies ──────────────────────────────────────────────────────
# pandoc          — DOCX → HTML conversion (file_reader.py)
# poppler-utils   — PDF → image (pdf2image / pdftoppm)
# tesseract-ocr   — page rotation detection (pytesseract OSD)
# default-jre-headless — JVM for aspose-cells via jpype
# libgl1, libglib2.0-0 — OpenCV headless runtime deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-rus \
        default-jre-headless \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Python dependencies ──────────────────────────────────────────────────────
COPY deploy/webui/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# ── Application code ─────────────────────────────────────────────────────────
COPY src/ /app/src/
COPY data/ /app/data/
COPY scripts/ /app/scripts/
COPY run_webui.py /app/run_webui.py

# Ensure runtime directories exist
RUN mkdir -p /app/data/uploads /app/logs

# ── Runtime ──────────────────────────────────────────────────────────────────
EXPOSE 8000

ENV WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8000

CMD ["python", "run_webui.py"]
