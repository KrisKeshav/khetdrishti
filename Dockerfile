# Works on Hugging Face Spaces (Docker SDK, port 7860) and Render (uses $PORT).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

# rasterio's bundled GDAL needs libexpat, which the slim image lacks (it broke the soil WCS fallback and the
# COG satellite reader in production while working on dev machines).
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install -r requirements.txt
# Build-time self-test: fail the build, not production, if a native library is missing.
RUN python -c "import numpy, rasterio; from rasterio.io import MemoryFile; \
from rasterio.transform import from_origin; \
m = MemoryFile(); ds = m.open(driver='GTiff', width=2, height=2, count=1, dtype='uint8', crs='EPSG:4326', \
transform=from_origin(0, 1, 0.5, 0.5)); ds.write(numpy.ones((1, 2, 2), 'uint8')); ds.close(); print('rasterio OK')"

COPY . .
# Use the committed knowledge index; only rebuild it from the live sources if it is missing.
RUN test -s data/knowledge/index.jsonl || python scripts/ingest_knowledge.py \
    || echo "knowledge ingest failed — app still runs without RAG"

# HF Spaces runs as a non-root user and needs a writable data dir.
# /app/state holds the SQLite DB; on a VM it is a mounted volume so saved plots and the cache survive redeploys.
RUN useradd -m appuser && mkdir -p /app/state && chown -R appuser /app
USER appuser

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860} --proxy-headers --forwarded-allow-ips='*'"]
