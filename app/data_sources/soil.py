"""ISRIC SoilGrids 2.0 (250 m global soil maps): texture, pH, organic carbon for 0-30 cm.
Texture -> FAO-56 field capacity / wilting point for the water-balance model."""
from datetime import timedelta

from app.engine.agro import soil_water_constants, usda_texture
from app.fetch import DataUnavailable, fetch_json

SOILGRIDS = "https://rest.isric.org/soilgrids/v2.0/properties/query"
DEPTHS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}   # label -> thickness (cm) for depth weighting
PROPS = ["clay", "sand", "silt", "phh2o", "soc"]


# SoilGrids masks built-up areas and water (null values). If the pin is on such a 250 m cell, we look at the
# neighbouring cells (~350 m away) and say so, rather than giving up or guessing.
NEIGHBOUR_OFFSETS = [(0, 0), (0.003, 0), (-0.003, 0), (0, 0.003), (0, -0.003)]


def soil_profile(lat: float, lon: float) -> dict:
    last_err = None
    for dlat, dlon in NEIGHBOUR_OFFSETS:
        try:
            out = _profile_at(lat + dlat, lon + dlon)
        except DataUnavailable as e:
            last_err = e
            if "no soil data" not in e.reason:
                raise  # network/API failure: neighbours won't help
            continue
        if (dlat, dlon) != (0, 0):
            out["note"] = "No soil data on the exact pin cell (built-up/water mask); using the nearest cell ~350 m away."
        return out
    raise last_err


def _profile_at(lat: float, lon: float) -> dict:
    params = [("lon", round(lon, 4)), ("lat", round(lat, 4)), ("value", "mean")]
    params += [("property", p) for p in PROPS] + [("depth", d) for d in DEPTHS]
    # httpx accepts a list of tuples for repeated query keys; fetch_json passes params through untouched.
    payload, prov = fetch_json("ISRIC SoilGrids 2.0", SOILGRIDS, params=params, ttl=timedelta(days=90), timeout=30)
    values = {}
    for layer in payload.get("properties", {}).get("layers", []):
        d_factor = layer.get("unit_measure", {}).get("d_factor", 1) or 1
        num = den = 0.0
        for d in layer["depths"]:
            v = d["values"].get("mean")
            w = DEPTHS.get(d["label"])
            if v is not None and w:
                num += v / d_factor * w
                den += w
        if den:
            values[layer["name"]] = num / den
    if not all(k in values for k in ("clay", "sand", "silt")):
        raise DataUnavailable("ISRIC SoilGrids 2.0", "no soil data at this point (water body or urban area?)")
    total = values["clay"] + values["sand"] + values["silt"]
    sand, silt, clay = (values[k] / total * 100 for k in ("sand", "silt", "clay"))
    texture = usda_texture(sand, silt, clay)
    out = {
        "depth": "0-30 cm (depth-weighted)",
        "sand_pct": round(sand), "silt_pct": round(silt), "clay_pct": round(clay),
        "texture": texture,
        "ph": round(values["phh2o"], 1) if "phh2o" in values else None,
        "organic_carbon_g_per_kg": round(values["soc"], 1) if "soc" in values else None,
        "water": soil_water_constants(texture),
        "resolution": "250 m modelled map — indicative for the area, not a lab soil test",
        "sources": [prov.to_dict()],
    }
    return out
