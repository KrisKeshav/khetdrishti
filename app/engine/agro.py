"""Pure agronomy maths (no I/O) — easy to unit-test and to explain to judges.

References
- FAO Irrigation & Drainage Paper 56 (Allen et al., 1998), "Crop evapotranspiration":
  eq. 21-25 (extraterrestrial radiation), eq. 52 (Hargreaves ET0), eq. 85 (root-zone depletion),
  Table 19 (typical soil water characteristics).
- USDA soil texture triangle (Soil Survey Manual).
"""
import math

ROOT_ZONE_MM = 270.0   # we use the 0-27 cm model layers from Open-Meteo as the monitored root zone

# FAO-56 Table 19: (theta_FC, theta_WP) in m³/m³, mid-point of the published ranges.
FAO56_SOIL_WATER = {
    "sand": (0.12, 0.045),
    "loamy sand": (0.15, 0.065),
    "sandy loam": (0.23, 0.11),
    "loam": (0.25, 0.12),
    "silt loam": (0.29, 0.15),
    "silt": (0.32, 0.17),
    "silty clay loam": (0.335, 0.205),
    "silty clay": (0.36, 0.23),
    "clay": (0.36, 0.22),
}
# USDA classes that Table 19 does not list are mapped to the closest listed class (and labelled as such).
TABLE19_MAPPING = {"sandy clay loam": "loam", "clay loam": "silty clay loam", "sandy clay": "clay"}


def extraterrestrial_radiation(lat_deg: float, doy: int) -> float:
    """Ra in MJ m-2 day-1 (FAO-56 eq. 21-25)."""
    gsc = 0.0820
    phi = math.radians(lat_deg)
    dr = 1 + 0.033 * math.cos(2 * math.pi * doy / 365)
    delta = 0.409 * math.sin(2 * math.pi * doy / 365 - 1.39)
    ws = math.acos(max(-1.0, min(1.0, -math.tan(phi) * math.tan(delta))))
    return (24 * 60 / math.pi) * gsc * dr * (ws * math.sin(phi) * math.sin(delta)
                                            + math.cos(phi) * math.cos(delta) * math.sin(ws))


def hargreaves_et0(tmax: float, tmin: float, lat_deg: float, doy: int) -> float:
    """Reference evapotranspiration, mm/day (FAO-56 eq. 52). Used only when Penman-Monteith inputs
    are unavailable (fallback weather provider)."""
    ra = extraterrestrial_radiation(lat_deg, doy)
    tmean = (tmax + tmin) / 2
    return max(0.0, 0.0023 * (tmean + 17.8) * math.sqrt(max(tmax - tmin, 0.0)) * 0.408 * ra)


def effective_rain(rain_mm: float, et0_mm: float) -> float:
    """Daily effective rainfall. FAO-56 (ch. 8) notes rain below ~0.2·ET0 is normally fully evaporated;
    of the rest we count 80 % (fixed-percentage method, FAO Irrigation & Drainage Paper 25)."""
    if rain_mm is None:
        return 0.0
    if rain_mm < 0.2 * (et0_mm or 0):
        return 0.0
    return 0.8 * rain_mm


def usda_texture(sand: float, silt: float, clay: float) -> str:
    """USDA texture class from particle-size percentages."""
    if silt + 1.5 * clay < 15:
        return "sand"
    if silt + 2 * clay < 30:
        return "loamy sand"
    if (7 <= clay < 20 and sand > 52) or (clay < 7 and silt < 50):
        return "sandy loam"
    if 7 <= clay < 27 and 28 <= silt < 50 and sand <= 52:
        return "loam"
    if (silt >= 50 and 12 <= clay < 27) or (50 <= silt < 80 and clay < 12):
        return "silt loam"
    if silt >= 80 and clay < 12:
        return "silt"
    if 20 <= clay < 35 and silt < 28 and sand > 45:
        return "sandy clay loam"
    if 27 <= clay < 40 and 20 < sand <= 45:
        return "clay loam"
    if 27 <= clay < 40 and sand <= 20:
        return "silty clay loam"
    if clay >= 35 and sand > 45:
        return "sandy clay"
    if clay >= 40 and silt >= 40:
        return "silty clay"
    return "clay"


