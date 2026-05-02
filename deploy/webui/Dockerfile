# ─────────────────────────────────────────────────────────────────────────────
# Tender Alchemist — продакшн имидж веб-интерфейса
# Python 3.13 + pandoc + poppler + tesseract + JRE (для aspose-cells через jpype)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.13-slim

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# ── Системные зависимости ──────────────────────────────────────────────────────────────────
# pandoc          — конвертация DOCX → HTML (file_reader.py)
# poppler-utils   — PDF → изображение (pdf2image / pdftoppm)
# tesseract-ocr   — определение поворота страницы (pytesseract OSD)
# default-jre-headless — JVM для aspose-cells через jpype
# libgl1, libglib2.0-0 — зависимости рантайма OpenCV (headless)
RUN apt-get update && apt-get install -y --no-install-recommends \
        pandoc \
        poppler-utils \
        tesseract-ocr \
        tesseract-ocr-rus \
        default-jre-headless \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Python-зависимости ──────────────────────────────────────────────────────────────────
COPY deploy/webui/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# ── Код приложения ──────────────────────────────────────────────────────────────────────
COPY src/ /app/src/
COPY data/product_terms.json /app/data/product_terms.json
COPY data/catalogs/e2e4_flat.csv /app/data/catalogs/e2e4_flat.csv
COPY scripts/e2e4_ingest.py /app/scripts/e2e4_ingest.py
COPY scripts/index_catalog.py /app/scripts/index_catalog.py
COPY scripts/run_webui.py /app/scripts/run_webui.py

# Создаём директории для работы приложения
RUN mkdir -p /app/data/uploads /app/data/catalogs /app/data/downloads /app/logs

# ── Запуск ────────────────────────────────────────────────────────────────────────────
EXPOSE 8000

ENV WEBUI_HOST=0.0.0.0 \
    WEBUI_PORT=8000 \
    E2E4_CATALOG_URL=https://e2e4online.ru/ws/excel/irkutsk.e2e4online.ru.zip

CMD ["python", "scripts/run_webui.py"]
