"""Tools the agent can call. Every tool is scoped to the plot the farmer selected (the LLM never supplies
coordinates, so it cannot hallucinate a location) and returns compact JSON with its sources.

Output shape: {"ok": true, "data": {...}, "sources": ["Open-Meteo Forecast API (live, 10:32 IST)", ...]}
          or  {"ok": false, "error": "...", "instruction": "Tell the farmer this data is unavailable. Do not guess."}
"""
import json
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from app.data_sources import climate, mandi, satellite, soil, weather
from app.engine.agro import soil_water_constants, water_balance
from app.engine.report import apply_stage, build_report
from app.fetch import DataUnavailable
from app.knowledge import rag
from app.knowledge.crops import crop_stage, get_crop

IST = timezone(timedelta(hours=5, minutes=30))

WATER_ADVICE = {
    "irrigate_now": "Irrigate now (soil water is below the crop's stress point and little rain is due)",
    "irrigate_on_due_date": "Irrigation becomes due on irrigation_due_date at the forecast rate of crop water use",
    "wait_rain_expected": "Hold irrigation: rain forecast in the next 3 days covers at least half of the deficit",
    "no_irrigation_needed": "No irrigation needed in the next 7 days",
    "soil_moisture_unavailable": "Soil moisture data unavailable; only forecast rain vs crop water use is known",
    "past_season": "Crop is past its typical season length: it should be harvested, irrigation is not relevant",
}


@dataclass
class PlotContext:
    lat: float
    lon: float
    crop_id: str
    sowing_date: str | None
    state: str = ""
    district: str = ""
    village: str = ""
    evidence: list = field(default_factory=list)       # raw tool data, for the verifier
    knowledge: list = field(default_factory=list)      # retrieved document texts, for the dosage guardrail
    sources: list = field(default_factory=list)        # provenance dicts, for the UI

    @property
    def crop(self) -> dict:
        return get_crop(self.crop_id)


def _src_label(p: dict) -> str:
    try:
        t = datetime.fromisoformat(p["fetched_at"]).astimezone(IST).strftime("%d %b %H:%M IST")
    except (KeyError, ValueError):
        t = "?"
    return f"{p['source']} ({p['mode']}, {t})"


def _ok(ctx: PlotContext, data: dict, sources: list[dict]) -> dict:
    ctx.evidence.append(data)
    ctx.sources.extend(sources)
    return {"ok": True, "data": data, "sources": [_src_label(s) for s in sources]}


# ------------------------------------------------------------------ implementations

def get_weather_forecast(ctx: PlotContext, days: int = 7) -> dict:
    days = max(1, min(int(days), 16))
    w = weather.forecast(ctx.lat, ctx.lon)
    rows = [{k: d[k] for k in ("date", "tmax", "tmin", "rain_mm", "rain_prob", "wind_kmh", "rh_mean")}
            for d in w["days"][:days]]
    rain_days = [d for d in rows if (d["rain_mm"] or 0) >= 2.5]
    data = {
        "provider": w["provider"], "days": rows,
        "summary": {
            "total_rain_mm": round(sum(d["rain_mm"] or 0 for d in rows), 1),
            "rainy_days_ge_2_5mm": len(rain_days),
            "first_rainy_day": rain_days[0]["date"] if rain_days else None,
            "hottest_day": max(rows, key=lambda d: d["tmax"])["date"],
            "max_temp_c": max(d["tmax"] for d in rows),
            "min_temp_c": min(d["tmin"] for d in rows),
        },
    }
    return _ok(ctx, data, w["sources"])


def get_crop_stage(ctx: PlotContext) -> dict:
    st = crop_stage(ctx.crop_id, ctx.sowing_date)
    data = {k: v for k, v in st.items() if k != "kc_by_date"}
    data["crop"] = ctx.crop["names"]["en"]
    return _ok(ctx, data, [{"source": f"FAO-56 crop calendar ({st.get('stage_source', 'Table 11')})",
                            "fetched_at": datetime.now(timezone.utc).isoformat(), "mode": "reference", "url": ""}])


def get_water_balance(ctx: PlotContext) -> dict:
    w = weather.forecast(ctx.lat, ctx.lon)
    sources = list(w["sources"])
    try:
        s = soil.soil_profile(ctx.lat, ctx.lon)
        consts, sources = s["water"], sources + s["sources"]
        assumption = None
    except DataUnavailable:
        consts, assumption = soil_water_constants("loam"), "soil texture unavailable; loam assumed"
    st = crop_stage(ctx.crop_id, ctx.sowing_date)
    theta = w["soil"]["root_zone_0_27cm"] if w.get("soil") else None
    wb = water_balance(w["days"], st["kc_by_date"], theta, consts["fc"], consts["wp"], ctx.crop["p"]["value"])
    wb["timeline"] = wb["timeline"][:7]
    wb["soil_texture"] = consts["texture"]
    wb["crop_coefficient_today"] = st["kc_today"]
    if assumption:
        wb["assumption"] = assumption
    apply_stage(wb, st)
    # Give the model plain words, not internal codes it might repeat to the farmer ("past_season").
    wb["recommendation"] = WATER_ADVICE.get(wb["recommendation"], wb["recommendation"])
    if ctx.crop.get("note"):
        wb["crop_note"] = ctx.crop["note"]
    return _ok(ctx, wb, sources)


