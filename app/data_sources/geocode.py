"""Geocoding with a fallback chain (all OpenStreetMap / GeoNames based, free, no key):

  reverse (pin -> village/district/state, India check):
      OpenStreetMap Nominatim  ->  Photon (komoot) + Open-Meteo search for the district
  search (place name -> coordinates; also used to locate mandi towns):
      Nominatim  ->  Open-Meteo Geocoding API

Why the chain: Nominatim rate-limits shared cloud IPs (it returned HTTP 429 to our Render server while working
fine from a laptop). A circuit breaker skips Nominatim for a while after it refuses us, so every request does
not pay for retries against a provider that is blocking us.
"""
import threading
import time
import unicodedata
from datetime import timedelta

from app.fetch import DataUnavailable, fetch_json

NOMINATIM = "https://nominatim.openstreetmap.org"
PHOTON = "https://photon.komoot.io/reverse"
OM_SEARCH = "https://geocoding-api.open-meteo.com/v1/search"
SOURCE = "OpenStreetMap Nominatim"
BREAKER_S = 600

_lock = threading.Lock()
_last_call = 0.0
_nominatim_blocked_until = 0.0


class OutsideIndia(Exception):
    pass


def _throttle():
    """Nominatim usage policy: at most 1 request per second."""
    global _last_call
    with _lock:
        wait = 1.1 - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _nominatim(path: str, params: dict):
    global _nominatim_blocked_until
    if time.monotonic() < _nominatim_blocked_until:
        raise DataUnavailable(SOURCE, "temporarily skipped after being rate-limited")
    _throttle()
    try:
        return fetch_json(SOURCE, f"{NOMINATIM}/{path}", params=params, ttl=timedelta(days=30), retries=0, timeout=10)
    except DataUnavailable:
        _nominatim_blocked_until = time.monotonic() + BREAKER_S
        raise


def _ascii(s: str) -> str:
    """'Guntūr District' -> 'Guntur District' (Agmarknet and our fuzzy matching use plain ASCII names)."""
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().strip()


def _clean_district(s: str) -> str:
    s = _ascii(s)
    for suffix in (" District", " district", " Division"):
        s = s.replace(suffix, "")
    return s.strip()


# ------------------------------------------------------------------ reverse

def reverse(lat: float, lon: float) -> dict:
    """Return {village, subdistrict, district, state, country_code, display_name, source}.
    Raises OutsideIndia if the point is not on Indian land (sea, another country)."""
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise OutsideIndia("Coordinates are invalid.")
    try:
        return _reverse_nominatim(lat, lon)
    except DataUnavailable as first:
        try:
            return _reverse_photon(lat, lon)
        except DataUnavailable:
            raise first


def _reverse_nominatim(lat, lon) -> dict:
    payload, prov = _nominatim("reverse", {"format": "jsonv2", "lat": round(lat, 5), "lon": round(lon, 5),
                                           "zoom": 14, "addressdetails": 1, "accept-language": "en"})
    if "error" in payload:
        raise OutsideIndia("No land address at this point — is the pin in water?")
    addr = payload.get("address", {})
    if addr.get("country_code") != "in":
        raise OutsideIndia(f"This point is in {addr.get('country', 'another country')}, not India.")
    district = (addr.get("state_district") or addr.get("county") or addr.get("city_district")
                or addr.get("city") or "")
    return {
        "village": addr.get("village") or addr.get("hamlet") or addr.get("town") or addr.get("suburb") or "",
        "subdistrict": addr.get("subdistrict") or addr.get("county") or "",
        "district": _clean_district(district),
        "state": addr.get("state", ""),
        "country_code": "in",
        "display_name": payload.get("display_name", ""),
        "source": prov.to_dict(),
    }


def _reverse_photon(lat, lon) -> dict:
    payload, prov = fetch_json("Photon geocoder (komoot, OSM)", PHOTON,
                               params={"lat": round(lat, 5), "lon": round(lon, 5), "lang": "en", "radius": 10,
                                       "limit": 1}, ttl=timedelta(days=30), retries=1, timeout=15)
    feats = payload.get("features") or []
    if not feats:
        raise OutsideIndia("No land address within 10 km — is the pin in water?")
    p = feats[0]["properties"]
    if p.get("countrycode", "").upper() != "IN":
        raise OutsideIndia(f"This point is in {p.get('country', 'another country')}, not India.")
    state = p.get("state", "")
    place = p.get("city") or p.get("locality") or p.get("district") or p.get("name") or ""
    subdistrict = p.get("county", "")
    district = _district_for([place, _strip_admin_suffix(subdistrict), p.get("name", "")], state)
    out = {"village": place, "subdistrict": subdistrict, "district": district, "state": state, "country_code": "in",
           "display_name": ", ".join(x for x in (place, subdistrict, district, state) if x),
           "source": prov.to_dict()}
    if not district:
        out["source"]["note"] = "district could not be resolved; mandi matching uses distance only"
    return out


def _strip_admin_suffix(s: str) -> str:
    for suffix in (" Taluka", " Tahsil", " Tehsil", " Mandal", " Block", " Subdivision", " Sub-Division"):
        s = s.replace(suffix, "")
    return s.strip()


def _district_for(names: list[str], state: str) -> str:
    """Photon gives tehsil/mandal, not district: look the place up in GeoNames (Open-Meteo) within the same state."""
    for name in [n for n in names if n]:
        try:
            for r in _om_search(name, count=10):
                if _ascii(r.get("admin1", "")).lower() == _ascii(state).lower() and r.get("admin2"):
                    return _clean_district(r["admin2"])
        except DataUnavailable:
            return ""
    return ""


# ------------------------------------------------------------------ search

def _om_search(name: str, count: int = 5) -> list[dict]:
    payload, _ = fetch_json("Open-Meteo Geocoding API", OM_SEARCH,
                            params={"name": name, "count": count, "countryCode": "IN", "language": "en"},
                            ttl=timedelta(days=30), retries=1, timeout=15)
    return payload.get("results") or []


def search(query: str, limit: int = 5) -> list[dict]:
    if not query.strip():
        return []
    try:
        payload, _ = _nominatim("search", {"q": query, "format": "jsonv2", "countrycodes": "in", "limit": limit,
                                           "accept-language": "en"})
        return [{"name": p.get("display_name"), "lat": float(p["lat"]), "lon": float(p["lon"])} for p in payload]
    except DataUnavailable:
        # Open-Meteo matches place names only, so use the first comma-separated part of the query.
        res = _om_search(query.split(",")[0].strip(), count=limit)
        return [{"name": ", ".join(x for x in (r["name"], r.get("admin2"), r.get("admin1")) if x),
                 "lat": r["latitude"], "lon": r["longitude"]} for r in res]


def locate_market(market: str, district: str, state: str) -> tuple[float, float] | None:
    """Best-effort coordinates for a mandi name like 'APMC Lasalgaon(Vinchur) '. Open-Meteo first: no strict
    rate limit and it lets us insist on the right state. Returns None if not found."""
    name = market.replace("APMC", "").strip()
    name = name.split("(")[0].strip() or name
    try:
        for r in _om_search(name, count=10):
            if _ascii(r.get("admin1", "")).lower() == _ascii(state).lower():
                return r["latitude"], r["longitude"]
    except DataUnavailable:
        pass
    for q in (f"{name}, {district}, {state}, India", f"{name}, {state}, India"):
        try:
            payload, _ = _nominatim("search", {"q": q, "format": "jsonv2", "countrycodes": "in", "limit": 1})
        except DataUnavailable:
            return None
        if payload:
            return float(payload[0]["lat"]), float(payload[0]["lon"])
    return None
