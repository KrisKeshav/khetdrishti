"""Builds the full field report for one plot by fetching every source in parallel.

A failing source never fails the report: it is recorded under `errors` and the rest still renders.
"""
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timezone

from app.data_sources import climate, geocode, mandi, satellite, soil, weather
from app.engine import risk
from app.engine.agro import soil_water_constants, water_balance
from app.fetch import DataUnavailable
from app.knowledge.crops import crop_stage, get_crop


REPORT_BUDGET_S = 30


class BadRequest(Exception):
    """Input the user can fix (bad crop, pin outside India, ...)."""


def _timed(fn, *args, **kwargs):
    t0 = time.perf_counter()
    try:
        return fn(*args, **kwargs), None, int((time.perf_counter() - t0) * 1000)
    except DataUnavailable as e:
        return None, e.reason, int((time.perf_counter() - t0) * 1000)
    except Exception as e:  # unexpected bug in one source must not take the demo down
        return None, f"{type(e).__name__}: {e}"[:200], int((time.perf_counter() - t0) * 1000)


def build_report(lat: float, lon: float, crop_id: str, sowing_date: str | None = None,
                 parts: tuple[str, ...] = ("weather", "soil", "satellite", "rainfall", "market")) -> dict:
    crop = get_crop(crop_id)
    if crop is None:
        raise BadRequest(f"Unknown crop '{crop_id}'.")
    try:
        loc = geocode.reverse(lat, lon)
    except geocode.OutsideIndia as e:
        raise BadRequest(str(e))
    except DataUnavailable:
        loc = {"state": "", "district": "", "village": "", "display_name": "", "source": None}
    try:
        stage = crop_stage(crop_id, sowing_date)
    except ValueError as e:
        raise BadRequest(str(e))

    jobs = {
        "weather": (weather.forecast, lat, lon),
        "soil": (soil.soil_profile, lat, lon),
        "satellite": (satellite.ndvi_report, lat, lon),
        "rainfall": (climate.rainfall_anomaly, lat, lon),
    }
    if loc.get("state"):
        jobs["market"] = (mandi.market_summary, loc["state"], loc.get("district", ""), crop["agmarknet"], lat, lon)
    jobs = {k: v for k, v in jobs.items() if k in parts}

    results, errors, timings = {}, {}, {}
    # One slow provider must not freeze the whole report (e.g. data.gov.in hanging for minutes). Each part gets
    # until the shared deadline; late parts are reported as timed out and keep running in the background, so
    # their result lands in the cache for the next request. Not a `with` block: its exit would wait for them.
    ex = ThreadPoolExecutor(max_workers=len(jobs) or 1)
    futs = {k: ex.submit(_timed, *job) for k, job in jobs.items()}
    deadline = time.monotonic() + REPORT_BUDGET_S
    for k, f in futs.items():
        try:
            results[k], err, timings[k] = f.result(timeout=max(0.1, deadline - time.monotonic()))
        except FutureTimeout:
            results[k], err, timings[k] = None, f"timed out after {REPORT_BUDGET_S} s (provider slow) — " \
                                                "will use cached data on the next request", REPORT_BUDGET_S * 1000
        if err:
            errors[k] = err
    ex.shutdown(wait=False)
    if "market" in parts and "market" not in jobs:
        errors["market"] = "state unknown for this location (geocoder unavailable)"

    report = {
        "plot": {"lat": lat, "lon": lon, "crop": crop_id, "sowing_date": sowing_date},
        "crop_profile": crop,
        "location": loc,
        "stage": stage,
        **results,
        "errors": errors,
        "timings_ms": timings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    # Water balance needs weather + soil texture + crop stage.
    w = results.get("weather")
    if w:
        soil_res = results.get("soil")
        consts = soil_res["water"] if soil_res else soil_water_constants("loam")
        theta = w["soil"]["root_zone_0_27cm"] if w.get("soil") else None
        wb = water_balance(w["days"], stage["kc_by_date"], theta, consts["fc"], consts["wp"], crop["p"]["value"])
        wb["soil_constants"] = consts
        if not soil_res:
            wb["assumption"] = "SoilGrids unavailable — loam soil assumed for field capacity / wilting point"
        report["water"] = apply_stage(wb, stage)

    report["risks"] = risk.assess(report)
    report["sources"] = collect_sources(report)
    return report


def apply_stage(wb: dict, stage: dict) -> dict:
    """Irrigation advice only makes sense while the crop is in the field."""
    if not stage["known"]:
        wb["stage_assumption"] = stage["assumption"]
    elif stage["stage"] == "after":
        for k in ("suggested_irrigation_mm", "irrigation_due_date"):
            wb.pop(k, None)
        wb["recommendation"] = "past_season"
        wb["stage_note"] = (f"Day {stage['days_after_sowing']} after sowing is beyond the typical "
                            f"{stage['season_length_days']}-day season — the crop should be harvested. "
                            "Check the sowing date if it is still standing.")
    return wb


def collect_sources(report: dict) -> list[dict]:
    seen, out = set(), []
    if report.get("location", {}).get("source"):
        out.append(report["location"]["source"])
    for k in ("weather", "soil", "satellite", "rainfall", "market"):
        for s in (report.get(k) or {}).get("sources", []):
            key = (s["source"], s["fetched_at"])
            if key not in seen:
                seen.add(key)
                out.append(s)
    return out
