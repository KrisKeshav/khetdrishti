"""Rainfall anomaly: last N days vs the same calendar window in each of the previous 10 years.

Primary: Open-Meteo Historical Weather API (ERA5 reanalysis). Fallback: NASA POWER (MERRA-2/IMERG).
Both sources have a few days' latency, so the window ends 6 days ago; the same source is used for the
current window and the baseline so the comparison is like-for-like.
The departure is classified with IMD's categories (large excess ... large deficient).
"""
from datetime import date, timedelta

from app.fetch import DataUnavailable, fetch_json
from app.knowledge.crops import imd

ARCHIVE = "https://archive-api.open-meteo.com/v1/archive"
POWER = "https://power.larc.nasa.gov/api/temporal/daily/point"
LAG_DAYS = 6
BASELINE_YEARS = 10


def imd_category(departure_pct: float) -> str:
    t = imd()["seasonal_anomaly_pct"]
    if departure_pct <= -99.5:
        return "no rain"
    if departure_pct >= t["large_excess"]:
        return "large excess"
    if departure_pct >= t["excess"]:
        return "excess"
    if departure_pct > t["deficient"]:
        return "normal"
    if departure_pct > t["large_deficient"]:
        return "deficient"
    return "large deficient"


def rainfall_anomaly(lat: float, lon: float, window_days: int = 30, today: date | None = None) -> dict:
    today = today or date.today()
    end = today - timedelta(days=LAG_DAYS)
    start = end - timedelta(days=window_days - 1)
    first = date(start.year - BASELINE_YEARS, start.month, min(start.day, 28))
    try:
        series, prov = _open_meteo_daily(lat, lon, first, end)
    except DataUnavailable as e:
        series, prov = _power_daily(lat, lon, first, end)
        prov["note"] = f"fallback: {e}"

    def window_sum(year_offset: int) -> float | None:
        s = date(start.year - year_offset, start.month, min(start.day, 28)) if year_offset else start
        vals = [series.get((s + timedelta(days=i)).isoformat()) for i in range(window_days)]
        vals = [v for v in vals if v is not None]
        return sum(vals) if len(vals) >= window_days * 0.9 else None

    current = window_sum(0)
    if current is None:
        raise DataUnavailable(prov["source"], "incomplete data for the current window")
    per_year = []
    for k in range(1, BASELINE_YEARS + 1):
        v = window_sum(k)
        if v is not None:
            per_year.append({"year": start.year - k, "rain_mm": round(v, 1)})
    if len(per_year) < 5:
        raise DataUnavailable(prov["source"], "not enough baseline years")
    normal = sum(p["rain_mm"] for p in per_year) / len(per_year)
    # A near-zero normal (dry season) makes percentages meaningless; report absolute values instead.
    departure = None if normal < 5 else round((current - normal) / normal * 100)
    return {
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "window_days": window_days,
        "rain_mm": round(current, 1),
        "normal_mm": round(normal, 1),
        "departure_pct": departure,
        "imd_category": imd_category(departure) if departure is not None else "dry-season window (normal < 5 mm)",
        "baseline_years": len(per_year),
        "per_year": per_year,
        "sources": [prov],
    }


def _open_meteo_daily(lat, lon, first: date, end: date) -> tuple[dict, dict]:
    def validate(p):
        if p.get("error") or "daily" not in p:
            raise ValueError(p.get("reason", "no daily"))
    payload, prov = fetch_json("Open-Meteo Historical Weather API (ERA5)", ARCHIVE,
                               params={"latitude": round(lat, 3), "longitude": round(lon, 3),
                                       "start_date": first.isoformat(), "end_date": end.isoformat(),
                                       "daily": "precipitation_sum", "timezone": "Asia/Kolkata"},
                               ttl=timedelta(hours=12), timeout=40, validate=validate)
    d = payload["daily"]
    return dict(zip(d["time"], d["precipitation_sum"])), prov.to_dict()


def _power_daily(lat, lon, first: date, end: date) -> tuple[dict, dict]:
    payload, prov = fetch_json("NASA POWER (daily, AG community)", POWER,
                               params={"parameters": "PRECTOTCORR", "community": "AG",
                                       "latitude": round(lat, 3), "longitude": round(lon, 3),
                                       "start": first.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
                                       "format": "JSON"},
                               ttl=timedelta(hours=12), timeout=60)
    raw = payload["properties"]["parameter"]["PRECTOTCORR"]
    series = {f"{k[:4]}-{k[4:6]}-{k[6:]}": (v if v is not None and v >= 0 else None) for k, v in raw.items()}
    return series, prov.to_dict()
