# Works on Hugging Face Spaces (Docker SDK, port 7860) and Render (uses $PORT).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
# Use the committed knowledge index; only rebuild it from the live sources if it is missing.
RUN test -s data/knowledge/index.jsonl || python scripts/ingest_knowledge.py \
    || echo "knowledge ingest failed — app still runs without RAG"

# HF Spaces runs as a non-root user and needs a writable data dir.
RUN useradd -m appuser && chown -R appuser /app
USER appuser

EXPOSE 7860
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-7860}"]
