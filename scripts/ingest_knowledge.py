"""Build the agronomy knowledge index (data/knowledge/index.jsonl).

Sources:
  1. URLs listed in data/knowledge_sources.yaml (HTML or PDF) — downloaded live.
  2. Any PDF placed in data/knowledge/pdfs/ (e.g. ICAR package-of-practices booklets).
Usage:  python scripts/ingest_knowledge.py
"""
import io
import json
import re
import sys
from pathlib import Path

import httpx
import yaml
from bs4 import BeautifulSoup
from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
KDIR = ROOT / "data" / "knowledge"
OUT = KDIR / "index.jsonl"
CHUNK_CHARS, OVERLAP = 900, 150


def clean(text: str) -> str:
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return "\n\n".join(p.extract_text() or "" for p in reader.pages)


def html_text(data: bytes) -> tuple[str, str]:
    soup = BeautifulSoup(data, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    return title, soup.get_text("\n")


def pieces(text: str) -> list[str]:
    """Line-level pieces with site navigation removed; over-long lines split into sentences."""
    out = []
    for line in text.split("\n"):
        line = line.strip()
        # Menus / breadcrumbs: short lines or lines made of '|'-separated links.
        if len(line) < 30 or line.count("|") >= 3:
            continue
        if len(line) <= CHUNK_CHARS:
            out.append(line)
        else:
            out += [s.strip() for s in re.split(r"(?<=[.;:])\s+", line) if s.strip()]
    return out


def chunk(text: str) -> list[str]:
    chunks, cur = [], ""
    for p in pieces(text):
        if len(cur) + len(p) > CHUNK_CHARS and cur:
            chunks.append(cur)
            cur = cur[-OVERLAP:] + " " + p
        else:
            cur = (cur + "\n" + p).strip()
    if len(cur) > 80:
        chunks.append(cur)
    return chunks


def main() -> int:
    KDIR.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load((ROOT / "data" / "knowledge_sources.yaml").read_text(encoding="utf-8")) or {}
    records, failures = [], []
    client = httpx.Client(timeout=60, follow_redirects=True, headers={"User-Agent": "KhetDrishti-ingest/1.0"})
    for src in cfg.get("sources", []):
        url = src["url"]
        try:
            r = client.get(url)
            r.raise_for_status()
            if url.lower().endswith(".pdf") or "pdf" in r.headers.get("content-type", ""):
                title, text = src.get("title", url), pdf_text(r.content)
            else:
                page_title, text = html_text(r.content)
                title = src.get("title") or page_title or url
        except Exception as e:
            failures.append(f"{url}: {e}")
            continue
        parts = chunk(clean(text))
        for i, c in enumerate(parts):
            records.append({"id": f"{len(records)}", "title": title, "url": url, "crop": src.get("crop", ""),
                            "publisher": src.get("publisher", ""), "text": c})
        print(f"OK   {len(parts):4d} chunks  {title[:70]}")
    for pdf in sorted((KDIR / "pdfs").glob("*.pdf")) if (KDIR / "pdfs").exists() else []:
        parts = chunk(clean(pdf_text(pdf.read_bytes())))
        for c in parts:
            records.append({"id": f"{len(records)}", "title": pdf.stem, "url": f"local:{pdf.name}", "crop": "",
                            "publisher": "", "text": c})
        print(f"OK   {len(parts):4d} chunks  {pdf.name}")
    OUT.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records), encoding="utf-8")
    print(f"\nWrote {len(records)} chunks to {OUT}")
    for f in failures:
        print("FAIL", f)
    return 0 if records else 1


if __name__ == "__main__":
    sys.exit(main())
