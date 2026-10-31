"""Retrieval over real agronomy documents (FAO, ICAR, state agri-universities, IMD guidance).

Index: data/knowledge/index.jsonl, built by scripts/ingest_knowledge.py from data/knowledge_sources.yaml
(URLs) and any PDFs dropped in data/knowledge/pdfs/. Retrieval: BM25 (lexical) with crop-name boosting.
BM25 needs no embedding model, so it runs on free hosting with no GPU and no extra API.
Every hit carries its document title + URL so answers can cite it.
"""
import json
import re
from functools import lru_cache

from rank_bm25 import BM25Okapi

from app.config import DATA_DIR

INDEX = DATA_DIR / "knowledge" / "index.jsonl"
STOP = set("the a an of and or to in on for with by is are be as at from that this it its was were can may "
           "should into than then such these those which when where how what".split())


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", text.lower()) if t not in STOP and len(t) > 1]


@lru_cache
def _load():
    if not INDEX.exists():
        return [], None
    chunks = [json.loads(line) for line in INDEX.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not chunks:
        return [], None
    return chunks, BM25Okapi([tokenize(c["title"] + " " + c["text"]) for c in chunks])


def reload():
    _load.cache_clear()


def stats() -> dict:
    chunks, _ = _load()
    return {"chunks": len(chunks), "documents": len({c["url"] for c in chunks})}


def search(query: str, crop: str | None = None, k: int = 3) -> list[dict]:
    chunks, bm25 = _load()
    if not bm25:
        return []
    q = tokenize(query + (f" {crop}" if crop else ""))
    scores = bm25.get_scores(q)
    if crop:   # prefer documents about this crop
        for i, c in enumerate(chunks):
            if crop.lower() in (c["title"] + " " + c.get("crop", "")).lower():
                scores[i] *= 1.3
    top = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)[:k]
    return [{"title": chunks[i]["title"], "url": chunks[i]["url"], "text": chunks[i]["text"],
             "score": round(float(scores[i]), 2)} for i in top if scores[i] > 0]
