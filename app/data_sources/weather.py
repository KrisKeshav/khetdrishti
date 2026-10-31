"""16-day weather + soil-moisture forecast.

Primary: Open-Meteo (gives FAO-56 Penman-Monteith ET0 and modelled soil moisture directly).
Fallback: MET Norway Locationforecast (~10 days, no ET0/soil) — ET0 is then estimated with the
Hargreaves equation (FAO-56 eq. 52), and soil moisture is reported as unavailable.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from app.engine.agro import hargreaves_et0
from app.fetch import DataUnavailable, fetch_json

OPEN_METEO = "https://api.open-meteo.com/v1/forecast"
MET_NO = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
IST = timezone(timedelta(hours=5, minutes=30))

DAILY_VARS = ["temperature_2m_max", "temperature_2m_min", "precipitation_sum", "precipitation_probability_max",
              "et0_fao_evapotranspiration", "wind_speed_10m_max", "relative_humidity_2m_mean"]
SOIL_VARS = ["soil_moisture_0_to_1cm", "soil_moisture_1_to_3cm", "soil_moisture_3_to_9cm",
             "soil_moisture_9_to_27cm", "soil_moisture_27_to_81cm"]
# Layer thicknesses (cm) used to depth-weight the 0-27 cm root-zone average.
ROOT_LAYERS = {"soil_moisture_0_to_1cm": 1, "soil_moisture_1_to_3cm": 2,
               "soil_moisture_3_to_9cm": 6, "soil_moisture_9_to_27cm": 18}


def _validate_open_meteo(p):
    if p.get("error") or "daily" not in p:
        raise ValueError(p.get("reason", "no daily block"))


def forecast(lat: float, lon: float) -> dict:
    """Returns {"days": [...], "soil": {...} | None, "provider": str, "sources": [...]}.
    Each day: date, tmax, tmin, rain_mm, rain_prob, et0_mm, wind_kmh, rh_mean."""
    try:
        return _open_meteo(lat, lon)
    except DataUnavailable as primary_err:
        try:
            out = _met_norway(lat, lon)
            out["sources"][0]["note"] = f"fallback: {primary_err}"
            return out
        except DataUnavailable:
            raise primary_err


def _open_meteo(lat: float, lon: float) -> dict:
    payload, prov = fetch_json(
        "Open-Meteo Forecast API", OPEN_METEO,
        params={"latitude": round(lat, 4), "longitude": round(lon, 4), "daily": ",".join(DAILY_VARS),
                "hourly": ",".join(SOIL_VARS), "timezone": "Asia/Kolkata", "forecast_days": 16,
                "wind_speed_unit": "kmh"},
        ttl=timedelta(hours=1), validate=_validate_open_meteo)
    d = payload["daily"]
    days = []
    for i, day in enumerate(d["time"]):
        days.append({
            "date": day,
            "tmax": d["temperature_2m_max"][i],
            "tmin": d["temperature_2m_min"][i],
            "rain_mm": d["precipitation_sum"][i],
            "rain_prob": d["precipitation_probability_max"][i],
            "et0_mm": d["et0_fao_evapotranspiration"][i],
            "wind_kmh": d["wind_speed_10m_max"][i],
            "rh_mean": d["relative_humidity_2m_mean"][i],
        })
    return {"days": days, "soil": _soil_now(payload.get("hourly")), "provider": "Open-Meteo",
            "et0_method": "FAO-56 Penman-Monteith (Open-Meteo)", "sources": [prov.to_dict()]}


def _soil_now(hourly: dict | None) -> dict | None:
    """Soil moisture (m³/m³) for the current hour: each layer + depth-weighted 0-27 cm root zone,
    plus the root-zone daily mean for the next 7 days."""
    if not hourly or "time" not in hourly:
        return None
    now = datetime.now(IST).strftime("%Y-%m-%dT%H:00")
    times = hourly["time"]
    idx = times.index(now) if now in times else 0
    layers = {v: hourly.get(v, [None])[idx] for v in SOIL_VARS}
    if any(layers[v] is None for v in ROOT_LAYERS):
        return None

    def root_zone(i):
        vals = [(hourly[v][i], w) for v, w in ROOT_LAYERS.items()]
        if any(x is None for x, _ in vals):
            return None
        return sum(x * w for x, w in vals) / sum(w for _, w in vals)

    daily = defaultdict(list)
    for i, t in enumerate(times):
        rz = root_zone(i)
        if rz is not None:
            daily[t[:10]].append(rz)
    return {
        "time": times[idx],
        "layers": {k.replace("soil_moisture_", ""): round(v, 3) for k, v in layers.items() if v is not None},
        "root_zone_0_27cm": round(root_zone(idx), 3),
        "root_zone_daily": [{"date": k, "value": round(sum(v) / len(v), 3)} for k, v in list(daily.items())[:7]],
        "unit": "m³/m³",
    }


def _met_norway(lat: float, lon: float) -> dict:
    payload, prov = fetch_json("MET Norway Locationforecast", MET_NO,
                               params={"lat": round(lat, 4), "lon": round(lon, 4)}, ttl=timedelta(hours=1))
    series = payload["properties"]["timeseries"]
    buckets: dict[str, dict] = defaultdict(lambda: {"temps": [], "rain": 0.0, "wind": [], "rh": []})
    for s in series:
        t = datetime.fromisoformat(s["time"].replace("Z", "+00:00")).astimezone(IST)
        b = buckets[t.date().isoformat()]
        inst = s["data"]["instant"]["details"]
        b["temps"].append(inst.get("air_temperature"))
        b["wind"].append(inst.get("wind_speed", 0) * 3.6)
        b["rh"].append(inst.get("relative_humidity"))
        # Hourly steps carry next_1_hours; later 6-hourly steps carry next_6_hours. Each step's
        # window ends where the next step begins, so summing one window per step avoids double counting.
        nxt = s["data"].get("next_1_hours") or s["data"].get("next_6_hours")
        if nxt:
            b["rain"] += nxt["details"].get("precipitation_amount", 0.0)
    days = []
    for day, b in sorted(buckets.items()):
        temps = [x for x in b["temps"] if x is not None]
        if len(temps) < 2:
            continue
        tmax, tmin = max(temps), min(temps)
        doy = date.fromisoformat(day).timetuple().tm_yday
        rh = [x for x in b["rh"] if x is not None]
        days.append({"date": day, "tmax": round(tmax, 1), "tmin": round(tmin, 1), "rain_mm": round(b["rain"], 1),
                     "rain_prob": None, "et0_mm": round(hargreaves_et0(tmax, tmin, lat, doy), 2),
                     "wind_kmh": round(max(b["wind"]), 1), "rh_mean": round(sum(rh) / len(rh)) if rh else None})
    if not days:
        raise DataUnavailable("MET Norway Locationforecast", "empty timeseries")
    return {"days": days, "soil": None, "provider": "MET Norway",
            "et0_method": "Hargreaves (FAO-56 eq. 52) from MET Norway temperatures", "sources": [prov.to_dict()]}