def get_crop_health_satellite(ctx: PlotContext) -> dict:
    r = satellite.ndvi_report(ctx.lat, ctx.lon)
    data = {k: v for k, v in r.items() if k not in ("series", "last_year_series", "sources")}
    data["recent_observations"] = [{"date": s["date"], "ndvi": s["ndvi"]} for s in r["series"][-6:]]
    # Same rule as the risk engine: only attribute greenness to this crop if it has been in the field long enough.
    if not ctx.sowing_date:
        data["interpretation"] = ("Sowing date unknown: this greenness may belong to a previous crop or a harvested "
                                  "field. Do not judge the current crop's health from it; ask for the sowing date.")
    else:
        days = (date.fromisoformat(r["latest_date"]) - date.fromisoformat(ctx.sowing_date)).days
        if days < 30:
            data["interpretation"] = (f"Latest image is only {max(days, 0)} days after sowing (or before it): it mostly "
                                      "shows soil / the previous crop, not this crop's health yet.")
    return _ok(ctx, data, r["sources"])


def get_rainfall_anomaly(ctx: PlotContext, window_days: int = 30) -> dict:
    window_days = window_days if window_days in (15, 30, 60, 90) else 30
    r = climate.rainfall_anomaly(ctx.lat, ctx.lon, window_days)
    data = {k: v for k, v in r.items() if k not in ("sources",)}
    return _ok(ctx, data, r["sources"])


def get_mandi_prices(ctx: PlotContext, crop: str | None = None) -> dict:
    target = get_crop(crop) if crop else ctx.crop
    if target is None:
        return {"ok": False, "error": f"unknown crop '{crop}'"}
    if not ctx.state:
        return {"ok": False, "error": "state not known for this plot",
                "instruction": "Tell the farmer market data could not be matched to their state."}
    m = mandi.market_summary(ctx.state, ctx.district, target["agmarknet"], ctx.lat, ctx.lon)
    data = {k: v for k, v in m.items() if k != "sources"}
    data["home_district_markets"] = data["home_district_markets"][:3]
    return _ok(ctx, data, m["sources"])


def get_soil_profile(ctx: PlotContext) -> dict:
    s = soil.soil_profile(ctx.lat, ctx.lon)
    return _ok(ctx, {k: v for k, v in s.items() if k != "sources"}, s["sources"])


def get_risk_alerts(ctx: PlotContext) -> dict:
    rep = build_report(ctx.lat, ctx.lon, ctx.crop_id, ctx.sowing_date)
    risks = [{k: r[k] for k in ("id", "severity", "title", "detail", "action")} for r in rep["risks"]]
    data = {"risks": risks, "checks_passed": _checks_passed(rep), "unavailable_sources": rep["errors"]}
    ctx.evidence.append([r["evidence"] for r in rep["risks"]])
    return _ok(ctx, data, rep["sources"])


def _checks_passed(rep: dict) -> list[str]:
    """The rules that ran and did NOT fire, with their numbers. Without these the model sees only silence
    and may guess 'yes, risky'; with them it can say 'no — 30 °C is below the 34 °C threshold'."""
    fired = {r["id"] for r in rep["risks"]}
    w = rep.get("weather")
    if not w:
        return []
    from app.knowledge.crops import imd
    t, crop = imd(), rep["crop_profile"]
    week = w["days"][:7]
    out = []
    thr = crop.get("heat_stress_c", {}).get("value")
    hot = max(week, key=lambda d: d["tmax"])
    if thr and "heat_stress" not in fired:
        out.append(f"heat: highest max temperature in 7 days is {hot['tmax']} °C on {hot['date']}, below the "
                   f"{thr} °C heat-stress threshold for {crop['names']['en']}")
    cold = min(week, key=lambda d: d["tmin"])
    if "cold_wave" not in fired:
        out.append(f"cold: lowest min temperature is {cold['tmin']} °C, above the IMD cold-wave level of "
                   f"{t['cold_wave_tmin_c']} °C")
    wet = max(week, key=lambda d: d["rain_mm"] or 0)
    if "heavy_rain" not in fired:
        out.append(f"rain: wettest day {wet['date']} has {wet['rain_mm']} mm, below the IMD heavy-rain level of "
                   f"{t['rain_intensity_mm']['heavy']} mm")
    return out


