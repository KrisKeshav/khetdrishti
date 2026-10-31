"""FastAPI app: JSON API + Server-Sent-Events agent stream + static frontend.

Run locally:  uvicorn app.main:app --reload --port 8000
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db
from app.agent import orchestrator
from app.agent.llm import pool
from app.config import ROOT, settings
from app.data_sources import geocode
from app.engine.report import BadRequest, build_report
from app.fetch import OUTAGE_TARGETS, DataUnavailable, active_outages, clear_outages, simulate_outage
from app.knowledge import rag
from app.knowledge.crops import crop_list
from app.monitor import run_monitor
from app.voice import stt, tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("khetdrishti")
STATIC = ROOT / "static"
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


def prewarm() -> None:
    """Fill the cache with fresh real data for every saved plot, so the first demo request is instant and
    a provider hiccup right after startup still has labelled real data to fall back on."""
    for p in db.list_plots():
        try:
            build_report(p["lat"], p["lon"], p["crop"], p["sowing_date"])
            log.info("pre-warmed plot %s (%s)", p["id"], p["name"])
        except Exception:
            log.exception("pre-warm failed for plot %s", p["id"])


@asynccontextmanager
async def lifespan(_app: FastAPI):
    scheduler.add_job(run_monitor, "cron", hour=settings.monitor_hour_ist, minute=0, id="daily_monitor",
                      replace_existing=True, misfire_grace_time=3600)
    # Re-warm every 45 min so cached data never gets old during a judging session.
    scheduler.add_job(prewarm, "interval", minutes=45, id="prewarm", replace_existing=True,
                      next_run_time=datetime.now(timezone.utc))
    scheduler.start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(title="KhetDrishti", version="1.0", lifespan=lifespan)


@app.exception_handler(Exception)
async def unhandled_error(_request, exc: Exception):
    # Last line of defence: log the bug, give the UI a clean message instead of a stack trace.
    log.exception("unhandled error: %s", exc)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side. "
                                                            "Please try again; other tabs keep working."})


# ------------------------------------------------------------------ models

class PlotIn(BaseModel):
    lat: float = Field(ge=6, le=38)        # India's bounding box; finer check is the reverse geocoder
    lon: float = Field(ge=68, le=98)
    crop: str
    sowing_date: str | None = None


class SavePlotIn(PlotIn):
    name: str = Field(default="", max_length=120)
    language: str = "hi"
    telegram_chat_id: str | None = None


class AskIn(BaseModel):
    question: str = Field(max_length=1000)
    plot: PlotIn
    language: str = "en"
    history: list[dict] = []


class TTSIn(BaseModel):
    text: str = Field(max_length=3000)
    language: str = "en"


def _check_sowing(s: str | None):
    if not s:
        return None
    try:
        d = date.fromisoformat(s)
    except ValueError:
        raise HTTPException(422, "Sowing date must be YYYY-MM-DD.")
    if d > date.today():
        raise HTTPException(422, "Sowing date cannot be in the future.")
    if (date.today() - d).days > 500:
        raise HTTPException(422, "Sowing date is more than 500 days ago.")
    return s


# ------------------------------------------------------------------ API

@app.get("/api/crops")
def crops():
    return crop_list()


@app.get("/api/geocode")
def geocode_search(q: str):
    try:
        return geocode.search(q)
    except DataUnavailable as e:
        raise HTTPException(503, f"Place search unavailable: {e.reason}")


@app.post("/api/report")
def report(p: PlotIn):
    try:
        rep = build_report(p.lat, p.lon, p.crop, _check_sowing(p.sowing_date))
    except BadRequest as e:
        raise HTTPException(422, str(e))
    rep.pop("crop_profile", None)
    return JSONResponse(json.loads(json.dumps(rep, default=str)))


@app.post("/api/ask")
def ask(body: AskIn):
    _check_sowing(body.plot.sowing_date)

    def stream():
        try:
            for event in orchestrator.run(body.question, body.plot.model_dump(), body.language, body.history):
                yield f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n"
        except Exception as e:  # last line of defence: the stream always ends cleanly
            log.exception("agent crashed")
            yield f"data: {json.dumps({'type': 'error', 'message': f'Internal error: {type(e).__name__}'})}\n\n"
        yield 'data: {"type": "done"}\n\n'

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.post("/api/transcribe")
async def transcribe(audio: UploadFile = File(...), language: str = Form("auto")):
    data = await audio.read()
    if len(data) > 15 * 1024 * 1024:
        raise HTTPException(413, "Recording too long (max ~2 minutes).")
    try:
        return stt.transcribe(data, audio.filename or "speech.webm", language)
    except stt.STTError as e:
        raise HTTPException(422, str(e))


@app.post("/api/tts")
def text_to_speech(body: TTSIn):
    out = tts.synthesize(body.text, body.language)
    if not out:
        raise HTTPException(503, "Server speech unavailable; browser voice will be used.")
    audio, mime, engine = out
    return Response(content=audio, media_type=mime, headers={"X-TTS-Engine": engine})


@app.get("/api/plots")
def plots():
    return db.list_plots()


@app.post("/api/plots")
def save_plot(p: SavePlotIn):
    _check_sowing(p.sowing_date)
    try:
        loc = geocode.reverse(p.lat, p.lon)
    except geocode.OutsideIndia as e:
        raise HTTPException(422, str(e))
    except DataUnavailable:
        loc = {}
    name = p.name or ", ".join(x for x in (loc.get("village"), loc.get("district")) if x) or "My plot"
    pid = db.add_plot(name=name, lat=p.lat, lon=p.lon, crop=p.crop, sowing_date=p.sowing_date,
                      language=p.language, telegram_chat_id=p.telegram_chat_id)
    return db.get_plot(pid)


@app.delete("/api/plots/{plot_id}")
def remove_plot(plot_id: int):
    db.delete_plot(plot_id)
    return {"ok": True}


@app.post("/api/monitor/run")
def monitor_now():
    return run_monitor()


@app.get("/api/alerts")
def alerts():
    return JSONResponse(json.loads(json.dumps(db.recent_alerts(), default=str)))


class OutageIn(BaseModel):
    targets: list[str]
    minutes: float = Field(default=5, gt=0, le=30)


@app.get("/api/resilience")
def resilience_state():
    return {"targets": OUTAGE_TARGETS, "active": active_outages()}


@app.post("/api/resilience/outage")
def resilience_outage(body: OutageIn):
    unknown = [t for t in body.targets if t not in OUTAGE_TARGETS]
    if unknown:
        raise HTTPException(422, f"Unknown targets: {unknown}")
    log.warning("SIMULATED OUTAGE started for %s (%s min)", body.targets, body.minutes)
    return {"active": simulate_outage(body.targets, body.minutes)}


@app.delete("/api/resilience/outage")
def resilience_restore():
    clear_outages()
    return {"active": {}}


# Lightweight live status of every upstream source, shown as dots in the header.
STATUS_CHECKS = {
    "Open-Meteo": "https://api.open-meteo.com/v1/forecast?latitude=20&longitude=78&daily=temperature_2m_max&forecast_days=1",
    "Sentinel-2 (Planetary Computer)": "https://planetarycomputer.microsoft.com/api/stac/v1/collections/sentinel-2-l2a",
    # Ping the dataset we actually use (the key is added server-side in status(), never sent to the browser).
    "data.gov.in": "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070?format=json&limit=1",
    "SoilGrids": "https://rest.isric.org/soilgrids/v2.0/properties/layers",
    "Nominatim": "https://nominatim.openstreetmap.org/status?format=json",
    "Groq": "https://api.groq.com/openai/v1/models",
}


@app.get("/api/status")
def status():
    def ping(item):
        name, url = item
        params = {"api-key": settings.data_gov_key} if name == "data.gov.in" else None
        try:
            r = httpx.get(url, params=params, timeout=10, headers={"User-Agent": settings.contact_ua})
            # Groq answers 401 without a key: reachable is what we want to show.
            return name, r.status_code < 500
        except httpx.HTTPError:
            return name, False

    with ThreadPoolExecutor(max_workers=len(STATUS_CHECKS)) as ex:
        sources = dict(ex.map(ping, STATUS_CHECKS.items()))
    return {
        "sources": sources,
        "config": {
            "llm": pool.configured,
            "llm_models": settings.groq_model_chain,
            "mandi_key": not settings.using_sample_mandi_key,
            "copernicus": bool(settings.cdse_client_id),
            "sarvam_tts": bool(settings.sarvam_api_key),
            "telegram": bool(settings.telegram_bot_token),
        },
        "knowledge": rag.stats(),
        "simulated_outages": active_outages(),
        "next_monitor_run": str(scheduler.get_job("daily_monitor").next_run_time) if scheduler.running else None,
    }


# ------------------------------------------------------------------ frontend

app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(Path(STATIC) / "index.html")
