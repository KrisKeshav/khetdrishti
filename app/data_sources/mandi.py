"""Mandi (APMC market) prices from data.gov.in / Agmarknet.

- Live feed:  "Current Daily Price of Various Commodities from Various Markets (Mandi)" (updated daily).
- History:    "Variety-wise Daily Market Prices Data of Commodity" (queried by arrival date).
Every record fetched is stored in `mandi_snapshots`, so our own trend history grows from real data.
All derived figures (state median, 7/30-day change, nearest markets) are computed here, not by the LLM.
"""
import difflib
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from app.config import settings
from app.data_sources import geocode
from app.db import save_mandi_rows
from app.fetch import DataUnavailable, fetch_json

LIVE_URL = "https://api.data.gov.in/resource/9ef84268-d588-465a-a308-a864a43d0070"
HIST_URL = "https://api.data.gov.in/resource/35985678-0d79-46b4-9ed6-6f13308a1d24"
SOURCE_LIVE = "data.gov.in — Agmarknet current daily mandi prices"
SOURCE_HIST = "data.gov.in — Agmarknet variety-wise daily prices (history)"
MIN_TREND_MARKETS = 3


def _limit() -> int:
    return 10 if settings.using_sample_mandi_key else 500


def _to_iso(d: str) -> str:
    return datetime.strptime(d, "%d/%m/%Y").date().isoformat()


def _norm(rec: dict) -> dict:
    """Both datasets -> one schema (the history dataset uses Capitalised keys and string prices)."""
    g = lambda *ks: next((rec[k] for k in ks if k in rec and rec[k] not in (None, "")), None)  # noqa: E731
    return {
        "arrival_date": _to_iso(g("arrival_date", "Arrival_Date")),
        "state": (g("state", "State") or "").strip(),
        "district": (g("district", "District") or "").strip(),
        "market": (g("market", "Market") or "").strip(),
        "commodity": (g("commodity", "Commodity") or "").strip(),
        "variety": (g("variety", "Variety") or "").strip(),
        "grade": (g("grade", "Grade") or "").strip(),
        "min_price": float(g("min_price", "Min_Price") or 0),
        "max_price": float(g("max_price", "Max_Price") or 0),
        "modal_price": float(g("modal_price", "Modal_Price") or 0),
    }


def _validate(p):
    if p.get("status") == "error" or "records" not in p:
        raise ValueError(p.get("message", "no records"))


def live_prices(state: str, commodity: str) -> tuple[list[dict], dict]:
    payload, prov = fetch_json(SOURCE_LIVE, LIVE_URL, params={
        "api-key": settings.data_gov_key, "format": "json", "limit": _limit(),
        "filters[state]": state, "filters[commodity]": commodity}, ttl=timedelta(hours=2), validate=_validate,
        retries=1, timeout=25)
    rows = [_norm(r) for r in payload["records"]]
    save_mandi_rows(rows)
    p = prov.to_dict()
    if settings.using_sample_mandi_key:
        p["note"] = f"public sample key: only {len(rows)} of {payload.get('total')} records returned"
    return rows, p


def history_on(state: str, commodity: str, day: date) -> tuple[list[dict], dict]:
    payload, prov = fetch_json(SOURCE_HIST, HIST_URL, params={
        "api-key": settings.data_gov_key, "format": "json", "limit": _limit(),
        "filters[State]": state, "filters[Commodity]": commodity,
        "filters[Arrival_Date]": day.strftime("%d/%m/%Y")}, ttl=timedelta(days=3), timeout=30, validate=_validate,
        retries=1)
    rows = [_norm(r) for r in payload["records"]]
    save_mandi_rows(rows)
    return rows, prov.to_dict()


def _median_modal(rows: list[dict]) -> float | None:
    vals = [r["modal_price"] for r in rows if r["modal_price"] > 0]
    return round(statistics.median(vals)) if vals else None


def _haversine_km(a, b) -> float:
    from math import asin, cos, radians, sin, sqrt
    lat1, lon1, lat2, lon2 = map(radians, (a[0], a[1], b[0], b[1]))
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 6371 * 2 * asin(sqrt(h))


