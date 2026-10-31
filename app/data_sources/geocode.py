"""OpenStreetMap Nominatim: reverse geocoding (plot -> village/district/state) and place search.
Usage policy: max 1 request/second, identifying User-Agent. We cache results for 30 days."""
import threading
import time
from datetime import timedelta

from app.fetch import DataUnavailable, fetch_json

NOMINATIM = "https://nominatim.openstreetmap.org"
SOURCE = "OpenStreetMap Nominatim"

_lock = threading.Lock()
_last_call = 0.0


def _throttle():
    global _last_call
    with _lock:
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


class OutsideIndia(Exception):
    pass


def reverse(lat: float, lon: float) -> dict:
    """Return {village, district, state, country_code, display_name} for a point.
    Raises OutsideIndia if the point is not on Indian land (e.g. sea, another country)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise OutsideIndia("Coordinates are invalid.")
    _throttle()
    payload, prov = fetch_json(SOURCE, f"{NOMINATIM}/reverse",
                               params={"format": "jsonv2", "lat": round(lat, 5), "lon": round(lon, 5),
                                       "zoom": 14, "addressdetails": 1, "accept-language": "en"},
                               ttl=timedelta(days=30))
    if "error" in payload:
        raise OutsideIndia("No land address at this point — is the pin in water?")
    addr = payload.get("address", {})
    if addr.get("country_code") != "in":
        raise OutsideIndia(f"This point is in {addr.get('country', 'another country')}, not India.")
    district = (addr.get("state_district") or addr.get("county") or addr.get("city_district")
                or addr.get("city") or "")
    district = district.replace(" District", "").replace(" district", "").strip()
    return {
        "village": addr.get("village") or addr.get("hamlet") or addr.get("town") or addr.get("suburb") or "",
        "subdistrict": addr.get("subdistrict") or addr.get("county") or "",
        "district": district,
        "state": addr.get("state", ""),
        "country_code": "in",
        "display_name": payload.get("display_name", ""),
        "source": prov.to_dict(),
    }


def search(query: str, limit: int = 5) -> list[dict]:
    if not query.strip():
        return []
    _throttle()
    payload, _ = fetch_json(SOURCE, f"{NOMINATIM}/search",
                            params={"q": query, "format": "jsonv2", "countrycodes": "in", "limit": limit,
                                    "accept-language": "en"},
                            ttl=timedelta(days=30))
    return [{"name": p.get("display_name"), "lat": float(p["lat"]), "lon": float(p["lon"])} for p in payload]


def locate_market(market: str, district: str, state: str) -> tuple[float, float] | None:
    """Best-effort coordinates for a mandi name like 'APMC Lasalgaon '. Returns None if not found."""
    name = market.replace("APMC", "").strip()
    name = name.split("(")[0].strip() or name
    for q in (f"{name}, {district}, {state}, India", f"{name}, {state}, India"):
        try:
            res = search(q, limit=1)
        except DataUnavailable:
            return None
        if res:
            return res[0]["lat"], res[0]["lon"]
    return None