def soil_water_constants(texture: str) -> dict:
    table_class = TABLE19_MAPPING.get(texture, texture)
    fc, wp = FAO56_SOIL_WATER.get(table_class, FAO56_SOIL_WATER["loam"])
    note = "FAO-56 Table 19 mid-range values"
    if table_class != texture:
        note += f" (USDA '{texture}' mapped to '{table_class}')"
    return {"texture": texture, "fc": fc, "wp": wp, "note": note}


def water_balance(days: list[dict], kc_by_date: dict[str, float], theta_now: float | None,
                  fc: float, wp: float, p: float, horizon: int = 7) -> dict:
    """FAO-56 daily root-zone depletion model (eq. 85, no capillary rise / runoff terms).

    days: forecast days with rain_mm, et0_mm. kc_by_date: crop coefficient per date.
    theta_now: current root-zone volumetric moisture (None if unavailable).
    Returns crop water use, rain, and — when soil moisture is known — the day irrigation becomes due.
    All numbers the advice relies on are computed here so the LLM never does arithmetic."""
    window = [d for d in days if d.get("et0_mm") is not None][:horizon]
    taw = (fc - wp) * ROOT_ZONE_MM            # total available water in root zone, mm
    raw = p * taw                             # readily available water, mm
    etc_total = rain_total = eff_total = 0.0
    timeline = []
    depletion = None if theta_now is None else min(max((fc - theta_now) * ROOT_ZONE_MM, 0.0), taw)
    start_depletion = depletion
    due_date = None
    for d in window:
        kc = kc_by_date.get(d["date"], 1.0)
        etc = kc * d["et0_mm"]
        eff = effective_rain(d.get("rain_mm") or 0.0, d["et0_mm"])
        etc_total += etc
        rain_total += d.get("rain_mm") or 0.0
        eff_total += eff
        row = {"date": d["date"], "etc_mm": round(etc, 1), "rain_mm": round(d.get("rain_mm") or 0.0, 1),
               "eff_rain_mm": round(eff, 1)}
        if depletion is not None:
            depletion = min(max(depletion - eff + etc, 0.0), taw)
            row["depletion_mm"] = round(depletion, 1)
            if due_date is None and depletion > raw:
                due_date = d["date"]
        timeline.append(row)

    out = {
        "horizon_days": len(window),
        "crop_water_use_mm": round(etc_total, 1),
        "rain_forecast_mm": round(rain_total, 1),
        "effective_rain_mm": round(eff_total, 1),
        "net_balance_mm": round(eff_total - etc_total, 1),
        "timeline": timeline,
        "soil_moisture_known": theta_now is not None,
    }
    if theta_now is None:
        out["recommendation"] = "soil_moisture_unavailable"
        return out

    avail_frac = (theta_now - wp) / (fc - wp) if fc > wp else 0.0
    rain_next3_eff = sum(r["eff_rain_mm"] for r in timeline[:3])
    out.update({
        "root_zone_moisture_m3m3": round(theta_now, 3),
        "field_capacity_m3m3": fc,
        "wilting_point_m3m3": wp,
        "available_water_pct": round(max(0.0, min(avail_frac, 1.0)) * 100),
        "current_depletion_mm": round(start_depletion, 1),
        "readily_available_water_mm": round(raw, 1),
        "irrigation_due_date": due_date,
        "effective_rain_next_3d_mm": round(rain_next3_eff, 1),
    })
    if start_depletion > raw:
        if rain_next3_eff >= start_depletion * 0.5:
            out["recommendation"] = "wait_rain_expected"
        else:
            out["recommendation"] = "irrigate_now"
            out["suggested_irrigation_mm"] = round(start_depletion, 1)
    elif due_date:
        out["recommendation"] = "irrigate_on_due_date"
        due_row = next(r for r in timeline if r["date"] == due_date)
        out["suggested_irrigation_mm"] = due_row["depletion_mm"]
    else:
        out["recommendation"] = "no_irrigation_needed"
    return out