def search_agronomy_knowledge(ctx: PlotContext, query: str) -> dict:
    hits = rag.search(query, crop=ctx.crop["names"]["en"])
    if not hits:
        return {"ok": False, "error": "no matching document in the knowledge base",
                "instruction": "Do not give chemical names or doses. Refer the farmer to the local Krishi Vigyan "
                               "Kendra or Kisan Call Centre 1800-180-1551."}
    ctx.knowledge.extend(h["text"] for h in hits)
    data = {"passages": [{"title": h["title"], "url": h["url"], "text": h["text"][:700]} for h in hits]}
    return _ok(ctx, data, [{"source": f"Document: {h['title']}", "url": h["url"], "mode": "reference",
                            "fetched_at": datetime.now(timezone.utc).isoformat()} for h in hits])


IMPLS = {
    "get_weather_forecast": get_weather_forecast,
    "get_crop_stage": get_crop_stage,
    "get_water_balance": get_water_balance,
    "get_crop_health_satellite": get_crop_health_satellite,
    "get_rainfall_anomaly": get_rainfall_anomaly,
    "get_mandi_prices": get_mandi_prices,
    "get_soil_profile": get_soil_profile,
    "get_risk_alerts": get_risk_alerts,
    "search_agronomy_knowledge": search_agronomy_knowledge,
}


def _fn(name, description, properties=None, required=None):
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []}}}


SCHEMAS = [
    _fn("get_weather_forecast", "Daily forecast for the farmer's plot: max/min temperature, rain (mm), rain "
        "probability, wind, humidity, plus pre-computed totals.",
        {"days": {"type": "integer", "description": "1-16 days ahead (default 7)"}}),
    _fn("get_crop_stage", "Current growth stage of the crop from the sowing date (FAO-56 crop calendar)."),
    _fn("get_water_balance", "Irrigation advice: current root-zone soil moisture, crop water use (FAO-56 Kc×ET0), "
        "effective rain, and whether/when to irrigate and how many mm."),
    _fn("get_crop_health_satellite", "Sentinel-2 satellite NDVI (crop greenness) for this exact plot: latest value, "
        "30-day change and comparison with the same time last year."),
    _fn("get_rainfall_anomaly", "Rain in the recent window vs the 10-year normal for this location, with the "
        "IMD category (deficient/normal/excess).",
        {"window_days": {"type": "integer", "enum": [15, 30, 60, 90]}}),
    _fn("get_mandi_prices", "Today's mandi (APMC) prices for the crop in the farmer's state: home-district and "
        "nearest markets with distance, best price, and 7/30-day price trend.",
        {"crop": {"type": "string", "description": "optional other crop id, e.g. 'onion'"}}),
    _fn("get_soil_profile", "Soil texture, pH and organic carbon for the plot (SoilGrids, 0-30 cm)."),
    _fn("get_risk_alerts", "Run the full risk engine (heat, cold, heavy rain, dry spell, irrigation, spraying "
        "window, disease weather, crop vigour, rainfall anomaly, price moves). Use for 'any risk?' questions."),
    _fn("search_agronomy_knowledge", "Search agronomy documents (FAO, ICAR, agri-universities) for practices, "
        "pests, diseases, fertiliser. Required before mentioning any chemical or dose.",
        {"query": {"type": "string"}}, ["query"]),
]


TOOL_TIMEOUT_S = 35
_tool_pool = ThreadPoolExecutor(max_workers=8)


def execute(ctx: PlotContext, name: str, args_json: str) -> dict:
    """Run one tool call with a time limit. A provider that hangs becomes a 'timed out' error the agent must
    acknowledge; the call keeps running in the background and fills the cache for next time."""
    fut = _tool_pool.submit(_execute, ctx, name, args_json)
    try:
        return fut.result(timeout=TOOL_TIMEOUT_S)
    except FutureTimeout:
        return {"ok": False, "error": f"{name} timed out after {TOOL_TIMEOUT_S} s (data provider is slow)",
                "instruction": "Tell the farmer this data is temporarily unavailable. Do not guess values."}


def _execute(ctx: PlotContext, name: str, args_json: str) -> dict:
    """Run one tool call safely. Bad arguments or failing sources become structured errors the model can
    read and react to (self-correction), never exceptions that crash the run."""
    fn = IMPLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool '{name}'. Available: {', '.join(IMPLS)}"}
    try:
        args = json.loads(args_json or "{}") or {}
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
    except (json.JSONDecodeError, ValueError) as e:
        return {"ok": False, "error": f"invalid JSON arguments: {e}"}
    try:
        return fn(ctx, **args)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {name}: {e}"}
    except DataUnavailable as e:
        return {"ok": False, "error": f"{e.source} unavailable ({e.reason})",
                "instruction": "Tell the farmer this specific data is unavailable right now. Do not guess values."}
    except Exception as e:  # never let one tool crash the agent
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:200],
                "instruction": "Tell the farmer this data is unavailable. Do not guess values."}