def market_summary(state: str, district: str, commodity: str, lat: float, lon: float,
                   locate_markets: int = 4) -> dict:
    """Latest prices for `commodity` in `state`, the plot's own district first, nearest markets by
    distance, and the state-median price trend over 7 and 30 days."""
    rows, prov_live = live_prices(state, commodity)
    sources = [prov_live]
    if not rows:
        # Mandis report through the day, so early mornings, Sundays and holidays often have no arrivals yet.
        # Walk back through the official history dataset to the latest day that has real reports.
        started = time.monotonic()
        for back in range(1, 6):
            day = date.today() - timedelta(days=back)
            try:
                rows, h_prov = history_on(state, commodity, day)
            except DataUnavailable:
                if time.monotonic() - started > 30:
                    break   # provider is struggling; give up rather than hammer it
                continue
            if rows:
                h_prov["note"] = (f"no {commodity} arrivals in today's live feed yet — showing the latest reported "
                                  f"day ({day.isoformat()})")
                sources.append(h_prov)
                break
    if not rows:
        raise DataUnavailable(SOURCE_LIVE, f"no {commodity} arrivals reported in {state} in the last 5 days")

    latest_day = max(r["arrival_date"] for r in rows)
    today_rows = [r for r in rows if r["arrival_date"] == latest_day]

    # District names differ between OSM and Agmarknet ("Nashik"/"Nasik"), so match fuzzily.
    districts = sorted({r["district"] for r in today_rows})
    match = difflib.get_close_matches(district, districts, n=1, cutoff=0.6) if district else []
    home = [r for r in today_rows if match and r["district"] == match[0]]

    # Nearest markets: geocode a few market names (cached for 30 days; Nominatim allows 1 req/s).
    candidates = home + [r for r in today_rows if r not in home]
    located = []
    for r in candidates[:locate_markets]:
        pos = geocode.locate_market(r["market"], r["district"], r["state"])
        if pos:
            located.append({**r, "distance_km": round(_haversine_km((lat, lon), pos))})
    located.sort(key=lambda r: r["distance_km"])

    # Trend vs 7 and 30 days ago, from the official history dataset. Compared like-for-like: only markets that
    # reported on BOTH days count, because the set of reporting markets changes daily (a morning feed may list
    # 2 markets vs 10 last week, and comparing those medians would show a fake crash or spike).
    ref_days = {"7d": date.fromisoformat(latest_day) - timedelta(days=7),
                "30d": date.fromisoformat(latest_day) - timedelta(days=30)}
    today_by_market = _modal_by_market(today_rows)
    trend, trend_notes = {}, []
    with ThreadPoolExecutor(max_workers=2) as ex:
        futs = {k: ex.submit(history_on, state, commodity, d) for k, d in ref_days.items()}
        for k, f in futs.items():
            try:
                h_rows, h_prov = f.result()
            except DataUnavailable:
                trend_notes.append(f"{k}: history unavailable")
                continue
            sources.append(h_prov)
            ref_by_market = _modal_by_market(h_rows)
            common = sorted(set(today_by_market) & set(ref_by_market))
            if len(common) < MIN_TREND_MARKETS:
                trend_notes.append(f"{k}: only {len(common)} market(s) reported on both days — not enough to compare")
                continue
            now_med = statistics.median(today_by_market[m] for m in common)
            ref_med = statistics.median(ref_by_market[m] for m in common)
            trend[k] = {"date": ref_days[k].isoformat(), "markets_compared": len(common),
                        "median_then": round(ref_med), "median_now": round(now_med),
                        "change_pct": round((now_med - ref_med) / ref_med * 100, 1),
                        "change_rs": round(now_med - ref_med)}

    today_median = _median_modal(today_rows)

    best = max(today_rows, key=lambda r: r["modal_price"])
    return {
        "commodity": commodity,
        "state": state,
        "arrival_date": latest_day,
        "unit": "Rs per quintal",
        "markets_reporting": len(today_rows),
        "state_median_modal": today_median,
        "home_district": match[0] if match else None,
        "home_district_markets": [_slim(r) for r in home][:5],
        "nearest_markets": [_slim(r) | {"distance_km": r["distance_km"]} for r in located],
        "highest_modal_market": _slim(best),
        "trend": trend,
        "trend_notes": trend_notes,
        "sources": sources,
    }


def _modal_by_market(rows: list[dict]) -> dict[str, float]:
    """One modal price per market (median across varieties/grades)."""
    by: dict[str, list[float]] = {}
    for r in rows:
        if r["modal_price"] > 0:
            by.setdefault(r["market"].strip().lower(), []).append(r["modal_price"])
    return {m: statistics.median(v) for m, v in by.items()}


def _slim(r: dict) -> dict:
    return {"market": r["market"], "district": r["district"], "variety": r["variety"],
            "min": round(r["min_price"]), "max": round(r["max_price"]), "modal": round(r["modal_price"]),
            "date": r["arrival_date"]}
