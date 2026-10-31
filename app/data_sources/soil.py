"""ISRIC SoilGrids 2.0 (250 m global soil maps): texture, pH, organic carbon for 0-30 cm.
Texture -> FAO-56 field capacity / wilting point for the water-balance model.

Two routes to the same SoilGrids maps:
  1. REST point-query API (one JSON request)                       — primary
  2. OGC WCS map service: tiny GeoTIFF per property x depth, read  — fallback
     with rasterio (the REST API has been down for hours at a time while the map service kept working)
"""
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from app.engine.agro import soil_water_constants, usda_texture
from app.fetch import DataUnavailable, Provenance, fetch_json, http_client, is_simulated_down

SOILGRIDS = "https://rest.isric.org/soilgrids/v2.0/properties/query"
WCS = "https://maps.isric.org/mapserv"
DEPTHS = {"0-5cm": 5, "5-15cm": 10, "15-30cm": 15}   # label -> thickness (cm) for depth weighting
PROPS = ["clay", "sand", "silt", "phh2o", "soc"]
D_FACTOR = 10        # SoilGrids stores clay/sand/silt in g/kg (/10 -> %), pH x10, SOC dg/kg (/10 -> g/kg)
WCS_HALF_DEG = 0.003  # ~330 m box around the pin: a handful of 250 m cells

# SoilGrids masks built-up areas and water (null values). If the pin is on such a 250 m cell, we look at the
# neighbouring cells (~350 m away) and say so, rather than giving up or guessing.
NEIGHBOUR_OFFSETS = [(0, 0), (0.003, 0), (-0.003, 0), (0, 0.003), (0, -0.003)]


_rest_down_until = 0.0
REST_BREAKER_S = 600


def soil_profile(lat: float, lon: float) -> dict:
    global _rest_down_until
    try:
        if time.monotonic() < _rest_down_until:     # circuit breaker: don't wait on a dead API every request
            raise DataUnavailable("ISRIC SoilGrids 2.0", "REST API recently failing; skipped for a few minutes")
        return _rest_with_neighbours(lat, lon)
    except DataUnavailable as rest_err:
        if "no soil data" in rest_err.reason:
            raise                                   # the map has no soil here; the WCS has the same map
        if "skipped" not in rest_err.reason:
            _rest_down_until = time.monotonic() + REST_BREAKER_S
        try:
            out = _wcs_profile(lat, lon)
        except DataUnavailable:
            raise rest_err
        out["sources"][0]["note"] = f"REST API unavailable ({rest_err.reason}); same SoilGrids maps read via WCS"
        return out


def _rest_with_neighbours(lat: float, lon: float) -> dict:
    last_err = None
    for dlat, dlon in NEIGHBOUR_OFFSETS:
        try:
            out = _rest_profile(lat + dlat, lon + dlon)
        except DataUnavailable as e:
            last_err = e
            if "no soil data" not in e.reason:
                raise  # network/API failure: neighbours won't help
            continue
        if (dlat, dlon) != (0, 0):
            out["note"] = "No soil data on the exact pin cell (built-up/water mask); using the nearest cell ~350 m away."
        return out
    raise last_err


def _rest_profile(lat: float, lon: float) -> dict:
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
    return _build(values, prov.to_dict())


def _wcs_profile(lat: float, lon: float) -> dict:
    source = "ISRIC SoilGrids 2.0 (WCS map service)"
    if is_simulated_down(source):
        raise DataUnavailable(source, "simulated outage (resilience demo)")
    jobs = [(p, d) for p in PROPS for d in DEPTHS]
    with ThreadPoolExecutor(max_workers=8) as ex:
        cells = dict(zip(jobs, ex.map(lambda job: _wcs_cell(job[0], job[1], lat, lon), jobs)))
    values = {}
    for p in PROPS:
        pairs = [(cells[(p, d)], w) for d, w in DEPTHS.items() if cells[(p, d)] is not None]
        if pairs:
            values[p] = sum(v * w for v, w in pairs) / sum(w for _, w in pairs)
    prov = Provenance(source, WCS, datetime.now(timezone.utc).isoformat(), "live").to_dict()
    return _build(values, prov)


def _wcs_cell(prop: str, depth: str, lat: float, lon: float) -> float | None:
    """Mean of the valid 250 m cells around the pin for one property/depth; cached 90 days (maps are static)."""
    from app.db import cache_get, cache_put
    key = f"soilwcs:{prop}:{depth}:{lat:.4f}:{lon:.4f}"
    hit = cache_get(key)
    if hit:
        return hit["payload"]
    params = {
        "map": f"/map/{prop}.map", "SERVICE": "WCS", "VERSION": "2.0.1", "REQUEST": "GetCoverage",
        "COVERAGEID": f"{prop}_{depth}_mean", "FORMAT": "image/tiff",
        "SUBSETTINGCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
        "OUTPUTCRS": "http://www.opengis.net/def/crs/EPSG/0/4326",
    }
    sub = [("SUBSET", f"long({lon - WCS_HALF_DEG},{lon + WCS_HALF_DEG})"),
           ("SUBSET", f"lat({lat - WCS_HALF_DEG},{lat + WCS_HALF_DEG})")]
    try:
        r = http_client().get(WCS, params=list(params.items()) + sub, timeout=30)
        r.raise_for_status()
        if "tiff" not in r.headers.get("content-type", ""):
            raise ValueError(r.text[:120])
        from rasterio.io import MemoryFile
        with MemoryFile(r.content) as mem, mem.open() as ds:
            arr = ds.read(1).astype("float64")
            nodata = ds.nodata
    except Exception as e:
        raise DataUnavailable("ISRIC SoilGrids 2.0 (WCS map service)", f"{type(e).__name__}: {e}"[:160])
    valid = arr[(arr > 0) & ((arr != nodata) if nodata is not None else True)]   # 0 = masked (urban/water)
    value = float(valid.mean()) / D_FACTOR if valid.size else None
    cache_put(key, "soilgrids wcs cell", value)
    return value


def _build(values: dict, source: dict) -> dict:
    if not all(k in values for k in ("clay", "sand", "silt")):
        raise DataUnavailable("ISRIC SoilGrids 2.0", "no soil data at this point (water body or urban area?)")
    total = values["clay"] + values["sand"] + values["silt"]
    sand, silt, clay = (values[k] / total * 100 for k in ("sand", "silt", "clay"))
    texture = usda_texture(sand, silt, clay)
    return {
        "depth": "0-30 cm (depth-weighted)",
        "sand_pct": round(sand), "silt_pct": round(silt), "clay_pct": round(clay),
        "texture": texture,
        "ph": round(values["phh2o"], 1) if "phh2o" in values else None,
        "organic_carbon_g_per_kg": round(values["soc"], 1) if "soc" in values else None,
        "water": soil_water_constants(texture),
        "resolution": "250 m modelled map — indicative for the area, not a lab soil test",
        "sources": [source],
    }
