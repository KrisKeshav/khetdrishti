"""Sentinel-2 NDVI time series for a single plot.

NDVI = (NIR - Red) / (NIR + Red) using bands B08 and B04 at 10 m. Pixels flagged by the Sentinel-2
Scene Classification Layer (SCL) as cloud, cloud shadow, water, snow or no-data are masked out; we keep
SCL 4 (vegetation), 5 (bare soil) and 7 (unclassified). Observations with < 50 % clear pixels are dropped.

Provider 1 (if configured): Copernicus Data Space Ecosystem — Sentinel Hub Statistical API. NDVI statistics
are computed server-side, so one small JSON response covers the whole season. Needs a free OAuth client.
Provider 2 (default, no account): Microsoft Planetary Computer — STAC search + its data API computes zonal
statistics for our plot polygon server-side (~1-2 s per scene, a few KB each).
Provider 3 (last resort, no account): Element84 Earth Search + Cloud-Optimised GeoTIFFs on AWS, read locally
with rasterio. Each scene needs several MB, so it is only practical when the server is hosted near us-west-2.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from math import cos, radians

import numpy as np

from app.config import settings
from app.fetch import DataUnavailable, Provenance, fetch_json, http_client

CDSE_TOKEN_URL = "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/protocol/openid-connect/token"
CDSE_STATS_URL = "https://sh.dataspace.copernicus.eu/api/v1/statistics"
PC_STAC = "https://planetarycomputer.microsoft.com/api/stac/v1/search"
PC_STATS = "https://planetarycomputer.microsoft.com/api/data/v1/item/statistics"
EARTH_SEARCH = "https://earth-search.aws.element84.com/v1/search"
CLEAR_SCL = (4, 5, 7)
MIN_VALID_FRAC = 0.5

EVALSCRIPT = """//VERSION=3
function setup() {
  return {
    input: [{bands: ["B04", "B08", "SCL", "dataMask"]}],
    output: [{id: "ndvi", bands: 1, sampleType: "FLOAT32"}, {id: "dataMask", bands: 1}]
  };
}
function evaluatePixel(s) {
  const ndvi = (s.B08 - s.B04) / (s.B08 + s.B04);
  const clear = (s.SCL == 4 || s.SCL == 5 || s.SCL == 7) ? 1 : 0;
  return {ndvi: [ndvi], dataMask: [s.dataMask * clear]};
}"""


def plot_bbox(lat: float, lon: float, half_size_m: float) -> tuple[float, float, float, float]:
    dlat = half_size_m / 111_320
    dlon = half_size_m / (111_320 * cos(radians(lat)))
    return (lon - dlon, lat - dlat, lon + dlon, lat + dlat)


# ------------------------------------------------------------------ public API

def ndvi_report(lat: float, lon: float, half_size_m: float = 40, days: int = 120,
                today: date | None = None) -> dict:
    """NDVI series for this season + same period last year, with derived indicators."""
    today = today or date.today()
    bbox = plot_bbox(lat, lon, half_size_m)
    cur_start, cur_end = today - timedelta(days=days), today
    ly_start, ly_end = cur_start - timedelta(days=365), cur_end - timedelta(days=365)

    errors = []
    providers = []
    if settings.cdse_client_id and settings.cdse_client_secret:
        providers.append(("Copernicus Data Space (Sentinel Hub Statistical API)", _cdse_series))
    providers.append(("Microsoft Planetary Computer (Sentinel-2 L2A)", _planetary_series))
    providers.append(("Element84 Earth Search + AWS Sentinel-2 COGs", _earth_search_series))

    for name, fn in providers:
        try:
            series, prov = fn(bbox, cur_start, cur_end, max_scenes=10)
            if not series:
                raise DataUnavailable(name, f"no cloud-free observation in the last {days} days")
            try:
                last_year, prov_ly = fn(bbox, ly_start, ly_end, max_scenes=4)
            except DataUnavailable as e:
                last_year, prov_ly = [], None
                errors.append(f"last-year series: {e.reason}")
            if prov_ly:
                prov_ly = {**prov_ly, "note": "same period last year"}
            sources = [prov] + ([prov_ly] if prov_ly else [])
            if errors:
                sources[0]["note"] = "; ".join(errors)
            return _summarise(series, last_year, half_size_m, name, sources)
        except DataUnavailable as e:
            errors.append(f"{name}: {e.reason}")
    raise DataUnavailable("Sentinel-2 NDVI", " | ".join(errors))


def _summarise(series, last_year, half_size_m, provider, sources) -> dict:
    series = sorted(series, key=lambda r: r["date"])
    latest = series[-1]
    latest_d = date.fromisoformat(latest["date"])
    out = {
        "provider": provider,
        "plot_size_m": int(half_size_m * 2),
        "series": series,
        "last_year_series": sorted(last_year, key=lambda r: r["date"]),
        "latest_ndvi": latest["ndvi"],
        "latest_date": latest["date"],
        "days_since_latest": (date.today() - latest_d).days,
        "health_class": _health_class(latest["ndvi"]),
        "health_class_basis": "generic NDVI ranges: <0.2 bare/very sparse, 0.2-0.4 sparse or stressed, "
                              "0.4-0.6 moderate, >0.6 dense green vegetation",
        "sources": sources,
    }
    # Change over ~30 days: compare with the observation closest to 30 days before the latest one.
    older = [r for r in series if (latest_d - date.fromisoformat(r["date"])).days >= 20]
    if older:
        ref = min(older, key=lambda r: abs((latest_d - date.fromisoformat(r["date"])).days - 30))
        out["ndvi_change_30d"] = round(latest["ndvi"] - ref["ndvi"], 3)
        out["ndvi_change_30d_ref_date"] = ref["date"]
    # Same time last year (±25 days around the anniversary of the latest observation).
    anniv = latest_d - timedelta(days=365)
    near = [r for r in last_year if abs((date.fromisoformat(r["date"]) - anniv).days) <= 25]
    if near:
        ly = sum(r["ndvi"] for r in near) / len(near)
        out["last_year_ndvi"] = round(ly, 3)
        if ly > 0.05:
            out["change_vs_last_year_pct"] = round((latest["ndvi"] - ly) / ly * 100)
        out["last_year_note"] = "Comparison is meaningful only if the same crop was grown last year."
    return out


def _health_class(v: float) -> str:
    if v < 0.2:
        return "bare soil / very sparse"
    if v < 0.4:
        return "sparse or stressed vegetation"
    if v < 0.6:
        return "moderate vegetation"
    return "dense, green vegetation"


# ------------------------------------------------------------------ provider 1: Copernicus Data Space

_token = {"value": None, "expires": 0.0}


def _cdse_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    try:
        r = http_client().post(CDSE_TOKEN_URL, data={"grant_type": "client_credentials",
                                                     "client_id": settings.cdse_client_id,
                                                     "client_secret": settings.cdse_client_secret}, timeout=20)
        r.raise_for_status()
        body = r.json()
    except Exception as e:  # network or auth failure
        raise DataUnavailable("Copernicus Data Space", f"auth failed: {e}"[:200])
    _token.update(value=body["access_token"], expires=time.time() + body.get("expires_in", 600))
    return _token["value"]


def _cdse_series(bbox, start: date, end: date, max_scenes: int) -> tuple[list[dict], dict]:
    width = height = max(2, round((bbox[3] - bbox[1]) * 111_320 / 10))   # ~10 m pixels
    body = {
        "input": {
            "bounds": {"bbox": list(bbox), "properties": {"crs": "http://www.opengis.net/def/crs/OGC/1.3/CRS84"}},
            "data": [{"type": "sentinel-2-l2a", "dataFilter": {"maxCloudCoverage": 80}}],
        },
        "aggregation": {
            "timeRange": {"from": f"{start.isoformat()}T00:00:00Z", "to": f"{end.isoformat()}T23:59:59Z"},
            "aggregationInterval": {"of": "P5D"},
            "width": width, "height": height,
            "evalscript": EVALSCRIPT,
        },
        "calculations": {"default": {}},
    }

    def validate(p):
        if "data" not in p:
            raise ValueError(str(p)[:200])

    payload, prov = fetch_json("Copernicus Data Space — Sentinel-2 L2A Statistical API", CDSE_STATS_URL,
                               method="POST", json_body=body, ttl=timedelta(hours=12), timeout=45,
                               headers={"Authorization": f"Bearer {_cdse_token()}"}, validate=validate)
    series = []
    for item in payload["data"]:
        if item.get("error"):
            continue
        stats = item["outputs"]["ndvi"]["bands"]["B0"]["stats"]
        n, nodata = stats.get("sampleCount", 0), stats.get("noDataCount", 0)
        if not n or stats.get("mean") is None or (n - nodata) / n < MIN_VALID_FRAC:
            continue
        series.append({"date": item["interval"]["from"][:10], "ndvi": round(float(stats["mean"]), 3),
                       "ndvi_sd": round(float(stats.get("stDev", 0) or 0), 3),
                       "valid_pct": round((n - nodata) / n * 100), "pixels": n - nodata})
    return series[-max_scenes * 3:], prov.to_dict()


# ------------------------------------------------------------------ provider 2: Planetary Computer

PC_MIN_CLEAR = 0.8   # NDVI here is computed over all plot pixels, so demand a mostly cloud-free plot


def _polygon(bbox) -> dict:
    x0, y0, x1, y1 = bbox
    return {"type": "Feature", "properties": {},
            "geometry": {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}}


def _planetary_series(bbox, start: date, end: date, max_scenes: int) -> tuple[list[dict], dict]:
    body = {"collections": ["sentinel-2-l2a"], "bbox": list(bbox), "datetime": f"{start}/{end}", "limit": 40,
            "query": {"eo:cloud_cover": {"lt": 60}}, "sortby": [{"field": "datetime", "direction": "desc"}]}
    payload, prov = fetch_json("Microsoft Planetary Computer STAC (Sentinel-2 L2A)", PC_STAC, method="POST",
                               json_body=body, ttl=timedelta(hours=6))
    by_date: dict[str, dict] = {}
    for f in payload.get("features", []):
        d = f["properties"]["datetime"][:10]
        if d not in by_date or f["properties"]["eo:cloud_cover"] < by_date[d]["properties"]["eo:cloud_cover"]:
            by_date[d] = f
    items = sorted(by_date.values(), key=lambda f: f["properties"]["datetime"], reverse=True)[:max_scenes]
    poly = _polygon(bbox)
    with ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(lambda it: _pc_scene(it, poly), items))
    p = prov.to_dict()
    p["note"] = "NDVI computed server-side by the Planetary Computer data API for the plot polygon"
    return [r for r in results if r], p


def _pc_scene(item: dict, poly: dict) -> dict | None:
    props = item["properties"]
    # From processing baseline 04.00 (Jan 2022) ESA adds +1000 to L2A digital numbers; PC serves them as-is.
    baseline = float(props.get("s2:processing_baseline", "0") or 0)
    expr = "(B08-B04)/(B08+B04-2000)" if baseline >= 4.0 else "(B08-B04)/(B08+B04)"
    common = {"collection": "sentinel-2-l2a", "item": item["id"]}
    try:
        scl, _ = fetch_json("Planetary Computer data API (SCL)", PC_STATS, method="POST", json_body=poly,
                            params={**common, "assets": "SCL", "categorical": "true"},
                            ttl=timedelta(days=365), retries=1, timeout=30)
        counts, classes = scl["properties"]["statistics"]["SCL_b1"]["histogram"]
        total = sum(counts)
        clear = sum(c for c, k in zip(counts, classes) if int(k) in CLEAR_SCL) / total if total else 0
        if clear < PC_MIN_CLEAR:
            return None
        nd, _ = fetch_json("Planetary Computer data API (NDVI)", PC_STATS, method="POST", json_body=poly,
                           params={**common, "expression": expr, "asset_as_band": "true"},
                           ttl=timedelta(days=365), retries=1, timeout=30)
        st = next(iter(nd["properties"]["statistics"].values()))
    except (DataUnavailable, KeyError, ValueError, StopIteration):
        return None
    return {"date": props["datetime"][:10], "ndvi": round(float(st["median"]), 3),
            "ndvi_sd": round(float(st["std"]), 3), "valid_pct": round(clear * 100), "pixels": int(st["count"]),
            "scene": item["id"], "cloud_cover_pct": round(props["eo:cloud_cover"], 1)}


# ------------------------------------------------------------------ provider 3: Earth Search COGs

os.environ.setdefault("GDAL_DISABLE_READDIR_ON_OPEN", "EMPTY_DIR")
os.environ.setdefault("CPL_VSIL_CURL_ALLOWED_EXTENSIONS", ".tif")
os.environ.setdefault("GDAL_HTTP_MULTIRANGE", "YES")
os.environ.setdefault("GDAL_HTTP_MERGE_CONSECUTIVE_RANGES", "YES")
os.environ.setdefault("GDAL_HTTP_TIMEOUT", "25")
os.environ.setdefault("GDAL_HTTP_MAX_RETRY", "2")
os.environ.setdefault("GDAL_HTTP_RETRY_DELAY", "1")
os.environ.setdefault("VSI_CACHE", "TRUE")

COG_BUDGET_S = 75   # total wall-clock budget for reading scenes


def _earth_search_series(bbox, start: date, end: date, max_scenes: int) -> tuple[list[dict], dict]:
    from app.db import cache_get, cache_put  # local import keeps module import light

    body = {"collections": ["sentinel-2-l2a"], "bbox": list(bbox),
            "datetime": f"{start.isoformat()}T00:00:00Z/{end.isoformat()}T23:59:59Z", "limit": 40,
            "query": {"eo:cloud_cover": {"lt": 50}},
            "sortby": [{"field": "properties.datetime", "direction": "desc"}]}
    payload, prov = fetch_json("Element84 Earth Search STAC (Sentinel-2 L2A)", EARTH_SEARCH, method="POST",
                               json_body=body, ttl=timedelta(hours=6))
    # One observation per date (overlapping tiles produce duplicates); prefer the least cloudy.
    by_date: dict[str, dict] = {}
    for f in payload.get("features", []):
        d = f["properties"]["datetime"][:10]
        if d not in by_date or f["properties"]["eo:cloud_cover"] < by_date[d]["properties"]["eo:cloud_cover"]:
            by_date[d] = f
    items = sorted(by_date.values(), key=lambda f: f["properties"]["datetime"], reverse=True)[:max_scenes]
    if not items:
        return [], prov.to_dict()

    series, deadline = [], time.monotonic() + COG_BUDGET_S
    # Not a `with` block: its exit would wait for slow reads past our time budget.
    ex = ThreadPoolExecutor(max_workers=6)
    futs = {}
    for it in items:
        key = f"cogstat:{it['id']}:{','.join(f'{v:.5f}' for v in bbox)}"
        hit = cache_get(key)          # a scene never changes, so its plot statistic is cached forever
        if hit:
            if hit["payload"]:
                series.append(hit["payload"])
            continue
        futs[ex.submit(_scene_ndvi, it, bbox)] = key
    try:
        for fut in as_completed(futs, timeout=max(1, deadline - time.monotonic())):
            try:
                res = fut.result()
            except Exception:
                continue  # unreadable scene: skip it, the others still count
            cache_put(futs[fut], "sentinel-2 cog stat", res)
            if res:
                series.append(res)
    except TimeoutError:
        prov.note = "time budget reached; some scenes skipped"
    ex.shutdown(wait=False, cancel_futures=True)
    return series, prov.to_dict()


def _scene_ndvi(item: dict, bbox) -> dict | None:
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    props = item["properties"]
    # Earth Search v1 reports whether the PB>=04.00 radiometric offset (-1000 DN) is already removed.
    baseline = float(props.get("s2:processing_baseline", "0") or 0)
    offset = 0.0 if props.get("earthsearch:boa_offset_applied", False) or baseline < 4.0 else -0.1

    def read(asset, out_shape=None):
        with rasterio.open(item["assets"][asset]["href"]) as src:
            b = transform_bounds("EPSG:4326", src.crs, *bbox)
            w = from_bounds(*b, transform=src.transform).round_offsets().round_lengths()
            w = w.intersection(Window(0, 0, src.width, src.height))
            return src.read(1, window=w, out_shape=out_shape, resampling=Resampling.nearest)

    with rasterio.Env():
        red = read("red").astype("float32")
        nir = read("nir", out_shape=red.shape).astype("float32")
        scl = read("scl", out_shape=red.shape)
    valid = np.isin(scl, CLEAR_SCL) & (red > 0) & (nir > 0)
    if red.size == 0 or valid.mean() < MIN_VALID_FRAC:
        return None
    r = red * 1e-4 + offset
    n = nir * 1e-4 + offset
    ndvi = (n - r) / np.where((n + r) == 0, np.nan, n + r)
    vals = ndvi[valid & np.isfinite(ndvi)]
    if vals.size == 0:
        return None
    return {"date": props["datetime"][:10], "ndvi": round(float(vals.mean()), 3),
            "ndvi_sd": round(float(vals.std()), 3), "valid_pct": round(float(valid.mean()) * 100),
            "pixels": int(vals.size), "scene": item["id"], "cloud_cover_pct": round(props["eo:cloud_cover"], 1)}
