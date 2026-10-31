"""Pre-demo health check: hits every live data source and the LLM through the real app code.
Run before every demo / deploy:   python scripts/smoke_test.py  [lat lon crop]
Exit code 0 = everything required works."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.data_sources import climate, geocode, mandi, satellite, soil, weather  # noqa: E402
from app.knowledge import rag  # noqa: E402

LAT, LON, CROP = 20.0650, 74.1300, "soybean"   # a farm near Niphad, Nashik (Maharashtra)
if len(sys.argv) >= 4:
    LAT, LON, CROP = float(sys.argv[1]), float(sys.argv[2]), sys.argv[3]

results = []


def check(name, fn, required=True):
    t = time.perf_counter()
    try:
        detail = fn()
        results.append((name, "OK", detail, time.perf_counter() - t, required))
    except Exception as e:
        results.append((name, "FAIL", f"{type(e).__name__}: {e}"[:160], time.perf_counter() - t, required))


def _geo():
    g = geocode.reverse(LAT, LON)
    return f"{g['village']}, {g['district']}, {g['state']}"


def _weather():
    w = weather.forecast(LAT, LON)
    return f"{w['provider']}: {len(w['days'])} days, soil moisture {'yes' if w['soil'] else 'NO'}"


def _sat():
    s = satellite.ndvi_report(LAT, LON)
    return f"{s['provider']}: NDVI {s['latest_ndvi']} on {s['latest_date']} ({len(s['series'])} obs)"


def _rain():
    r = climate.rainfall_anomaly(LAT, LON)
    return f"{r['rain_mm']} mm vs {r['normal_mm']} mm ({r['imd_category']})"


def _soil():
    s = soil.soil_profile(LAT, LON)
    return f"{s['texture']}, pH {s['ph']}"


def _mandi():
    g = geocode.reverse(LAT, LON)
    from app.knowledge.crops import get_crop
    m = mandi.market_summary(g["state"], g["district"], get_crop(CROP)["agmarknet"], LAT, LON)
    key = "sample key (10 rows!)" if settings.using_sample_mandi_key else "own key"
    return f"{m['commodity']} median Rs {m['state_median_modal']} on {m['arrival_date']}, {m['markets_reporting']} markets, {key}"


def _llm():
    from app.agent.llm import pool
    if not pool.configured:
        raise RuntimeError("GROQ_API_KEYS not set")
    msg, model = pool.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=300)
    if not (msg.content or "").strip():
        raise RuntimeError(f"{model} returned an empty reply (reasoning used the whole token budget?)")
    return f"{model}: {msg.content!r}"


def _models():
    from groq import Groq
    if not settings.groq_keys:
        raise RuntimeError("GROQ_API_KEYS not set")
    available = {m.id for m in Groq(api_key=settings.groq_keys[0]).models.list().data}
    wanted = settings.groq_model_chain + [settings.groq_stt_model]
    missing = [m for m in wanted if m not in available]
    if missing:
        raise RuntimeError(f"not on Groq any more: {missing} — update GROQ_MODELS in .env")
    return f"all {len(wanted)} configured models available"


def _rag():
    s = rag.stats()
    if not s["chunks"]:
        raise RuntimeError("index empty — run scripts/ingest_knowledge.py")
    return f"{s['chunks']} chunks from {s['documents']} documents"


check("Nominatim (geocode)", _geo)
check("Weather forecast", _weather)
check("Sentinel-2 NDVI", _sat)
check("Rainfall anomaly", _rain)
check("SoilGrids", _soil, required=False)
check("Mandi prices", _mandi)
check("Groq models", _models)
check("Groq chat", _llm)
check("Knowledge index", _rag)

width = max(len(r[0]) for r in results)
for name, status, detail, secs, req in results:
    flag = status if status == "OK" else (status if req else "WARN")
    print(f"{flag:5} {name:<{width}}  {secs:5.1f}s  {detail}")
failed = [r for r in results if r[1] != "OK" and r[4]]
print("\nREADY FOR DEMO" if not failed else f"\n{len(failed)} required check(s) failed")
sys.exit(1 if failed else 0)
