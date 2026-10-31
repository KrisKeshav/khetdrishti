"""The single doorway to the outside world.

fetch_json() gives every external call the same behaviour:
  1. fresh cache hit (within ttl)          -> return it, labelled "cache"
  2. live request with retries             -> store + return, labelled "live"
  3. live failed but an older copy exists  -> return it, labelled "stale" (UI shows its age)
  4. nothing available                     -> raise DataUnavailable (callers must NOT invent a value)
"""
import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.db import cache_get, cache_put

log = logging.getLogger("khetdrishti.fetch")

class DataUnavailable(Exception):
    """Raised when neither the live source nor any cached copy can provide data."""

    def __init__(self, source: str, reason: str):
        super().__init__(f"{source} unavailable: {reason}")
        self.source = source
        self.reason = reason


@dataclass
class Provenance:
    source: str            # human-readable provider name, e.g. "Open-Meteo Forecast API"
    url: str               # endpoint (without secrets)
    fetched_at: str        # ISO-8601 UTC
    mode: str              # live | cache | stale
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


_client = httpx.Client(
    timeout=httpx.Timeout(20.0, connect=8.0),
    headers={"User-Agent": settings.contact_ua},
    follow_redirects=True,
)

SECRET_PARAMS = {"api-key", "api_key", "key", "client_secret", "token"}


def _cache_key(method: str, url: str, params: dict | list | None, body) -> str:
    items = params.items() if isinstance(params, dict) else (params or [])
    safe = sorted((k, str(v)) for k, v in items if k not in SECRET_PARAMS)
    raw = json.dumps([method, url, safe, body], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


# ---------------- resilience lab: simulated outages ----------------
# Lets us (and the judges) switch a provider off for a few minutes to watch the fallback chain work live.
# A simulated outage behaves like a provider that is down AND has no usable cache, so the next provider in the
# chain (or an honest "unavailable") is exercised. Expires automatically.
_outages: dict[str, float] = {}
OUTAGE_TARGETS = {
    "open-meteo": "Open-Meteo (forecast + ERA5 archive)",
    "planetary computer": "Microsoft Planetary Computer (satellite)",
    "data.gov.in": "data.gov.in (mandi prices)",
    "soilgrids": "ISRIC SoilGrids (soil)",
    "primary-llm": "Primary LLM model on Groq",
}


def simulate_outage(targets: list[str], minutes: float = 10) -> dict:
    until = time.time() + minutes * 60
    for t in targets:
        if t in OUTAGE_TARGETS:
            _outages[t] = until
    return active_outages()


def clear_outages() -> None:
    _outages.clear()


def active_outages() -> dict:
    now = time.time()
    return {k: int(v - now) for k, v in _outages.items() if v > now}


def is_simulated_down(name: str) -> bool:
    name = name.lower()
    return any(k in name for k in active_outages())


def fetch_json(source: str, url: str, *, params: dict | None = None, method: str = "GET",
               json_body=None, data=None, headers: dict | None = None, ttl: timedelta = timedelta(minutes=30),
               retries: int = 2, timeout: float | None = None, validate=None) -> tuple[object, Provenance]:
    """Fetch JSON with cache/retry/stale fallback. `validate(payload)` may raise ValueError to reject
    a syntactically-OK but semantically bad response (e.g. an error object with HTTP 200)."""
    if is_simulated_down(source):
        raise DataUnavailable(source, "simulated outage (resilience demo)")
    key = _cache_key(method, url, params, json_body or data)
    try:
        cached = cache_get(key)
    except Exception:
        log.exception("cache read failed for %s", source)
        cached = None
    now = datetime.now(timezone.utc)
    if cached and now - cached["fetched_at"] < ttl:
        return cached["payload"], Provenance(source, url, cached["fetched_at"].isoformat(), "cache")

    last_err = "unknown error"
    for attempt in range(retries + 1):
        try:
            resp = _client.request(method, url, params=params, json=json_body, data=data, headers=headers,
                                   timeout=timeout or _client.timeout)
            if resp.status_code == 429 or resp.status_code >= 500:
                raise httpx.HTTPStatusError(f"HTTP {resp.status_code}", request=resp.request, response=resp)
            resp.raise_for_status()
            payload = resp.json()
            if validate:
                validate(payload)
            try:
                ts = cache_put(key, source, payload)
            except Exception:  # a cache problem must never turn good live data into a failure
                log.exception("cache write failed for %s", source)
                ts = datetime.now(timezone.utc)
            return payload, Provenance(source, url, ts.isoformat(), "live")
        except httpx.HTTPStatusError as e:
            last_err = f"HTTP {e.response.status_code}"
            if e.response.status_code < 500 and e.response.status_code != 429:
                break  # client errors won't fix themselves on retry
        except (httpx.TransportError, ValueError) as e:
            last_err = f"{type(e).__name__}: {e}"[:200]
        if attempt < retries:
            time.sleep(0.6 * (2 ** attempt))

    if cached:
        age_h = (now - cached["fetched_at"]).total_seconds() / 3600
        return cached["payload"], Provenance(source, url, cached["fetched_at"].isoformat(), "stale",
                                             note=f"live call failed ({last_err}); showing data {age_h:.1f} h old")
    raise DataUnavailable(source, last_err)


def fetch_bytes(url: str, *, headers: dict | None = None, timeout: float = 30.0) -> bytes:
    resp = _client.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp.content


def http_client() -> httpx.Client:
    return _client
